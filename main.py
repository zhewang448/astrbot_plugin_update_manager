from __future__ import annotations

import asyncio
import inspect
import json
import traceback
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp
import astrbot.api.message_components as Comp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.version_comparator import VersionComparator

from .dashboard_client import DashboardClient
from .plugin_utils import (
    BoundedCache,
    apply_github_proxy,
    build_market_index,
    clean_version,
    extract_changelog_range,
    find_local_changelog,
    find_market_entry,
    is_valid_version,
    normalize_github_url_to_archive,
    normalize_name,
    normalize_repo,
    normalize_weekdays,
    parse_check_times,
    parse_custom_source_bindings,
    parse_plugin_metadata,
    parse_rate_limit_headers,
    truncate_text,
)


def _compatible_filter_hook(name: str):
    decorator = getattr(filter, name, None)
    return decorator() if callable(decorator) else (lambda func: func)


MARKET_URLS = (
    "https://api.soulter.top/astrbot/plugins",
    "https://cloud.astrbot.app/api/v1/market/plugins.json",
    "https://github.com/AstrBotDevs/AstrBot_Plugins_Collection/raw/refs/heads/main/plugin_cache_original.json",
)
PLUGIN_NAME = "astrbot_plugin_update_manager"
MAX_CHANGELOG_CHARS_PER_PLUGIN = 2000
MAX_TOTAL_CHANGELOG_CHARS = 6000


@dataclass
class UpdateCheckResult:
    status: str
    updates: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    not_found: list[dict[str, Any]] = field(default_factory=list)
    invalid_versions: list[dict[str, Any]] = field(default_factory=list)
    custom_source_errors: list[dict[str, Any]] = field(default_factory=list)
    market_fetch_failed: bool = False


@register(
    PLUGIN_NAME,
    "bushikq",
    "一个用于一键更新和管理所有 AstrBot 插件的工具，支持定时检查",
    "2.6.0",
)
class PluginUpdateManager(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.schedule_mode = self.config.get("schedule_mode", "interval")
        self.interval_hours = self.config.get("interval_hours", 24)
        self.check_weekdays = self.config.get(
            "check_weekdays", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        )
        self.check_times = self.config.get("check_times", ["04:00"])
        self.check_on_startup = self.config.get("check_on_startup", False)
        self.proxy_address = str(self.config.get("github_proxy", "") or "").strip()
        self.github_token = str(self.config.get("github_token", "") or "").strip()
        self.test_mode = self.config.get("test_mode", False)
        self.black_plugin_list = list(self.config.get("black_plugin_list", []) or [])
        self.white_plugin_list = list(self.config.get("white_plugin_list", []) or [])
        self.admin_sid_list = list(self.config.get("admin_sid_list", []) or [])
        self.restart_mode = self.config.get("restart_mode", False)
        self.custom_plugin_sources = list(
            self.config.get("custom_plugin_sources", []) or []
        )
        self.send_changelog_to_admin = self.config.get(
            "send_changelog_to_admin", False
        )
        self._http_cache = BoundedCache(max_entries=128)

        self.dashboard: DashboardClient | None = None
        self.scheduler: AsyncIOScheduler | None = None
        self._startup_task: asyncio.Task | None = None
        self._update_lock = asyncio.Lock()

        if self.proxy_address:
            logger.info(f"使用 GitHub 代理：{self.proxy_address}")

    async def initialize(self):
        self._refresh_plugin_list_schema()
        self._initialize_scheduler()

        if self.restart_mode:
            try:
                self.dashboard = DashboardClient(self.context)
                await self.dashboard.initialize()
                logger.info("插件更新管理器：重启模块已就绪")
            except Exception as exc:
                self.dashboard = None
                logger.error(f"插件更新管理器：重启模块初始化失败：{exc}")

        if self.schedule_mode == "calendar" and self.check_on_startup:
            self._startup_task = asyncio.create_task(
                self._scheduled_update_check(),
                name=f"{PLUGIN_NAME}:startup-check",
            )

    async def terminate(self):
        startup_task = self._startup_task
        self._startup_task = None
        if (
            startup_task
            and not startup_task.done()
            and startup_task is not asyncio.current_task()
        ):
            startup_task.cancel()
            with suppress(asyncio.CancelledError):
                await startup_task

        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("定时任务调度器已关闭。")

        if self.dashboard:
            await self.dashboard.terminate()
            logger.info("重启模块连接已断开。")

    def _initialize_scheduler(self) -> None:
        self.scheduler = AsyncIOScheduler()
        job_count = 0

        if self.schedule_mode == "calendar":
            weekdays, invalid_weekdays = normalize_weekdays(self.check_weekdays)
            check_times, invalid_times = parse_check_times(self.check_times)
            if invalid_weekdays:
                logger.warning(f"已忽略无效星期值：{invalid_weekdays}")
            if invalid_times:
                logger.warning(f"已忽略无效检查时间：{invalid_times}，请使用 HH:MM 格式。")

            if weekdays and check_times:
                day_of_week = ",".join(weekdays)
                for check_time in check_times:
                    hour, minute = (int(value) for value in check_time.split(":"))
                    self.scheduler.add_job(
                        self._scheduled_update_check,
                        "cron",
                        day_of_week=day_of_week,
                        hour=hour,
                        minute=minute,
                        id=f"calendar_plugin_update_{hour:02d}{minute:02d}",
                        name=f"Plugin Update Check {check_time}",
                        coalesce=True,
                        max_instances=1,
                        misfire_grace_time=60,
                    )
                    job_count += 1
                logger.info(
                    f"已启用定时方式 2：每周 {day_of_week}，在 {check_times} 检查更新。"
                )
            else:
                logger.warning("定时方式 2 未配置有效的星期和时间，本次不创建定时任务。")
        else:
            try:
                interval_hours = float(self.interval_hours)
            except (TypeError, ValueError):
                interval_hours = 0
                logger.warning(f"无效的检查间隔：{self.interval_hours}")
            if interval_hours > 0:
                self.scheduler.add_job(
                    self._scheduled_update_check,
                    "interval",
                    hours=interval_hours,
                    id="interval_plugin_update",
                    name="Interval Plugin Update Check",
                    coalesce=True,
                    max_instances=1,
                    misfire_grace_time=60,
                )
                job_count = 1
                logger.info(f"已启用定时方式 1：启动后每 {interval_hours:g} 小时检查一次。")
            else:
                logger.info("定时方式 1 的间隔为 0，未启用定时检查。")

        if job_count:
            self.scheduler.start()
            for job in self.scheduler.get_jobs():
                logger.info(f"定时任务 {job.id} 下一次执行时间：{job.next_run_time}")
        else:
            self.scheduler = None

    def _refresh_plugin_list_schema(self) -> None:
        schema = getattr(self.config, "schema", None)
        if not isinstance(schema, dict):
            return

        custom_selected: list[str] = []
        for item in self.custom_plugin_sources:
            if not isinstance(item, dict):
                continue
            value = item.get("plugin")
            if isinstance(value, list):
                custom_selected.extend(str(name) for name in value)
            else:
                custom_selected.append(str(value or ""))

        selected = {
            str(name)
            for name in (
                *self.black_plugin_list,
                *self.white_plugin_list,
                *custom_selected,
            )
            if str(name).strip()
        }
        labels_by_name: dict[str, str] = {}
        for plugin in self.context.get_all_stars():
            name = str(getattr(plugin, "name", "") or "").strip()
            if not name or getattr(plugin, "reserved", False):
                continue
            display_name = str(getattr(plugin, "display_name", "") or "").strip()
            author = str(getattr(plugin, "author", "") or "").strip()
            label = display_name if display_name and display_name != name else name
            if display_name and display_name != name:
                label = f"{display_name}（{name}）"
            if author:
                label += f" · {author}"
            labels_by_name[name] = label

        for name in selected:
            labels_by_name.setdefault(name, f"{name}（当前未加载）")

        options = sorted(labels_by_name, key=str.casefold)
        labels = [labels_by_name[name] for name in options]
        for key in ("white_plugin_list", "black_plugin_list"):
            item_schema = schema.get(key)
            if isinstance(item_schema, dict):
                item_schema["options"] = options
                item_schema["labels"] = labels

        custom_schema = schema.get("custom_plugin_sources")
        if not isinstance(custom_schema, dict):
            return
        templates = custom_schema.get("templates")
        if not isinstance(templates, dict):
            return
        github_template = templates.get("github_metadata")
        if not isinstance(github_template, dict):
            return
        template_items = github_template.get("items")
        if not isinstance(template_items, dict):
            return
        plugin_item = template_items.get("plugin")
        if isinstance(plugin_item, dict):
            plugin_item["options"] = options
            plugin_item["labels"] = labels

    @_compatible_filter_hook("on_plugin_loaded")
    async def on_plugin_loaded(self, metadata):
        self._refresh_plugin_list_schema()

    @_compatible_filter_hook("on_plugin_unloaded")
    async def on_plugin_unloaded(self, metadata):
        self._refresh_plugin_list_schema()

    async def _scheduled_update_check(self):
        if self._update_lock.locked():
            logger.warning("定时任务：已有插件更新检查正在执行，本次跳过。")
            return

        logger.info("定时任务：正在检查并更新插件...")
        final_message, need_to_restart = await self._check_and_perform_updates()
        await self.send_message_to_admin([Comp.Plain(text=final_message)])
        if need_to_restart:
            await self.restart_command()

    async def send_message_to_admin(self, msg_components):
        for admin in self.admin_sid_list:
            try:
                await self.context.send_message(admin, MessageChain(msg_components))
            except Exception as exc:
                logger.error(f"定时任务：发送给管理员 {admin} 消息失败：{exc}")

    async def restart_command(self, notify_admin: bool = True) -> str | None:
        try:
            if not self.dashboard:
                self.dashboard = DashboardClient(self.context)
                await self.dashboard.initialize()
            logger.info("准备执行重启...")
            await self.dashboard.restart()
            return None
        except Exception as exc:
            error_message = f"尝试重启失败：{exc}"
            logger.error(error_message)
            if notify_admin:
                await self.send_message_to_admin([Comp.Plain(text=error_message)])
            return error_message

    async def _check_and_perform_updates(self) -> tuple[str, bool]:
        if self._update_lock.locked():
            return "已有一次插件更新检查正在执行，请稍后再试。", False

        async with self._update_lock:
            try:
                check_result = await self.get_need_update_plugins_list()
                if check_result.status == "fetch_failed":
                    return "插件市场请求失败，请检查网络或代理设置后重试。", False

                notes = self._format_check_notes(check_result)
                if not check_result.updates:
                    message = "目前没有发现需要更新的插件。"
                    if notes:
                        message += f"\n\n{notes}"
                    logger.info(message)
                    return message, False

                update_method = self.context._star_manager.update_plugin
                try:
                    supports_download_url = "download_url" in inspect.signature(
                        update_method
                    ).parameters
                except (TypeError, ValueError):
                    supports_download_url = False

                succeeded_plugins: list[str] = []
                succeeded_update_info: list[dict[str, Any]] = []
                failed_plugins: list[str] = []
                error_messages: list[str] = []

                logger.info(
                    f"发现 {len(check_result.updates)} 个需要更新的插件："
                    f"{[plugin['name'] for plugin in check_result.updates]}。"
                )
                ordered_updates = sorted(
                    check_result.updates,
                    key=lambda plugin: plugin["name"] == PLUGIN_NAME,
                )
                for plugin in ordered_updates:
                    plugin_name = plugin["name"]
                    try:
                        if (
                            plugin.get("source_type") == "custom"
                            and not supports_download_url
                        ):
                            raise RuntimeError(
                                "当前 AstrBot 版本不支持按固定下载地址更新自定义源"
                            )
                        update_kwargs = {
                            "plugin_name": plugin_name,
                            "proxy": self.proxy_address,
                        }
                        if supports_download_url and plugin.get("download_url"):
                            update_kwargs["download_url"] = plugin["download_url"]
                        await update_method(**update_kwargs)
                        source_label = (
                            "自定义源"
                            if plugin.get("source_type") == "custom"
                            else "插件市场"
                        )
                        version_change = (
                            f"{plugin_name} ({plugin.get('version') or '?'} -> "
                            f"{plugin.get('online_version') or '?'}, {source_label})"
                        )
                        succeeded_plugins.append(version_change)
                        succeeded_update_info.append(plugin)
                        logger.info(f"插件更新成功：{version_change}")
                    except Exception as exc:
                        failed_plugins.append(plugin_name)
                        error_messages.append(f"更新插件 {plugin_name} 失败：{exc}")
                        logger.error(f"更新插件 {plugin_name} 失败：{traceback.format_exc()}")

                lines = [f"发现 {len(check_result.updates)} 个插件需要更新。"]
                if succeeded_plugins:
                    lines.append(
                        f"成功更新 {len(succeeded_plugins)} 个插件：\n"
                        + "\n".join(succeeded_plugins)
                    )
                if failed_plugins:
                    lines.append(
                        f"有 {len(failed_plugins)} 个插件更新失败：{failed_plugins}\n"
                        + "\n".join(error_messages)
                    )
                if notes:
                    lines.append(notes)

                need_to_restart = bool(succeeded_plugins and self.restart_mode)
                if need_to_restart:
                    lines.append("即将重启 AstrBot...")

                if (
                    self.send_changelog_to_admin
                    and succeeded_update_info
                    and self.admin_sid_list
                ):
                    asyncio.create_task(
                        self._build_and_send_changelogs(succeeded_update_info),
                        name=f"{PLUGIN_NAME}:send-changelogs",
                    )

                return "\n\n".join(lines), need_to_restart
            except Exception as exc:
                logger.error(f"插件更新流程异常：{traceback.format_exc()}")
                return f"插件更新流程异常终止：{exc}", False

    @staticmethod
    def _format_check_notes(result: UpdateCheckResult) -> str:
        lines: list[str] = []
        if result.ambiguous:
            details = "; ".join(
                f"{item['name']} -> {', '.join(item['candidates'])}"
                for item in result.ambiguous
            )
            lines.append(f"同名匹配存在歧义，已安全跳过：{details}")
        if result.not_found:
            lines.append(
                "未在插件市场找到："
                + ", ".join(item["name"] for item in result.not_found)
            )
        if result.invalid_versions:
            details = "; ".join(
                f"{item['name']} ({item['version']} -> {item['online_version']})"
                for item in result.invalid_versions
            )
            lines.append(f"版本号无法比较，已跳过：{details}")
        if result.custom_source_errors:
            details = "; ".join(
                f"{item.get('plugin') or '未命名绑定'} -> {item['error']}"
                for item in result.custom_source_errors
            )
            lines.append(f"自定义更新源检查失败，已跳过：{details}")
        if result.market_fetch_failed:
            lines.append("插件市场请求失败，市场插件本次未检查。")
        return "\n".join(lines)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("更新所有插件", alias={"updateallplugins", "更新全部插件"})
    async def update_all_plugins_command(self, event: AstrMessageEvent):
        logger.info("收到用户命令 '更新所有插件'。")
        if self._update_lock.locked():
            yield event.plain_result("已有一次插件更新检查正在执行，请稍后再试。")
            return

        yield event.plain_result("正在检查并更新所有插件，请稍候...")
        result_message, need_to_restart = await self._check_and_perform_updates()
        yield event.plain_result(result_message).use_t2i(False)
        if need_to_restart:
            await self.restart_command()

    async def _fetch_online_plugins(
        self, session: aiohttp.ClientSession
    ) -> object | None:
        for url in MARKET_URLS:
            proxied_url = apply_github_proxy(url, self.proxy_address)
            try:
                async with session.get(proxied_url) as response:
                    if response.status != 200:
                        logger.warning(
                            f"请求插件市场 {url} 失败，状态码：{response.status}"
                        )
                        continue
                    remote_data = await response.json(content_type=None)
                    if isinstance(remote_data, (dict, list)) and remote_data:
                        logger.info(f"成功从 {url} 获取插件市场数据。")
                        return remote_data
                    logger.warning(f"插件市场 {url} 返回了空数据或未知格式。")
            except Exception as exc:
                logger.warning(f"请求插件市场 {url} 失败：{exc}")
        logger.error("所有插件市场地址均请求失败。")
        return None

    def _github_api_headers(
        self, accept: str = "application/vnd.github+json"
    ) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrbot-plugin-update-manager",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    async def _fetch_text_cached(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        accept: str = "application/vnd.github+json",
    ) -> str:
        headers = self._github_api_headers(accept)
        cached = self._http_cache.get(url)
        if cached and cached.get("etag"):
            headers["If-None-Match"] = str(cached["etag"])

        async with session.get(url, headers=headers) as response:
            if response.status == 304 and cached:
                return str(cached["value"])
            if response.status != 200:
                rate_limit_hint = parse_rate_limit_headers(response.headers)
                body = (await response.text())[:200].strip()
                detail = rate_limit_hint if rate_limit_hint else body
                raise RuntimeError(f"GitHub 请求失败（{response.status}）：{detail}")
            value = await response.text()
            self._http_cache.set(url, {
                "etag": response.headers.get("ETag", ""),
                "value": value,
            })
            return value

    async def _fetch_json_cached(
        self, session: aiohttp.ClientSession, url: str
    ) -> dict[str, Any]:
        text = await self._fetch_text_cached(session, url)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("GitHub 返回了无法解析的 JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("GitHub 返回了未知数据格式")
        return value

    async def _fetch_custom_source(
        self,
        session: aiohttp.ClientSession,
        binding,
    ) -> dict[str, str]:
        repo_api = (
            f"https://api.github.com/repos/{binding.owner}/{binding.repo}"
        )
        target_ref = binding.branch
        if not target_ref:
            repo_info = await self._fetch_json_cached(session, repo_api)
            target_ref = str(repo_info.get("default_branch") or "").strip()
        if not target_ref:
            raise RuntimeError("仓库没有可用的默认分支")

        commit_api = f"{repo_api}/commits/{quote(target_ref, safe='')}"
        commit_info = await self._fetch_json_cached(session, commit_api)
        commit_sha = str(commit_info.get("sha") or "").strip()
        if len(commit_sha) != 40:
            raise RuntimeError("GitHub 未返回有效的提交 SHA")

        metadata = None
        metadata_error = None
        for filename in ("metadata.yaml", "metadata.yml"):
            metadata_api = (
                f"{repo_api}/contents/{quote(filename, safe='')}?"
                f"ref={quote(commit_sha, safe='')}"
            )
            try:
                metadata_text = await self._fetch_text_cached(
                    session,
                    metadata_api,
                    accept="application/vnd.github.raw+json",
                )
                metadata = parse_plugin_metadata(metadata_text)
                break
            except Exception as exc:
                metadata_error = exc
        if not metadata:
            raise RuntimeError(
                f"无法读取 metadata.yaml 或 metadata.yml：{metadata_error}"
            )

        download_url = apply_github_proxy(
            f"https://github.com/{binding.owner}/{binding.repo}/archive/"
            f"{commit_sha}.zip",
            self.proxy_address,
        )
        return {
            **metadata,
            "ref": target_ref,
            "commit_sha": commit_sha,
            "repo_url": binding.repo_url,
            "download_url": download_url,
        }

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重启astrbot")
    async def restart_astrbot_command(self, event: AstrMessageEvent):
        logger.info("收到用户命令 '重启astrbot'。")
        yield event.plain_result("正在重启，请稍候...")
        error_message = await self.restart_command(notify_admin=False)
        if error_message:
            yield event.plain_result(error_message)

    @staticmethod
    def _append_version_update(
        result: UpdateCheckResult,
        plugin: dict[str, Any],
        online_version: str,
        **source_fields: Any,
    ) -> None:
        local_version = clean_version(plugin["version"])
        remote_version = clean_version(online_version)
        if not is_valid_version(local_version) or not is_valid_version(remote_version):
            result.invalid_versions.append(
                {
                    "name": plugin["name"],
                    "version": plugin["version"],
                    "online_version": online_version,
                }
            )
            return

        try:
            is_updatable = (
                VersionComparator.compare_version(local_version, remote_version) == -1
            )
        except Exception as exc:
            logger.error(f"比较插件 {plugin['name']} 的版本时出错：{exc}")
            result.invalid_versions.append(
                {
                    "name": plugin["name"],
                    "version": plugin["version"],
                    "online_version": online_version,
                }
            )
            return

        if is_updatable:
            result.updates.append(
                {
                    **plugin,
                    "online_version": online_version,
                    **source_fields,
                }
            )

    async def get_need_update_plugins_list(self) -> UpdateCheckResult:
        # 黑白名单转为归一化集合，避免大小写或连字符差异导致静默失效。
        black_set = {normalize_name(n) for n in self.black_plugin_list if n}
        white_set = {normalize_name(n) for n in self.white_plugin_list if n}

        local_plugins: list[dict[str, Any]] = []
        for plugin in self.context.get_all_stars():
            name = str(getattr(plugin, "name", "") or "").strip()
            if not name or getattr(plugin, "reserved", False):
                continue
            if normalize_name(name) in black_set:
                continue
            if white_set and normalize_name(name) not in white_set:
                continue
            local_plugins.append(
                {
                    "name": name,
                    "version": str(getattr(plugin, "version", "") or "").strip(),
                    "author": str(getattr(plugin, "author", "") or "").strip(),
                    "repo": str(getattr(plugin, "repo", "") or "").strip(),
                    "root_dir_name": str(
                        getattr(plugin, "root_dir_name", "") or ""
                    ).strip(),
                }
            )

        bindings, claimed_plugins, config_errors = parse_custom_source_bindings(
            self.custom_plugin_sources
        )
        result = UpdateCheckResult(
            status="ok", custom_source_errors=list(config_errors)
        )
        local_by_name = {plugin["name"]: plugin for plugin in local_plugins}
        for plugin_name in sorted(claimed_plugins):
            if plugin_name not in local_by_name:
                result.custom_source_errors.append(
                    {
                        "plugin": plugin_name,
                        "error": "绑定的插件当前未加载，已跳过",
                    }
                )

        market_plugins = [
            plugin for plugin in local_plugins if plugin["name"] not in claimed_plugins
        ]
        market_data: object | None = None
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            # 并发检查所有自定义源，最多同时发起 5 个请求。
            if bindings:
                semaphore = asyncio.Semaphore(5)

                async def _fetch_one(plugin_name: str, binding) -> None:
                    plugin = local_by_name.get(plugin_name)
                    if not plugin:
                        return
                    async with semaphore:
                        try:
                            remote = await self._fetch_custom_source(session, binding)
                            if normalize_name(remote["name"]) != normalize_name(plugin_name):
                                raise RuntimeError(
                                    f"远端插件名 {remote['name']} 与本地插件名不一致"
                                )
                            remote_repo = normalize_repo(remote.get("repo"))
                            if remote_repo and remote_repo != binding.repo_id.lower():
                                raise RuntimeError(
                                    f"metadata 中的仓库 {remote['repo']} 与绑定仓库不一致"
                                )
                            self._append_version_update(
                                result,
                                plugin,
                                remote["version"],
                                download_url=remote["download_url"],
                                source_type="custom",
                                source_repo=remote["repo_url"],
                                source_ref=remote["ref"],
                                commit_sha=remote["commit_sha"],
                                matched_by="custom_binding",
                            )
                        except Exception as exc:
                            result.custom_source_errors.append(
                                {"plugin": plugin_name, "error": str(exc)}
                            )
                            logger.warning(
                                f"插件 {plugin_name} 的自定义更新源检查失败，已跳过：{exc}"
                            )

                await asyncio.gather(
                    *[_fetch_one(n, b) for n, b in bindings.items()]
                )

            if market_plugins:
                market_data = await self._fetch_online_plugins(session)
                if market_data is None:
                    result.market_fetch_failed = True
                else:
                    market_index = build_market_index(market_data)
                    for plugin in market_plugins:
                        match = find_market_entry(plugin, market_index)
                        if match.status == "ambiguous":
                            result.ambiguous.append(
                                {"name": plugin["name"], "candidates": match.candidates}
                            )
                            logger.warning(
                                f"插件 {plugin['name']} 匹配到多个市场条目，已跳过："
                                f"{match.candidates}"
                            )
                            continue
                        if match.status == "not_found" or not match.entry:
                            result.not_found.append(plugin)
                            logger.warning(
                                f"插件 {plugin['name']} 不在在线插件市场中。"
                            )
                            continue

                        online_version = str(
                            match.entry.get("version") or ""
                        ).strip()
                        self._append_version_update(
                            result,
                            plugin,
                            online_version,
                            download_url=str(
                                match.entry.get("download_url") or ""
                            ).strip(),
                            source_type="market",
                            market_id=match.entry.get("_market_id", ""),
                            matched_by=match.matched_by,
                        )

        if result.market_fetch_failed and not bindings:
            result.status = "fetch_failed"
        elif result.market_fetch_failed or result.custom_source_errors:
            result.status = "partial"
        self._write_debug_data(local_plugins, market_data, result)
        return result

    def _write_debug_data(
        self,
        local_plugins: list[dict[str, Any]],
        market_data: object,
        result: UpdateCheckResult,
    ) -> None:
        if not self.test_mode:
            return
        debug_path = Path(__file__).resolve().parent / "test.md"
        try:
            with debug_path.open("w", encoding="utf-8") as file:
                file.write(f"于 {datetime.now().isoformat()} 记录\n\n")
                file.write("## 本地插件\n\n")
                file.write(json.dumps(local_plugins, ensure_ascii=False, indent=2))
                file.write("\n\n## 市场数据\n\n")
                file.write(json.dumps(market_data, ensure_ascii=False, indent=2))
                file.write("\n\n## 检查结果\n\n")
                file.write(json.dumps(asdict(result), ensure_ascii=False, indent=2))
                file.write("\n")
            logger.info(f"调试模式：已生成 {debug_path.name}。")
        except Exception as exc:
            logger.error(f"写入调试文件失败：{exc}")

    async def _build_and_send_changelogs(
        self, succeeded_updates: list[dict[str, Any]]
    ) -> None:
        """更新成功后读取各插件本地 CHANGELOG，以合并转发形式发送给管理员。

        在后台任务中运行，不阻塞更新结果的返回。任何单个插件的失败都只
        记录警告，不影响其他插件的日志发送。
        """
        plugin_dir_base = Path(__file__).resolve().parent.parent
        node_texts: list[str] = []

        for info in succeeded_updates:
            plugin_name = info.get("name", "")
            root_dir = info.get("root_dir_name", "").strip()
            old_version = info.get("version", "")
            new_version = info.get("online_version", "")

            if not root_dir:
                continue

            changelog_path = find_local_changelog(plugin_dir_base / root_dir)
            if not changelog_path:
                continue

            try:
                raw_text = await asyncio.to_thread(
                    changelog_path.read_text, encoding="utf-8", errors="replace"
                )
                changelog_text = extract_changelog_range(raw_text, old_version, new_version)
                changelog_text = truncate_text(
                    changelog_text, MAX_CHANGELOG_CHARS_PER_PLUGIN
                )
            except Exception as exc:
                logger.warning(f"读取插件 {plugin_name} 的 CHANGELOG 失败：{exc}")
                continue

            if not changelog_text:
                continue

            header = f"{plugin_name}  {old_version} → {new_version}"
            node_texts.append(f"{header}\n\n{changelog_text}")

        if not node_texts:
            return

        await self._try_send_changelog_forward(node_texts)

    async def _try_send_changelog_forward(self, node_texts: list[str]) -> None:
        """尝试以合并转发发送更新日志；平台不支持时降级为长文本。"""
        NodeCls = getattr(Comp, "Node", None)
        NodesCls = getattr(Comp, "Nodes", None)

        if NodeCls and NodesCls:
            try:
                nodes = [
                    NodeCls(
                        uin="0",
                        name="插件更新管理器",
                        content=[Comp.Plain(text=text)],
                    )
                    for text in node_texts
                ]
                await self.send_message_to_admin([NodesCls(nodes=nodes)])
                return
            except Exception as exc:
                logger.warning(f"发送合并转发失败，降级为长文本：{exc}")

        # 降级：拼接成一条长文本，超出限制时截断。
        separator = "\n\n" + "─" * 20 + "\n\n"
        combined = separator.join(node_texts)
        combined = truncate_text(combined, MAX_TOTAL_CHANGELOG_CHARS)
        await self.send_message_to_admin(
            [Comp.Plain(text=f"本次更新日志：\n\n{combined}")]
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("检查插件更新", alias={"checkpluginupdates"})
    async def check_plugins_command(self, event: AstrMessageEvent):
        """只检查有无可用更新，不执行更新操作。"""
        logger.info("收到用户命令 '检查插件更新'。")
        if self._update_lock.locked():
            yield event.plain_result("已有一次插件更新检查正在执行，请稍后再试。")
            return

        yield event.plain_result("正在检查插件更新，请稍候...")
        async with self._update_lock:
            try:
                check_result = await self.get_need_update_plugins_list()
            except Exception as exc:
                yield event.plain_result(f"检查失败：{exc}")
                return

        if check_result.status == "fetch_failed":
            yield event.plain_result("插件市场请求失败，请检查网络或代理设置后重试。")
            return

        notes = self._format_check_notes(check_result)
        if not check_result.updates:
            message = "目前没有发现需要更新的插件。"
            if notes:
                message += f"\n\n{notes}"
            yield event.plain_result(message)
            return

        lines = [f"发现 {len(check_result.updates)} 个可更新插件："]
        for plugin in check_result.updates:
            source_label = (
                "自定义源" if plugin.get("source_type") == "custom" else "插件市场"
            )
            lines.append(
                f"• {plugin['name']}  "
                f"{plugin.get('version') or '?'} → {plugin.get('online_version') or '?'}"
                f"  [{source_label}]"
            )
        if notes:
            lines.append(f"\n{notes}")
        yield event.plain_result("\n".join(lines)).use_t2i(False)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("安装插件", alias={"installplugin"})
    async def install_plugin_command(self, event: AstrMessageEvent):
        """通过 AstrBot 原生插件管理器安装指定仓库。"""
        parts = str(getattr(event, "message_str", "") or "").strip().split(
            maxsplit=1
        )
        if len(parts) < 2 or not parts[1].strip():
            yield event.plain_result(
                "用法：安装插件 <插件仓库链接>\n"
                "例如：安装插件 https://github.com/owner/repo\n"
                "也支持 AstrBot 原生接受的 Git/仓库链接格式。"
            )
            return

        repo_url = parts[1].strip()
        if any(character.isspace() for character in repo_url):
            yield event.plain_result("插件仓库链接不能包含空白字符。")
            return

        if self._update_lock.locked():
            yield event.plain_result("已有一次插件更新或安装正在执行，请稍后再试。")
            return

        yield event.plain_result("正在安装插件，请稍候...")
        async with self._update_lock:
            try:
                install_method = self.context._star_manager.install_plugin
                install_kwargs = {"repo_url": repo_url}
                try:
                    supports_proxy = "proxy" in inspect.signature(
                        install_method
                    ).parameters
                except (TypeError, ValueError):
                    supports_proxy = False
                if supports_proxy and self.proxy_address:
                    install_kwargs["proxy"] = self.proxy_address

                plugin_info = await install_method(**install_kwargs)
                installed_name = (
                    plugin_info.get("name")
                    if isinstance(plugin_info, dict)
                    else None
                )
                suffix = f"：{installed_name}" if installed_name else ""
                yield event.plain_result(
                    f"插件安装成功{suffix}。AstrBot 已完成下载、校验、依赖处理并加载。"
                ).use_t2i(False)
            except Exception as exc:
                logger.error(f"安装插件失败：{traceback.format_exc()}")
                yield event.plain_result(f"插件安装失败：{exc}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("清除插件数据", alias={"clearplugindata"})
    async def clear_plugin_data_command(self, event: AstrMessageEvent):
        """清除指定插件的 AstrBot 持久化数据，并重新加载插件。"""
        parts = str(getattr(event, "message_str", "") or "").strip().split(
            maxsplit=2
        )
        if len(parts) < 2 or not parts[1].strip():
            yield event.plain_result(
                "用法：清除插件数据 <插件名> --confirm\n"
                "该操作会删除 AstrBot 管理的插件文件数据和 KV 数据，但保留用户配置文件。"
            )
            return

        target_name = parts[1].strip()
        confirmed = len(parts) == 3 and parts[2].strip() == "--confirm"
        if not confirmed:
            yield event.plain_result(
                f"危险操作警告：将清除插件「{target_name}」的 AstrBot 持久化文件数据和 KV 数据。\n"
                "AstrBot 用户配置文件不会删除，但插件数据目录和 KV 中可能含有用户录入内容，清除后不可恢复。\n"
                "框架未管理的其他路径不会处理。\n"
                f"确认操作请发送：清除插件数据 {target_name} --confirm"
            )
            return

        target = None
        get_registered_star = getattr(self.context, "get_registered_star", None)
        if callable(get_registered_star):
            target = get_registered_star(target_name)
        if target is None:
            candidates = [
                plugin
                for plugin in self.context.get_all_stars()
                if str(getattr(plugin, "root_dir_name", "") or "").strip()
                == target_name
            ]
            if len(candidates) == 1:
                target = candidates[0]

        if target is None:
            yield event.plain_result(f"未找到已加载的插件：{target_name}")
            return

        plugin_name = str(getattr(target, "name", "") or "").strip()
        root_dir_name = str(getattr(target, "root_dir_name", "") or "").strip()
        if not plugin_name or not root_dir_name:
            yield event.plain_result("目标插件信息不完整，未执行任何删除操作。")
            return
        if normalize_name(plugin_name) == normalize_name(PLUGIN_NAME):
            yield event.plain_result("不能清除本插件自身的数据，未执行任何删除操作。")
            return
        if (
            root_dir_name in {".", ".."}
            or any(char in root_dir_name for char in ("/", "\\", ":"))
        ):
            yield event.plain_result("目标插件目录名不安全，未执行任何删除操作。")
            return
        if bool(getattr(target, "reserved", False)):
            yield event.plain_result("不能清除 AstrBot 保留插件的数据，未执行任何删除操作。")
            return

        manager = getattr(self.context, "_star_manager", None)
        cleanup_method = getattr(manager, "_cleanup_plugin_optional_artifacts", None)
        reload_method = getattr(manager, "reload", None)
        required_cleanup_params = {
            "root_dir_name",
            "plugin_label",
            "plugin_id",
            "delete_config",
            "delete_data",
        }
        try:
            cleanup_params = set(inspect.signature(cleanup_method).parameters)
            reload_params = set(inspect.signature(reload_method).parameters)
        except (TypeError, ValueError, AttributeError):
            cleanup_params = set()
            reload_params = set()
        if (
            not callable(cleanup_method)
            or not callable(reload_method)
            or not required_cleanup_params.issubset(cleanup_params)
            or "specified_plugin_name" not in reload_params
        ):
            yield event.plain_result(
                "当前 AstrBot 版本没有可验证的数据清理或插件重载接口，未执行任何删除操作。"
            )
            return

        if self._update_lock.locked():
            yield event.plain_result("已有一次插件更新、安装或数据清理正在执行，请稍后再试。")
            return

        yield event.plain_result(
            f"已确认，正在清除插件「{plugin_name}」的数据并重载插件，请稍候..."
        )
        async with self._update_lock:
            try:
                await cleanup_method(
                    root_dir_name=root_dir_name,
                    plugin_label=plugin_name,
                    plugin_id=str(getattr(target, "plugin_id", "") or "") or None,
                    delete_config=False,
                    delete_data=True,
                )
                reload_result = await reload_method(specified_plugin_name=plugin_name)
                reload_ok = (
                    bool(reload_result[0])
                    if isinstance(reload_result, tuple) and reload_result
                    else reload_result is not False
                )
                if not reload_ok:
                    detail = (
                        reload_result[1]
                        if isinstance(reload_result, tuple) and len(reload_result) > 1
                        else "未知错误"
                    )
                    yield event.plain_result(
                        f"插件「{plugin_name}」的数据清理已执行，但重载失败：{detail}\n"
                        "用户配置文件未删除，请手动检查插件状态。"
                    )
                    return
                yield event.plain_result(
                    f"插件「{plugin_name}」的 AstrBot 持久化数据清理已执行，用户配置文件未删除，插件已重载。"
                ).use_t2i(False)
            except Exception as exc:
                logger.error(f"清除插件 {plugin_name} 数据失败：{traceback.format_exc()}")
                yield event.plain_result(
                    f"清除插件「{plugin_name}」数据或重载失败：{exc}\n"
                    "未执行配置文件删除，请手动检查插件状态。"
                )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重新安装插件", alias={"reinstallplugin"})
    async def reinstall_plugin_command(self, event: AstrMessageEvent):
        """强制重新下载并安装指定插件，不进行版本比较。"""
        message_text = str(getattr(event, "message_str", "") or "").strip()
        command_body = message_text.removeprefix("/").lstrip()
        for command_name in ("重新安装插件", "reinstallplugin"):
            if command_body.startswith(command_name):
                command_body = command_body[len(command_name) :].strip()
                break

        arguments = command_body.split(maxsplit=2)
        link_only = bool(arguments) and arguments[0].startswith(
            ("http://", "https://", "github.com/", "www.github.com/")
        )
        if link_only:
            custom_url = arguments[0]
            use_proxy = not any(argument == "--no-proxy" for argument in arguments[1:])
            manager = getattr(self.context, "_star_manager", None)
            inspect_method = getattr(manager, "inspect_plugin_repository", None)
            if not callable(inspect_method):
                yield event.plain_result(
                    "当前 AstrBot 版本不支持从仓库链接读取插件信息，无法使用仅链接格式。"
                )
                return
            try:
                inspect_kwargs = {"repo_url": custom_url}
                if "proxy" in inspect.signature(inspect_method).parameters:
                    inspect_kwargs["proxy"] = self.proxy_address if use_proxy else ""
                remote_plugin = await inspect_method(**inspect_kwargs)
            except Exception as exc:
                yield event.plain_result(f"读取插件仓库信息失败：{exc}")
                return
            target_name = (
                str(remote_plugin.get("name") or "").strip()
                if isinstance(remote_plugin, dict)
                else ""
            )
            if not target_name:
                yield event.plain_result(
                    "插件仓库缺少有效的 metadata.name，未执行重装。"
                )
                return
        else:
            parts = message_text.split(maxsplit=3)
            target_name = parts[1].strip() if len(parts) > 1 else ""
            custom_url = (
                parts[2].strip()
                if len(parts) > 2 and not parts[2].startswith("--")
                else ""
            )
            use_proxy = not any(part == "--no-proxy" for part in parts[2:])

        if not target_name:
            yield event.plain_result(
                "用法：重新安装插件 <插件名> [GitHub地址或下载URL] [--no-proxy]\n"
                "或：重新安装插件<GitHub仓库链接> [--no-proxy]\n"
                "例如：\n"
                "  重新安装插件 astrbot_plugin_demo\n"
                "  重新安装插件 astrbot_plugin_demo https://github.com/owner/repo\n"
                "  重新安装插件 astrbot_plugin_demo https://github.com/owner/repo/tree/dev\n"
                "  重新安装插件 astrbot_plugin_demo https://github.com/owner/repo --no-proxy\n"
                "  重新安装插件https://github.com/owner/repo/tree/dev\n"
                "\n"
                "不进行版本比较，直接重新下载覆盖安装。\n"
                "仅链接格式会读取仓库 metadata.name 定位本地插件；第二个参数可以是 GitHub 仓库地址或直接下载地址（.zip）。\n"
                "添加 --no-proxy 禁用 github_proxy 加速（默认启用）。"
            )
            return

        logger.info(
            f"收到用户命令 '重新安装插件 {target_name}'"
            + (f"，指定地址：{custom_url}" if custom_url else "")
            + f"，代理：{'启用' if use_proxy else '禁用'}"
        )

        # 在已加载插件中查找，支持大小写不敏感匹配
        local_plugin = None
        for plugin in self.context.get_all_stars():
            name = str(getattr(plugin, "name", "") or "").strip()
            if name == target_name or normalize_name(name) == normalize_name(target_name):
                local_plugin = {
                    "name": name,
                    "version": str(getattr(plugin, "version", "") or "").strip(),
                    "author": str(getattr(plugin, "author", "") or "").strip(),
                    "repo": str(getattr(plugin, "repo", "") or "").strip(),
                    "root_dir_name": str(
                        getattr(plugin, "root_dir_name", "") or ""
                    ).strip(),
                }
                break

        if not local_plugin:
            yield event.plain_result(f"未找到已加载的插件：{target_name}")
            return

        plugin_name = local_plugin["name"]
        yield event.plain_result(f"正在重新安装插件 {plugin_name}，请稍候...")

        update_method = self.context._star_manager.update_plugin
        try:
            supports_download_url = "download_url" in inspect.signature(
                update_method
            ).parameters
        except (TypeError, ValueError):
            supports_download_url = False

        download_url = ""
        source_label = "仓库地址"
        normalized_result = None

        # 如果用户指定了下载地址，先尝试标准化
        if custom_url:
            # 尝试解析为 GitHub 仓库 URL
            normalized_result = normalize_github_url_to_archive(custom_url)
            if normalized_result:
                owner, repo, archive_url = normalized_result
                # 检查是否需要获取默认分支（用户只给了 owner/repo）
                if "/tree/" not in custom_url and not custom_url.endswith(".zip"):
                    # 用户给的是裸仓库地址，需要查默认分支
                    try:
                        timeout = aiohttp.ClientTimeout(total=15)
                        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
                            repo_api = f"https://api.github.com/repos/{owner}/{repo}"
                            repo_info = await self._fetch_json_cached(session, repo_api)
                            default_branch = str(repo_info.get("default_branch") or "main").strip()
                            # 重新生成带默认分支的归档地址
                            archive_url = f"https://github.com/{owner}/{repo}/archive/{quote(default_branch, safe='')}.zip"
                            source_label = f"GitHub {owner}/{repo}@{default_branch}"
                    except Exception as exc:
                        logger.warning(f"获取默认分支失败，使用 main：{exc}")
                        archive_url = f"https://github.com/{owner}/{repo}/archive/main.zip"
                        source_label = f"GitHub {owner}/{repo}@main（兜底）"
                else:
                    # 用户明确指定了分支或已是 .zip
                    source_label = f"用户指定（{custom_url[:60]}...）" if len(custom_url) > 60 else "用户指定"

                download_url = archive_url
            else:
                # 不是 GitHub 地址，当作直接下载 URL
                parsed = urlparse(custom_url)
                if parsed.scheme not in ("http", "https"):
                    yield event.plain_result(
                        f"下载地址格式错误：{custom_url}\n"
                        "必须是 http:// 或 https:// 开头的完整 URL，或 GitHub 仓库地址。"
                    )
                    return
                download_url = custom_url
                source_label = f"直接下载（{custom_url[:50]}...）" if len(custom_url) > 50 else "直接下载"
        else:
            # 否则自动获取下载地址
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
                # 优先检查自定义绑定
                bindings, _, _ = parse_custom_source_bindings(self.custom_plugin_sources)
                if plugin_name in bindings:
                    try:
                        remote = await self._fetch_custom_source(session, bindings[plugin_name])
                        download_url = remote.get("download_url", "")
                        source_label = "自定义源"
                    except Exception as exc:
                        logger.warning(f"获取插件 {plugin_name} 自定义源失败：{exc}")
                        yield event.plain_result(
                            f"获取自定义源下载地址失败：{exc}\n将尝试使用仓库地址重新安装。"
                        )

                # 未找到自定义源或获取失败时，尝试插件市场
                if not download_url:
                    market_data = await self._fetch_online_plugins(session)
                    if market_data:
                        market_index = build_market_index(market_data)
                        match = find_market_entry(local_plugin, market_index)
                        if match.status == "matched" and match.entry:
                            download_url = str(match.entry.get("download_url") or "").strip()
                            source_label = "插件市场"

        # 执行重新安装（覆盖式）
        try:
            proxy_to_use = self.proxy_address if use_proxy else ""
            update_kwargs = {"plugin_name": plugin_name, "proxy": proxy_to_use}
            if supports_download_url and download_url:
                # AstrBot Core 对 download_url 直接下载，不会再套用 proxy；
                # 在交给 Core 前处理 GitHub 直链，避免绕过配置的加速地址。
                update_kwargs["download_url"] = apply_github_proxy(
                    download_url,
                    proxy_to_use,
                )
            elif custom_url and not supports_download_url:
                yield event.plain_result(
                    f"当前 AstrBot 版本不支持指定下载地址。\n"
                    f"请升级 AstrBot 或使用不带 URL 参数的重新安装命令。"
                )
                return

            async with self._update_lock:
                await update_method(**update_kwargs)
            proxy_status = "已启用代理加速" if use_proxy and self.proxy_address else "未使用代理"
            yield event.plain_result(
                f"插件 {plugin_name} 重新安装成功（覆盖式）。\n"
                f"来源：{source_label}\n"
                f"代理：{proxy_status}"
            ).use_t2i(False)
        except Exception as exc:
            logger.error(f"重新安装插件 {plugin_name} 失败：{traceback.format_exc()}")
            yield event.plain_result(f"重新安装插件 {plugin_name} 失败：{exc}")

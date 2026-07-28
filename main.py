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
    build_market_index,
    clean_version,
    find_market_entry,
    is_valid_version,
    normalize_weekdays,
    parse_check_times,
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


@dataclass
class UpdateCheckResult:
    status: str
    updates: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    not_found: list[dict[str, Any]] = field(default_factory=list)
    invalid_versions: list[dict[str, Any]] = field(default_factory=list)


@register(
    PLUGIN_NAME,
    "bushikq",
    "一个用于一键更新和管理所有 AstrBot 插件的工具，支持定时检查",
    "2.4.0",
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
        self.test_mode = self.config.get("test_mode", False)
        self.black_plugin_list = list(self.config.get("black_plugin_list", []) or [])
        self.white_plugin_list = list(self.config.get("white_plugin_list", []) or [])
        self.admin_sid_list = list(self.config.get("admin_sid_list", []) or [])
        self.restart_mode = self.config.get("restart_mode", False)

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

        selected = {
            str(name)
            for name in (*self.black_plugin_list, *self.white_plugin_list)
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
                        update_kwargs = {
                            "plugin_name": plugin_name,
                            "proxy": self.proxy_address,
                        }
                        if supports_download_url and plugin.get("download_url"):
                            update_kwargs["download_url"] = plugin["download_url"]
                        await update_method(**update_kwargs)
                        version_change = (
                            f"{plugin_name} ({plugin.get('version') or '?'} -> "
                            f"{plugin.get('online_version') or '?'})"
                        )
                        succeeded_plugins.append(version_change)
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
            try:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.warning(f"请求插件市场 {url} 失败，状态码：{response.status}")
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

<<<<<<< Updated upstream
=======
    @staticmethod
    def _github_api_headers() -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrbot-plugin-update-manager",
        }

    async def _fetch_text_cached(
        self, session: aiohttp.ClientSession, url: str
    ) -> str:
        headers = self._github_api_headers()
        cached = self._http_cache.get(url)
        if cached and cached.get("etag"):
            headers["If-None-Match"] = str(cached["etag"])

        async with session.get(url, headers=headers) as response:
            if response.status == 304 and cached:
                return str(cached["value"])
            if response.status != 200:
                message = (await response.text())[:200].strip()
                raise RuntimeError(f"GitHub 请求失败（{response.status}）：{message}")
            value = await response.text()
            self._http_cache[url] = {
                "etag": response.headers.get("ETag", ""),
                "value": value,
            }
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
            raw_url = (
                f"https://raw.githubusercontent.com/{binding.owner}/"
                f"{binding.repo}/{commit_sha}/{filename}"
            )
            try:
                metadata_text = await self._fetch_text_cached(session, raw_url)
                metadata = parse_plugin_metadata(metadata_text)
                break
            except Exception as exc:
                metadata_error = exc
        if not metadata:
            raise RuntimeError(
                f"无法读取 metadata.yaml 或 metadata.yml：{metadata_error}"
            )

        return {
            **metadata,
            "ref": target_ref,
            "commit_sha": commit_sha,
            "repo_url": binding.repo_url,
            "download_url": (
                f"https://github.com/{binding.owner}/{binding.repo}/archive/"
                f"{commit_sha}.zip"
            ),
        }

>>>>>>> Stashed changes
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重启astrbot")
    async def restart_astrbot_command(self, event: AstrMessageEvent):
        logger.info("收到用户命令 '重启astrbot'。")
        yield event.plain_result("正在重启，请稍候...")
        error_message = await self.restart_command(notify_admin=False)
        if error_message:
            yield event.plain_result(error_message)

    async def get_need_update_plugins_list(self) -> UpdateCheckResult:
        local_plugins: list[dict[str, Any]] = []
        for plugin in self.context.get_all_stars():
            name = str(getattr(plugin, "name", "") or "").strip()
            if not name or getattr(plugin, "reserved", False):
                continue
            if name in self.black_plugin_list:
                continue
            if self.white_plugin_list and name not in self.white_plugin_list:
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

        market_data = await self._fetch_online_plugins()
        if market_data is None:
            result = UpdateCheckResult(status="fetch_failed")
            self._write_debug_data(local_plugins, market_data, result)
            return result

        market_index = build_market_index(market_data)
        result = UpdateCheckResult(status="ok")
        for plugin in local_plugins:
            match = find_market_entry(plugin, market_index)
            if match.status == "ambiguous":
                result.ambiguous.append(
                    {"name": plugin["name"], "candidates": match.candidates}
                )
                logger.warning(
                    f"插件 {plugin['name']} 匹配到多个市场条目，已跳过：{match.candidates}"
                )
                continue
            if match.status == "not_found" or not match.entry:
                result.not_found.append(plugin)
                logger.warning(f"插件 {plugin['name']} 不在在线插件市场中。")
                continue

            online_version = str(match.entry.get("version") or "").strip()
            local_version_for_compare = clean_version(plugin["version"])
            online_version_for_compare = clean_version(online_version)
            if not is_valid_version(local_version_for_compare) or not is_valid_version(
                online_version_for_compare
            ):
                result.invalid_versions.append(
                    {
                        "name": plugin["name"],
                        "version": plugin["version"],
                        "online_version": online_version,
                    }
                )
                continue

            try:
                is_updatable = (
                    VersionComparator.compare_version(
                        local_version_for_compare, online_version_for_compare
                    )
                    == -1
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
                continue

            if is_updatable:
                result.updates.append(
                    {
                        **plugin,
                        "online_version": online_version,
                        "download_url": str(
                            match.entry.get("download_url") or ""
                        ).strip(),
                        "market_id": match.entry.get("_market_id", ""),
                        "matched_by": match.matched_by,
                    }
                )

<<<<<<< Updated upstream
=======
        market_plugins = [
            plugin for plugin in local_plugins if plugin["name"] not in claimed_plugins
        ]
        market_data: object | None = None
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            for plugin_name, binding in bindings.items():
                plugin = local_by_name.get(plugin_name)
                if not plugin:
                    continue
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
>>>>>>> Stashed changes
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

import inspect
import re
import traceback
import aiohttp
from pathlib import Path
from datetime import datetime  # 供调试模式使用

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.version_comparator import VersionComparator
import astrbot.api.message_components as Comp

# 导入 APScheduler 库，用于定时任务
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 导入重启部件
from .dashboard_client import DashboardClient
import asyncio


@register(
    "astrbot_plugin_update_manager",
    "bushikq",
    "一个用于一键更新和管理所有AstrBot插件的工具，支持定时检查",
    "2.3.0",
)
class PluginUpdateManager(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 配置读取
        self.interval_hours = self.config.get("interval_hours", 24)
        # self.proxy_address = self.context.get_config()["http_proxy"]代理地址
        self.proxy_address = self.config.get("github_proxy", None)
        self.test_mode = self.config.get("test_mode", False)
        self.black_plugin_list = self.config.get("black_plugin_list", [])
        self.white_plugin_list = self.config.get("white_plugin_list", [])
        self.admin_sid_list = self.config.get("admin_sid_list", [])
        self.restart_mode = self.config.get("restart_mode", False)

        # 仅定义变量，暂不初始化
        self.dashboard: DashboardClient = None
        self.scheduler: AsyncIOScheduler = None

        # 运行时辅助变量
        self.not_found_plugins_names = []
        self.not_found_plugins_data = []

        if self.proxy_address:
            logger.info(f"使用代理：{self.proxy_address}")

    async def initialize(self):
        # 初始化并启动定时任务
        if self.interval_hours:
            self.scheduler = AsyncIOScheduler()
            self.scheduler.add_job(
                self._scheduled_update_check,
                "interval",
                hours=self.interval_hours,
                id="scheduled_plugin_update",
                name="Scheduled Plugin Update Check",
            )
            self.scheduler.start()
            logger.info(
                f"插件更新管理器已启动，每 {self.interval_hours} 小时检查一次。"
            )
        else:
            logger.info("插件更新管理器已启动，未配置定时任务。")

        # 初始化 Dashboard 客户端
        if self.restart_mode:
            self.dashboard = DashboardClient(self.context)
            if self.dashboard:
                await self.dashboard.initialize()
            logger.info("插件更新管理器：重启模块已就绪")

    async def terminate(self):
        """插件卸载或机器人关闭时调用"""
        # 关闭调度器
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("定时任务调度器已关闭。")

        # 关闭 Dashboard 连接
        if self.dashboard:
            await self.dashboard.terminate()
            logger.info("重启模块连接已断开。")

    async def _scheduled_update_check(self):
        """定时任务回调"""
        logger.info("定时任务：正在检查并更新所有插件...")
        final_message, need_to_restart = await self._check_and_perform_updates()
        msg_components = [(Comp.Plain(text=final_message))]
        await self.send_message_to_admin(msg_components)

        # 尝试重启
        if need_to_restart:
            await self.restart_command()

    async def send_message_to_admin(self, msg_components):
        if self.admin_sid_list:  # 如果有管理员sid，则发送消息给管理员
            for admin in self.admin_sid_list:
                try:
                    await self.context.send_message(
                        admin,
                        MessageChain(msg_components),
                    )
                except Exception as e:
                    logger.error(f"定时任务：发送给管理员{admin}消息失败：{e}")

    async def restart_command(self):
        """执行重启的内部方法"""
        if not self.dashboard:
            self.dashboard = DashboardClient(self.context)
            await self.dashboard.initialize()
        logger.info("准备执行重启...")
        try:
            await self.dashboard.restart()
        except Exception as e:
            logger.error(f"重启失败: {e}")
            await self.send_message_to_admin([Comp.Plain(text=f"尝试重启失败: {e}")])

    async def _check_and_perform_updates(self) -> str:
        """
        返回一个字符串，包含更新的结果摘要。
        """
        # 检查所有必要的依赖是否成功导入

        plug_path = Path(__file__).resolve().parent.parent
        logger.info(f"插件目录：{plug_path}")
        if not plug_path.is_dir():
            return f"未找到插件目录 {plug_path}，无法执行更新。", False

        update_summary_messages = []
        error_msg = []
        failed_plugins = []
        successed_plugins = []

        try:
            if self.test_mode:  # 调试模式
                with open(
                    Path(__file__).resolve().parent / "test.md", "w", encoding="utf-8"
                ) as f:
                    f.write(f"于{datetime.now()}记录\n ")
                    logger.info("调试模式：已生成测试文件 test.md。")

            # 提取需要更新插件的信息列表，用于日志输出
            plugins_to_update = await self.get_need_update_plugins_list()
            if not plugins_to_update:
                message = "目前没有发现需要更新的插件。"
                logger.info(f"{message}")
                return message, False
            plugin_names_to_update = [p["name"] for p in plugins_to_update]
            logger.info(
                f"发现 {len(plugin_names_to_update)} 个需要更新的插件：{plugin_names_to_update}。"
            )
            update_summary_messages.append(
                f"发现 {len(plugin_names_to_update)} 个插件需要更新。"
            )

            # 旧版 AstrBot 的 update_plugin 没有 download_url 参数，动态探测以保持兼容
            supports_download_url = (
                "download_url"
                in inspect.signature(
                    self.context._star_manager.update_plugin
                ).parameters
            )

            # 遍历并逐个更新插件
            for plugin_to_update in plugins_to_update:
                plugin_name_to_update = plugin_to_update["name"]
                try:
                    logger.info(f"正在更新插件：{plugin_name_to_update}...")
                    update_kwargs = {
                        "plugin_name": plugin_name_to_update,
                        "proxy": self.proxy_address,
                    }
                    # 新版插件市场提供 download_url（市场托管的指定版本 zip 包），
                    # 优先使用它以保证更新到的版本与市场展示一致
                    if supports_download_url and plugin_to_update.get("download_url"):
                        update_kwargs["download_url"] = plugin_to_update[
                            "download_url"
                        ]
                    await self.context._star_manager.update_plugin(**update_kwargs)
                    # await self.context._star_manager.reload(specified_plugin_name=plugin_name_to_update)实测会自动重载插件，无需手动重新加载
                    logger.info(f"插件 {plugin_name_to_update} 更新并已自动重新加载。")
                    successed_plugins.append(
                        f"{plugin_name_to_update} "
                        f"({plugin_to_update.get('version') or '?'} -> "
                        f"{plugin_to_update.get('online_version') or '?'})"
                    )

                except Exception as e:
                    error_msg.append(f"更新插件 {plugin_name_to_update} 失败: {str(e)}")
                    failed_plugins.append(plugin_name_to_update)
                    logger.error(f"更新失败: {traceback.format_exc()}")

            # 构建最终的回复消息
            final_reply_to_user = "\n".join(update_summary_messages)
            if error_msg:
                final_reply_to_user += (
                    f"\n\n注意：部分插件更新失败：{str(failed_plugins)}。\n"
                    + "\n".join(error_msg)
                )
                final_reply_to_user += "\n".join(error_msg)
            if self.not_found_plugins_names:
                final_reply_to_user += f"\n\n注意：插件{str(self.not_found_plugins_names)} 名称不一致，未能判断是否需要更新。\n"
            final_reply_to_user += (
                f"\n成功更新 {len(successed_plugins)} 个插件。\n{successed_plugins}"
            )
            if successed_plugins and self.restart_mode:
                final_reply_to_user += "\n\n即将重启astrbot..."
                need_to_restart = True
            else:
                need_to_restart = False

            return final_reply_to_user, need_to_restart

        except Exception as e:
            logger.error(f"插件更新流程异常: {traceback.format_exc()}")
            return f"插件更新流程异常终止: {e}", False

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("更新所有插件", alias={"updateallplugins", "更新全部插件"})
    async def update_all_plugins_command(self, event: AstrMessageEvent):
        """
        当用户发送 "更新所有插件" 命令时，触发检查并更新所有需要更新的插件。
        """
        logger.info("收到用户命令 '更新所有插件'。")
        yield event.plain_result("正在检查并更新所有插件，请稍候...")

        # 调用核心更新逻辑，并将结果返回给用户
        result_message, need_to_restart = await self._check_and_perform_updates()
        yield event.plain_result(result_message).use_t2i(False)
        if need_to_restart:
            await self.restart_command()

    async def _fetch_online_plugins(self):
        """
        异步从远程 URL 获取在线插件列表。
        """
        urls = [
            "https://cloud.astrbot.app/api/v1/market/plugins.json",  # 新版官方插件市场 API
            "https://api.soulter.top/astrbot/plugins",  # 旧地址，现 301 重定向到新市场，保留兜底
            "https://github.com/AstrBotDevs/AstrBot_Plugins_Collection/raw/refs/heads/main/plugin_cache_original.json",  # 旧格式缓存兜底
        ]  # 创建列表，防止url出现变动 方便维护
        remote_data = None

        for url in urls:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            # content_type=None：GitHub raw 返回 text/plain，需跳过 MIME 校验
                            remote_data = await response.json(content_type=None)
                            if remote_data and isinstance(remote_data, dict):
                                logger.info("成功获取远程插件市场数据")
                                return remote_data
                        else:
                            logger.error(f"请求 {url} 失败，状态码：{response.status}")
            except Exception as e:
                logger.error(f"请求 {url} 失败，错误：{e}")

        logger.warning("远程插件市场数据获取失败")
        return None

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重启astrbot")
    async def restart_astrbot_command(self, event: AstrMessageEvent):
        """
        当用户发送 "重启astrbot" 命令时，触发重启操作。
        """
        logger.info("收到用户命令 '重启astrbot'。")
        yield event.plain_result("正在重启，请稍候...")
        await self.restart_command()

    @staticmethod
    def _normalize_name(name) -> str:
        """插件名归一化：小写、连字符转下划线（新版市场中部分键使用连字符风格）"""
        return str(name or "").strip().lower().replace("-", "_")

    @staticmethod
    def _normalize_repo(repo) -> str:
        """仓库地址归一化，用于与市场条目精确匹配"""
        repo = str(repo or "").strip().lower().rstrip("/")
        if repo.endswith(".git"):
            repo = repo[: -len(".git")]
        for prefix in ("https://", "http://", "www."):
            if repo.startswith(prefix):
                repo = repo[len(prefix) :]
        return repo

    @staticmethod
    def _clean_version(version) -> str:
        """去掉版本号的 v/V 前缀（新版市场版本号取自 metadata/发布标签，前缀不统一）"""
        return re.sub(r"^[vV]", "", str(version or "").strip())

    def _build_market_index(self, online_plugins_data: dict):
        """兼容新旧两种插件市场数据格式，建立 插件名/仓库地址 两套索引。

        新格式（cloud.astrbot.app）：键为 "作者/插件名"，包含 $meta 元数据键，
        条目内含 name、download_url 等字段；旧格式：键即插件名。
        """
        by_name, by_repo = {}, {}
        for key, entry in online_plugins_data.items():
            if key == "$meta" or not isinstance(entry, dict):
                continue
            name = entry.get("name") or (
                key.split("/", 1)[-1] if "/" in key else key
            )
            entry = {**entry, "name": name}
            by_name.setdefault(self._normalize_name(name), entry)
            if entry.get("repo"):
                by_repo.setdefault(self._normalize_repo(entry["repo"]), entry)
        return by_name, by_repo

    def _find_market_entry(self, local_plugin: dict, by_name: dict, by_repo: dict):
        """按官方 WebUI 的顺序匹配市场条目：仓库地址优先，其次插件名"""
        if local_plugin.get("repo"):
            entry = by_repo.get(self._normalize_repo(local_plugin["repo"]))
            if entry:
                return entry
        name = self._normalize_name(local_plugin.get("name"))
        candidates = [name]
        if name.startswith("astrbot_plugin_"):
            candidates.append(name[len("astrbot_plugin_") :])
        else:
            candidates.append(f"astrbot_plugin_{name}")
        for candidate in candidates:
            entry = by_name.get(candidate)
            if entry:
                return entry
        return None

    async def get_need_update_plugins_list(self):
        """
        获取本地插件列表，并与在线版本进行比较，返回需要更新的插件信息列表。
        每项为 dict，包含 name、version、online_version、download_url 等字段。
        """
        self.not_found_plugins_data = []
        self.not_found_plugins_names = []
        local_plugins_list = []
        need_examine_list = self.context.get_all_stars()
        for plugin in need_examine_list:
            if getattr(plugin, "reserved", False):
                continue  # 跳过 AstrBot 保留（系统）插件
            if plugin.name in self.black_plugin_list:
                continue  # 跳过黑名单插件
            if self.white_plugin_list and plugin.name not in self.white_plugin_list:
                continue  # 白名单不为空时，跳过白名单外插件
            local_plugins_list.append(
                {
                    "name": plugin.name,
                    "version": plugin.version,
                    "author": plugin.author,
                    "desc": plugin.desc,
                    "repo": plugin.repo,
                    "is_updatable": False,
                    "online_version": "",
                    "download_url": "",
                }
            )
        online_plugins_data = await self._fetch_online_plugins()
        if self.test_mode:  # 调试模式
            with open(
                Path(__file__).resolve().parent / "test.md", "w", encoding="utf-8"
            ) as f:
                f.write(f"于{datetime.now()}记录\n\n")
                f.write(f"本地插件列表：{local_plugins_list}\n\n")
                f.write(f"在线插件市场数据：{online_plugins_data}\n\n")
        if not online_plugins_data:
            logger.warning("无法获取在线插件数据，跳过版本比较。")
            return []
        by_name, by_repo = self._build_market_index(online_plugins_data)
        for p in local_plugins_list:
            online_plugin_data = self._find_market_entry(p, by_name, by_repo)

            if online_plugin_data:
                p["online_version"] = self._clean_version(
                    online_plugin_data.get("version", "")
                )
                p["download_url"] = online_plugin_data.get("download_url", "")
                local_version = self._clean_version(p["version"])
                if not local_version or not p["online_version"]:
                    continue  # 任一侧版本号缺失时无法比较，跳过
                try:
                    if (
                        VersionComparator.compare_version(
                            local_version, p["online_version"]
                        )
                        == -1
                    ):
                        p["is_updatable"] = True
                    else:
                        p["is_updatable"] = False
                except Exception as e:
                    logger.error(f"比较插件 {p['name']} 的版本时出错: {e}")
                    p["is_updatable"] = False  # 发生错误时，保守地认为不可更新
            elif (
                "astrbot-" in (p["name"] or "")
                or p["name"] == "astrbot"
                or p["repo"] == "https://astrbot.app"
            ):
                continue  # 跳过系统插件
            else:
                logger.warning(f"插件 {p['name']} 不在在线插件市场中。")
                self.not_found_plugins_names.append(p["name"])
                self.not_found_plugins_data.append(p)
        if self.test_mode:  # 调试模式
            with open(
                Path(__file__).resolve().parent / "test.md", "a", encoding="utf-8"
            ) as f:
                f.write(f"最终列表：{local_plugins_list}\n\n")
                f.write(f"名称不一致的插件信息：{self.not_found_plugins_data}\n\n")
        return [p for p in local_plugins_list if p["is_updatable"]]

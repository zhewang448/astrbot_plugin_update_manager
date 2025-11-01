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


@register(
    "astrbot_plugin_update_manager",
    "bushikq",
    "一个用于一键更新和管理所有AstrBot插件的工具，支持定时检查",
    "2.2.1",
)
class PluginUpdateManager(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.interval_hours = self.config.get("interval_hours", 24)
        # self.proxy_address = self.context.get_config()["http_proxy"]代理地址
        self.proxy_address = self.config.get("github_proxy", None)
        self.test_mode = self.config.get("test_mode", False)
        self.black_plugin_list = self.config.get("black_plugin_list", [])
        self.white_plugin_list = self.config.get("white_plugin_list", [])
        self.admin_sid_list = self.config.get("admin_sid_list", [])

        if self.proxy_address:
            logger.info(f"使用代理：{self.proxy_address}")
        else:
            logger.info("未设置代理。")

        if self.interval_hours:
            # 初始化 APScheduler 调度器
            self.scheduler = AsyncIOScheduler()
            # 添加一个定时任务，检查并更新插件
            self.scheduler.add_job(
                self._scheduled_update_check,
                "interval",
                hours=self.interval_hours,
                id="scheduled_plugin_update",
                name="Scheduled Plugin Update Check",
            )
            # 启动调度器
            self.scheduler.start()
            logger.info("插件更新管理器已启动，定时任务已安排。")
        else:
            logger.info("插件更新管理器已启动，但未配置定时任务。")

    async def _scheduled_update_check(self):
        """
        这个方法会被 APScheduler 定时调用，用于检查并更新所有插件。
        """
        logger.info("定时任务：正在检查并更新所有插件...")
        final_message = await self._check_and_perform_updates()
        msg_components = [(Comp.Plain(text=final_message))]
        if self.admin_sid_list:  # 如果有管理员sid，则发送消息给管理员
            for admin in self.admin_sid_list:
                try:
                    await self.context.send_message(
                        admin,
                        MessageChain(msg_components),
                    )
                except Exception as e:
                    logger.error(f"定时任务：发送给管理员{admin}消息失败：{e}")

    async def _check_and_perform_updates(self) -> str:
        """
        返回一个字符串，包含更新的结果摘要。
        """
        # 检查所有必要的依赖是否成功导入

        plug_path = Path(__file__).resolve().parent.parent
        logger.info(f"插件目录：{plug_path}")
        if not plug_path.is_dir():
            error_msg = f"未找到插件目录 {plug_path}，无法执行更新。"
            logger.error(f"错误：未找到插件目录 {plug_path}。")
            return error_msg

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

            # 提取需要更新插件的名称列表，用于日志输出
            plugin_names_to_update = await self.get_need_update_plugins_list()
            if not plugin_names_to_update:
                message = "目前没有发现需要更新的插件。"
                logger.info(f"{message}")
                return message
            logger.info(
                f"发现 {len(plugin_names_to_update)} 个需要更新的插件：{plugin_names_to_update}。"
            )
            update_summary_messages.append(
                f"发现 {len(plugin_names_to_update)} 个插件需要更新。"
            )

            # 遍历并逐个更新插件
            for plugin_name_to_update in plugin_names_to_update:
                try:
                    logger.info(f"正在更新插件：{plugin_name_to_update}...")
                    await self.context._star_manager.update_plugin(
                        plugin_name=plugin_name_to_update,
                        proxy=self.proxy_address,
                    )
                    # await self.context._star_manager.reload(specified_plugin_name=plugin_name_to_update)实测会自动重载插件，无需手动重新加载
                    logger.info(f"插件 {plugin_name_to_update} 更新并已自动重新加载。")
                    successed_plugins.append(plugin_name_to_update)

                except Exception as e:
                    error_msg.append(f"更新插件 {plugin_name_to_update} 失败: {str(e)}")
                    failed_plugins.append(plugin_name_to_update)
                    logger.error(f"{error_msg}\n{traceback.format_exc()}")

            # 构建最终的回复消息
            final_reply_to_user = "\n".join(update_summary_messages)
            if error_msg:
                final_reply_to_user += (
                    f"\n\n注意：部分插件更新失败：{str(failed_plugins)}。\n"
                )
                final_reply_to_user += "\n".join(error_msg)
            if self.not_found_plugins_names:
                final_reply_to_user += f"\n\n注意：插件{str(self.not_found_plugins_names)} 名称不一致，未能判断是否需要更新。\n"
            final_reply_to_user += (
                f"\n成功更新 {len(successed_plugins)} 个插件。\n{successed_plugins}"
            )
            return final_reply_to_user

        except Exception as e:
            error_msg = f"插件更新流程中发生意外错误: {traceback.format_exc()}"
            logger.error(f"{error_msg}")
            return f"插件更新流程异常终止: {e}。请检查机器人日志。"

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("更新所有插件", alias={"updateallplugins", "更新全部插件"})
    async def update_all_plugins_command(self, event: AstrMessageEvent):
        """
        当用户发送 "更新所有插件" 命令时，触发检查并更新所有需要更新的插件。
        """
        logger.info("收到用户命令 '更新所有插件'。")
        yield event.plain_result("正在检查并更新所有插件，请稍候...")

        # 调用核心更新逻辑，并将结果返回给用户
        result_message = await self._check_and_perform_updates()
        yield event.plain_result(result_message).use_t2i(False)

    async def _fetch_online_plugins(self):
        """
        异步从远程 URL 获取在线插件列表。
        这部分代码基于你在 plugin.py 中提供的逻辑。
        """
        urls = [
            "https://api.soulter.top/astrbot/plugins",
            "https://github.com/AstrBotDevs/AstrBot_Plugins_Collection/raw/refs/heads/main/plugin_cache_original.json",
        ]  # 创建列表，防止url出现变动 方便维护
        remote_data = None

        for url in urls:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            remote_data = await response.json()
                            if remote_data:
                                logger.info("成功获取远程插件市场数据")
                                return remote_data
                        else:
                            logger.error(f"请求 {url} 失败，状态码：{response.status}")
            except Exception as e:
                logger.error(f"请求 {url} 失败，错误：{e}")

        logger.warning("远程插件市场数据获取失败")
        return None

    async def get_need_update_plugins_list(self):
        """
        获取本地插件列表，并与在线版本进行比较，返回需要更新的插件名列表。
        """
        self.not_found_plugins_data = []
        self.not_found_plugins_names = []
        local_plugins_list = []
        need_examine_list = self.context.get_all_stars()
        for plugin in need_examine_list:
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
            return local_plugins_list
        for p in local_plugins_list:
            if p_name := p.get("name"):
                online_plugin_data = (
                    online_plugins_data.get(p_name)
                    or online_plugins_data.get(p_name.lower())
                    or online_plugins_data.get(p_name.replace("astrbot_plugin_", ""))
                    or online_plugins_data.get(f"astrbot_plugin_{p_name}")
                )
            if not online_plugin_data and p.get("repo"):
                online_plugin_data = online_plugins_data.get(p["repo"].split("/")[-1])

            if online_plugin_data:
                p["online_version"] = online_plugin_data.get("version", "")
                try:
                    if (
                        VersionComparator.compare_version(
                            p["version"], p["online_version"]
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
                "astrbot-" in p["name"]
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
        return [p["name"] for p in local_plugins_list if p["is_updatable"]]

    async def terminate(self):
        """
        插件终止时（例如：插件被禁用或机器人关闭），关闭 APScheduler 定时任务。
        """
        if self.scheduler.running:
            self.scheduler.shutdown()  # 关闭调度器
            logger.info("定时任务调度器已关闭。")

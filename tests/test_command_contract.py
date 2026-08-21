import ast
import unittest
from pathlib import Path


class PluginDataCleanupCommandContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_path = Path(__file__).resolve().parents[1] / "main.py"
        cls.tree = ast.parse(source_path.read_text(encoding="utf-8"))
        cls.command = next(
            node
            for node in ast.walk(cls.tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "clear_plugin_data_command"
        )

    def test_command_is_admin_only_and_requires_confirmation(self):
        decorators = [ast.unparse(item) for item in self.command.decorator_list]
        self.assertIn("filter.permission_type(filter.PermissionType.ADMIN)", decorators)
        self.assertIn(
            "filter.command('清除插件数据', alias={'clearplugindata'})",
            decorators,
        )
        self.assertIn("--confirm", ast.unparse(self.command))

    def test_cleanup_preserves_config_and_reloads_target_plugin(self):
        calls = [
            node
            for node in ast.walk(self.command)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"cleanup_method", "reload_method"}
        ]
        cleanup_call = next(
            call
            for call in calls
            if isinstance(call.func, ast.Name) and call.func.id == "cleanup_method"
        )
        cleanup_keywords = {
            keyword.arg: ast.literal_eval(keyword.value)
            for keyword in cleanup_call.keywords
            if keyword.arg in {"delete_config", "delete_data"}
        }
        self.assertEqual(
            cleanup_keywords,
            {"delete_config": False, "delete_data": True},
        )
        reload_call = next(
            call
            for call in calls
            if isinstance(call.func, ast.Name) and call.func.id == "reload_method"
        )
        self.assertEqual(
            [keyword.arg for keyword in reload_call.keywords],
            ["specified_plugin_name"],
        )


class ReinstallInlineRepositoryCommandContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        cls.command = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "reinstall_plugin_command"
        )

    def test_inline_repository_url_reads_remote_metadata_name(self):
        source = ast.unparse(self.command)
        self.assertIn("link_only", source)
        self.assertIn("inspect_plugin_repository", source)
        self.assertIn("remote_plugin.get('name')", source)


class CustomSourceMetadataRequestContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        cls.method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_fetch_custom_source"
        )

    def test_metadata_uses_authenticated_contents_api_not_raw_host(self):
        source = ast.unparse(self.method)
        self.assertIn("/contents/", source)
        self.assertIn("application/vnd.github.raw+json", source)
        self.assertNotIn("raw.githubusercontent.com", source)


class ManualRestartCompletionNotificationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name
            in {
                "restart_astrbot_command",
                "on_astrbot_loaded",
                "on_platform_loaded",
                "_notify_pending_restart",
                "_save_pending_restart",
            }
        }
        cls.command = methods["restart_astrbot_command"]
        cls.loaded_hook = methods["on_astrbot_loaded"]
        cls.platform_hook = methods["on_platform_loaded"]
        cls.notify_pending_restart = methods["_notify_pending_restart"]
        cls.save_pending_restart = methods["_save_pending_restart"]

    def test_manual_restart_persists_origin_before_requesting_restart(self):
        source = ast.unparse(self.command)
        self.assertIn("await self._save_pending_restart(event.unified_msg_origin)", source)
        self.assertIn("self._clear_pending_restart()", source)

    def test_startup_hook_notifies_pending_session_and_clears_record(self):
        decorators = [ast.unparse(item) for item in self.loaded_hook.decorator_list]
        source = ast.unparse(self.notify_pending_restart)
        self.assertIn("_compatible_filter_hook('on_astrbot_loaded')", decorators)
        self.assertIn("await self.context.send_message(session, MessageChain([Comp.Plain(text='AstrBot 已重启完成。')]))", source)
        self.assertIn("self._pending_restart_path.unlink()", source)

    def test_platform_loaded_hook_retries_the_same_notification(self):
        decorators = [ast.unparse(item) for item in self.platform_hook.decorator_list]
        source = ast.unparse(self.platform_hook)
        self.assertIn("_compatible_filter_hook('on_platform_loaded')", decorators)
        self.assertIn("await self._notify_pending_restart()", source)

    def test_notification_keeps_record_when_delivery_is_not_confirmed(self):
        source = ast.unparse(self.notify_pending_restart)
        self.assertIn("if not sent", source)
        self.assertIn("logger.warning", source)
        self.assertIn("exc!r", source)

    def test_pending_record_uses_plugin_data_directory(self):
        source = ast.unparse(self.save_pending_restart)
        self.assertIn("json.dumps({'session': session}, ensure_ascii=False)", source)

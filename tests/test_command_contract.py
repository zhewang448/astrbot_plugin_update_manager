import ast
import json
import unittest
from pathlib import Path


class VersionConsistencyContractTests(unittest.TestCase):
    def test_current_version_is_aligned_across_release_files(self):
        root = Path(__file__).resolve().parents[1]
        expected = "2.8.0"
        readme = root.joinpath("README.md").read_text(encoding="utf-8")
        metadata = root.joinpath("metadata.yaml").read_text(encoding="utf-8")
        changelog = root.joinpath("CHANGELOG.md").read_text(encoding="utf-8")
        source = root.joinpath("main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        register_call = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "register"
        )
        self.assertEqual(ast.literal_eval(register_call.args[3]), expected)
        self.assertIn(f"# AstrBot 插件更新管理器 v{expected}", readme)
        self.assertIn(f"version: v{expected}", metadata)
        self.assertIn(f"## v{expected}", changelog)


class PluginMaintenanceDocumentationContractTests(unittest.TestCase):
    def test_reinstall_repository_form_is_under_plugin_maintenance(self):
        readme = Path(__file__).resolve().parents[1].joinpath("README.md").read_text(
            encoding="utf-8"
        )
        maintenance_start = readme.index("| **插件维护** |")
        framework_start = readme.index("| **AstrBot 框架** |")
        reinstall_form = "`重新安装插件<仓库链接> [--no-proxy]`"
        self.assertGreaterEqual(readme.index(reinstall_form), maintenance_start)
        self.assertLess(readme.index(reinstall_form), framework_start)


class ScheduledPluginUpdateModeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.schema = json.loads(root.joinpath("_conf_schema.json").read_text(encoding="utf-8"))
        tree = ast.parse(root.joinpath("main.py").read_text(encoding="utf-8"))
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name
            in {
                "__init__",
                "_scheduled_update_check",
                "_scheduled_astrbot_update",
                "_check_astrbot_update_only",
                "_format_plugin_check_result",
                "_migrate_legacy_config",
            }
        }
        cls.initialize = methods["__init__"]
        cls.scheduled_check = methods["_scheduled_update_check"]
        cls.scheduled_framework_check = methods["_scheduled_astrbot_update"]
        cls.framework_check_only = methods["_check_astrbot_update_only"]
        cls.format_check_result = methods["_format_plugin_check_result"]
        cls.migrate_config = methods["_migrate_legacy_config"]

    def test_auto_update_configuration_defaults_to_enabled(self):
        self.assertEqual(self.schema["plugin_updates"]["type"], "object")
        self.assertEqual(self.schema["framework_updates"]["type"], "object")
        self.assertTrue(
            self.schema["plugin_updates"]["items"]["auto_update"]["default"]
        )
        self.assertTrue(
            self.schema["framework_updates"]["items"]["auto_update"]["default"]
        )
        self.assertTrue(
            self.schema["schedule_mode"]["invisible"]
            and self.schema["astrbot_auto_update"]["invisible"]
        )
        source = ast.unparse(self.initialize)
        self.assertIn("self._migrate_legacy_config()", source)
        self.assertIn("self.plugin_auto_update = plugin_config.get('auto_update', True)", source)

    def test_framework_schedule_can_check_without_applying_updates(self):
        source = ast.unparse(self.scheduled_framework_check)
        self.assertIn("self.astrbot_framework_auto_update", source)
        self.assertIn("await self._check_astrbot_update_only()", source)
        self.assertIn("need_to_restart = False", source)
        check_source = ast.unparse(self.framework_check_only)
        self.assertIn("check_astrbot_update", check_source)
        self.assertIn("未执行更新或重启", check_source)

    def test_migration_maps_flat_keys_into_nested_sections(self):
        source = ast.unparse(self.migrate_config)
        self.assertIn("plugin_updates", source)
        self.assertIn("framework_updates", source)
        self.assertIn("self.config[legacy_key] = default", source)
        self.assertIn("save_config", source)

    def test_disabled_mode_checks_and_notifies_without_updating_or_restarting(self):
        source = ast.unparse(self.scheduled_check)
        self.assertIn("if self.plugin_auto_update", source)
        self.assertIn("await self._check_and_perform_updates()", source)
        self.assertIn("await self.get_need_update_plugins_list()", source)
        self.assertIn("self._format_plugin_check_result(check_result)", source)
        self.assertIn("need_to_restart = False", source)
        self.assertIn("await self.send_message_to_admin", source)
        self.assertIn("if need_to_restart", source)

    def test_check_only_message_reuses_standard_update_report(self):
        source = ast.unparse(self.format_check_result)
        self.assertIn("format_update_report", source)
        self.assertIn("self._format_check_notes(check_result)", source)
        self.assertIn("truncate_text", source)


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
            "filter.command('清除插件数据', alias={'clearplugindata', 'clearplugin'})",
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


class TestPluginChangelogCommandContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        cls.command = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "test_plugin_changelog_command"
        )

    def test_command_is_admin_only_and_returns_real_local_logs(self):
        decorators = [ast.unparse(item) for item in self.command.decorator_list]
        source = ast.unparse(self.command)
        self.assertIn("filter.permission_type(filter.PermissionType.ADMIN)", decorators)
        self.assertIn(
            "filter.command('测试插件管理日志', alias={'testpluginchangelog', '测试插件日志', '测试插件更新日志'})",
            decorators,
        )
        self.assertIn("entries = self._get_local_plugin_preview_entries()[:5]", source)
        self.assertIn("report = self._build_local_plugin_test_report(entries)", source)
        self.assertIn("await self._build_local_plugin_changelog_nodes(entries)", source)
        self.assertIn("if self.astrbot_update_enabled", source)
        self.assertIn("await self._build_latest_astrbot_changelog_node()", source)
        self.assertIn("yield event.chain_result([NodesCls(nodes=nodes)]).use_t2i(False)", source)
        self.assertNotIn("truncate_text", source)


class UpdateManagerHelpCommandContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        cls.command = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "update_manager_help_command"
        )

    def test_help_command_lists_all_categories(self):
        decorators = [ast.unparse(item) for item in self.command.decorator_list]
        source = ast.unparse(self.command)
        self.assertIn("filter.command('更新管理帮助', alias={'updatemanagerhelp'})", decorators)
        self.assertIn('【插件更新】', source)
        self.assertIn('【插件维护】', source)
        self.assertIn('【AstrBot 框架】', source)


class CommandOrderingContractTests(unittest.TestCase):
    def test_commands_are_grouped_by_category_and_logical_order(self):
        source_path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        expected_order = [
            "update_manager_help_command",
            "check_plugins_command",
            "update_all_plugins_command",
            "test_plugin_changelog_command",
            "install_plugin_command",
            "reinstall_plugin_command",
            "clear_plugin_data_command",
            "check_astrbot_update_command",
            "update_astrbot_command",
            "restart_astrbot_command",
        ]
        commands = {
            node.name: node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name in expected_order
        }
        self.assertEqual(
            [name for name, _ in sorted(commands.items(), key=lambda item: item[1])],
            expected_order,
        )


class ManualRestartCompletionNotificationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name
            in {
                "restart_astrbot_command",
                "on_astrbot_loaded",
                "on_platform_loaded",
                "_schedule_pending_restart_notification",
                "_retry_pending_restart_notification",
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
        self.assertIn("self._schedule_pending_restart_notification()", source)

    def test_notification_uses_delayed_retries_until_platform_api_is_ready(self):
        tree = ast.parse(Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8"))
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name in {"_schedule_pending_restart_notification", "_retry_pending_restart_notification"}
        }
        schedule_source = ast.unparse(methods["_schedule_pending_restart_notification"])
        retry_source = ast.unparse(methods["_retry_pending_restart_notification"])
        self.assertIn("asyncio.create_task", schedule_source)
        self.assertIn("await asyncio.sleep(5)", retry_source)
        self.assertIn("for attempt in range(1, 13)", retry_source)
        self.assertIn("if await self._notify_pending_restart()", retry_source)

    def test_notification_keeps_record_when_delivery_is_not_confirmed(self):
        source = ast.unparse(self.notify_pending_restart)
        self.assertIn("if not sent", source)
        self.assertIn("logger.warning", source)
        self.assertIn("exc!r", source)

    def test_pending_record_uses_plugin_data_directory(self):
        source = ast.unparse(self.save_pending_restart)
        self.assertIn("json.dumps({'session': session}, ensure_ascii=False)", source)

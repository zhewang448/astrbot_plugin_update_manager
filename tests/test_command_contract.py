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

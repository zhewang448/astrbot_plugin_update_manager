import ast
import json
import logging
import sys
import types
from pathlib import Path

import pytest


astrbot_module = types.ModuleType("astrbot")
astrbot_api_module = types.ModuleType("astrbot.api")
astrbot_api_module.logger = logging.getLogger("astrbot-test")
astrbot_core_module = types.ModuleType("astrbot.core")
astrbot_star_module = types.ModuleType("astrbot.core.star")
astrbot_context_module = types.ModuleType("astrbot.core.star.context")
astrbot_context_module.Context = object
sys.modules.setdefault("astrbot", astrbot_module)
sys.modules.setdefault("astrbot.api", astrbot_api_module)
sys.modules.setdefault("astrbot.core", astrbot_core_module)
sys.modules.setdefault("astrbot.core.star", astrbot_star_module)
sys.modules.setdefault("astrbot.core.star.context", astrbot_context_module)

from dashboard_client import DashboardClient  # noqa: E402


class FakeResponse:
    def __init__(self, body):
        self.status = 200
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self, **_kwargs):
        return self._body


class FakeSession:
    closed = False

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse(self.responses.pop(0))


def build_client(responses):
    client = object.__new__(DashboardClient)
    client._session = FakeSession(responses)
    client.core_update_check_url = "http://localhost/api/update/check"
    client.core_update_url = "http://localhost/api/update/do"
    client.core_update_releases_url = "http://localhost/api/update/releases"
    client.core_update_progress_url = "http://localhost/api/update/progress"
    client._generate_jwt = lambda: "test-token"
    return client


@pytest.mark.asyncio
async def test_framework_update_uses_dashboard_progress_endpoints():
    client = build_client(
        [
            {
                "status": "success",
                "message": "ReleaseInfo(version='v4.28.0')",
                "data": {"version": "v4.27.4", "has_new_version": True},
            },
            {"status": "ok", "data": {"id": "progress-1"}},
            {
                "status": "ok",
                "data": {"id": "progress-1", "status": "success"},
            },
        ]
    )

    check = await client.check_astrbot_update()
    progress_id = await client.start_astrbot_update(
        proxy="https://proxy.example", reboot=False
    )
    progress = await client.get_astrbot_update_progress(progress_id)

    assert check["has_new_version"] is True
    assert check["message"] == "ReleaseInfo(version='v4.28.0')"
    assert progress_id == "progress-1"
    assert progress["status"] == "success"
    assert client._session.calls == [
        (
            "GET",
            "http://localhost/api/update/check",
            {"headers": {"Authorization": "Bearer test-token"}, "json": None},
        ),
        (
            "POST",
            "http://localhost/api/update/do",
            {
                "headers": {"Authorization": "Bearer test-token"},
                "json": {
                    "version": "latest",
                    "proxy": "https://proxy.example",
                    "reboot": False,
                },
            },
        ),
        (
            "GET",
            "http://localhost/api/update/progress",
            {
                "headers": {"Authorization": "Bearer test-token"},
                "json": None,
                "params": {"id": "progress-1"},
            },
        ),
    ]


@pytest.mark.asyncio
async def test_framework_update_reads_latest_release_notes():
    client = build_client(
        [
            {
                "status": "ok",
                "data": [
                    {
                        "tag_name": "v4.28.0",
                        "published_at": "2026-09-01T00:00:00Z",
                        "body": "- 修复更新流程",
                    }
                ],
            }
        ]
    )

    release = await client.get_astrbot_latest_release()

    assert release == {"version": "v4.28.0", "notes": "- 修复更新流程"}
    assert client._session.calls == [
        (
            "GET",
            "http://localhost/api/update/releases",
            {"headers": {"Authorization": "Bearer test-token"}, "json": None},
        )
    ]


def test_astrbot_update_commands_check_before_starting_update():
    source = Path("main.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    functions = {
        node.name: node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    check_command = functions["check_astrbot_update_command"]
    update_command = functions["update_astrbot_command"]
    update_helper = functions["_perform_astrbot_update"]
    command_names = {}
    for function in (check_command, update_command):
        decorator = next(
            decorator
            for decorator in function.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "command"
        )
        command_names[function.name] = (
            ast.literal_eval(decorator.args[0]),
            ast.literal_eval(next(keyword.value for keyword in decorator.keywords)),
        )

    assert command_names["check_astrbot_update_command"] == (
        "检查astrbot更新",
        {"checkastrbotupdates", "checkastrbot", "检查AstrBot更新"},
    )
    assert command_names["update_astrbot_command"] == (
        "更新astrbot",
        {"updateastrbot", "astrbotupdate", "更新AstrBot"},
    )

    assert "astrbot_update_enabled" in ast.get_source_segment(source, check_command)
    assert "astrbot_update_enabled" in ast.get_source_segment(source, update_command)

    calls = [
        (node.lineno, node.value.func.attr)
        for node in ast.walk(update_helper)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr in {"check_astrbot_update", "start_astrbot_update"}
    ]
    assert calls == [
        (
            min(line for line, name in calls if name == "check_astrbot_update"),
            "check_astrbot_update",
        ),
        (
            min(line for line, name in calls if name == "start_astrbot_update"),
            "start_astrbot_update",
        ),
    ]

    schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
    legacy_plugin_schedule = {
        "schedule_mode",
        "interval_hours",
        "check_weekdays",
        "check_times",
        "check_on_startup",
    }
    assert legacy_plugin_schedule.issubset(schema)
    assert schema["astrbot_update_enabled"]["default"] is True
    assert schema["astrbot_auto_update"]["default"] is False
    assert schema["astrbot_auto_update"]["condition"] == {
        "astrbot_update_enabled": True
    }
    assert schema["astrbot_schedule_mode"]["default"] == "interval"
    assert schema["astrbot_interval_hours"]["default"] == 24
    assert schema["astrbot_check_weekdays"]["default"] == [
        "mon",
        "tue",
        "wed",
        "thu",
        "fri",
        "sat",
        "sun",
    ]
    assert schema["astrbot_check_times"]["default"] == ["04:00"]
    assert schema["astrbot_check_on_startup"]["default"] is False

    scheduler = functions["_initialize_scheduler"]
    scheduler_source = ast.get_source_segment(source, scheduler)
    assert "self._scheduled_update_check" in scheduler_source
    assert "self._scheduled_astrbot_update" in scheduler_source
    assert "self.astrbot_schedule_mode" in scheduler_source

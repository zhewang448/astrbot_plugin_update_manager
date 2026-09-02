# dashboard_client.py

import datetime
import os
import re
from typing import Any

import aiohttp
import jwt

from astrbot.api import logger
from astrbot.core.star.context import Context


_VERSION_RE = re.compile(
    r"^([0-9]+(?:\.[0-9]+)*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+.+)?$"
)


def _compare_versions(left: object, right: object) -> int | None:
    """按 AstrBot 的 SemVer 规则比较版本；格式无效时返回 None。"""

    def split(value: object) -> tuple[list[int], list[int | str] | None] | None:
        normalized = str(value or "").strip()
        if normalized.startswith(("v", "V")):
            normalized = normalized[1:]
        match = _VERSION_RE.fullmatch(normalized)
        if not match:
            return None
        prerelease = match.group(2)
        return (
            [int(part) for part in match.group(1).split(".")],
            [int(part) if part.isdigit() else part for part in prerelease.split(".")]
            if prerelease
            else None,
        )

    parsed_left = split(left)
    parsed_right = split(right)
    if not parsed_left or not parsed_right:
        return None

    left_core, left_prerelease = parsed_left
    right_core, right_prerelease = parsed_right
    for left_part, right_part in zip(
        left_core + [0] * (len(right_core) - len(left_core)),
        right_core + [0] * (len(left_core) - len(right_core)),
    ):
        if left_part != right_part:
            return 1 if left_part > right_part else -1

    if left_prerelease is None or right_prerelease is None:
        if left_prerelease is right_prerelease:
            return 0
        return 1 if left_prerelease is None else -1

    for index in range(max(len(left_prerelease), len(right_prerelease))):
        left_part = left_prerelease[index] if index < len(left_prerelease) else None
        right_part = right_prerelease[index] if index < len(right_prerelease) else None
        if left_part == right_part:
            continue
        if left_part is None:
            return -1
        if right_part is None:
            return 1
        if isinstance(left_part, int) and isinstance(right_part, str):
            return -1
        if isinstance(left_part, str) and isinstance(right_part, int):
            return 1
        return 1 if left_part > right_part else -1
    return 0


class DashboardClient:
    """
    面板 HTTP 客户端
    - 复用 aiohttp.ClientSession
    - 使用本地 Dashboard JWT 调用内部接口
    """

    def __init__(self, context: Context):
        self.context = context

        dbc = context.get_config().get("dashboard", {})
        self.host = dbc.get("host", "127.0.0.1")
        port_value = os.environ.get("DASHBOARD_PORT") or dbc.get("port", 6185)
        self.port = int(port_value)
        if self.host == "0.0.0.0":
            self.host = "127.0.0.1"

        self.restart_url = f"http://{self.host}:{self.port}/api/stat/restart-core"
        self.core_update_check_url = f"http://{self.host}:{self.port}/api/update/check"
        self.core_update_url = f"http://{self.host}:{self.port}/api/update/do"
        self.core_update_releases_url = (
            f"http://{self.host}:{self.port}/api/update/releases"
        )
        self.core_update_progress_url = (
            f"http://{self.host}:{self.port}/api/update/progress"
        )
        self._session: aiohttp.ClientSession | None = None

    async def initialize(self):
        if self._session and not self._session.closed:
            return
        timeout = aiohttp.ClientTimeout(total=15)
        self._session = aiohttp.ClientSession(timeout=timeout)

    async def terminate(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def restart(self) -> None:
        """重启 AstrBot 核心。"""
        await self._request("POST", self.restart_url)

    async def check_astrbot_update(
        self, *, include_prerelease: bool = False
    ) -> dict[str, Any]:
        """检查 AstrBot 框架是否有可用更新。"""
        body = await self._request("GET", self.core_update_check_url)
        data = body.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Dashboard 返回了无效的框架更新检查结果")
        result = {**data, "message": str(body.get("message") or "")}
        if not include_prerelease:
            return result

        current_version = str(result.get("version") or "")
        try:
            target_release = await self._find_newer_release(current_version)
        except Exception as exc:
            logger.warning(f"获取 AstrBot 预发布版本列表失败，将仅检查正式版本：{exc}")
            return result
        if target_release:
            return {
                **result,
                "has_new_version": True,
                "target_version": target_release["version"],
                "target_release": target_release,
            }
        return result

    async def start_astrbot_update(
        self, *, version: str = "latest", proxy: str = "", reboot: bool = False
    ) -> str:
        """启动 AstrBot 框架更新并返回 Dashboard 进度任务 ID。"""
        body = await self._request(
            "POST",
            self.core_update_url,
            json={"version": version or "latest", "proxy": proxy or None, "reboot": reboot},
        )
        data = body.get("data")
        progress_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(progress_id, str) or not progress_id:
            raise RuntimeError("Dashboard 未返回框架更新任务 ID")
        return progress_id

    async def get_astrbot_latest_release(self) -> dict[str, str] | None:
        """返回 AstrBot 当前可获取的最新发布信息。"""
        releases = await self._get_astrbot_releases()
        return releases[0] if releases else None

    async def _get_astrbot_releases(self) -> list[dict[str, str]]:
        """读取 Dashboard 返回的全部可用发布版本。"""
        body = await self._request("GET", self.core_update_releases_url)
        data = body.get("data")
        if not isinstance(data, list):
            return []
        return [
            {
                "version": version,
                "notes": str(release.get("body") or "").strip(),
            }
            for release in data
            if isinstance(release, dict)
            and (version := str(release.get("tag_name") or "").strip())
        ]

    async def _find_newer_release(self, current_version: str) -> dict[str, str] | None:
        """从包含预发布版本的列表中选择高于当前版本的最高版本。"""
        selected: dict[str, str] | None = None
        for release in await self._get_astrbot_releases():
            if _compare_versions(release["version"], current_version) != 1:
                continue
            if (
                selected is None
                or _compare_versions(release["version"], selected["version"]) == 1
            ):
                selected = release
        return selected

    async def get_astrbot_update_progress(self, progress_id: str) -> dict[str, Any]:
        """查询指定 AstrBot 框架更新任务的进度。"""
        body = await self._request(
            "GET", self.core_update_progress_url, params={"id": progress_id}
        )
        data = body.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Dashboard 返回了无效的框架更新进度")
        return data

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """统一发送带本地 Dashboard 鉴权的请求。"""
        if self._session is None or self._session.closed:
            await self.initialize()

        headers = {"Authorization": f"Bearer {self._generate_jwt()}"}
        async with self._session.request(
            method, url, headers=headers, json=json, **kwargs
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"请求失败 [{resp.status}]: {await resp.text()}")

            body = await resp.json(content_type=None)
            if not isinstance(body, dict):
                raise RuntimeError("Dashboard 返回了未知数据格式")
            if body.get("status") not in {"ok", "success"}:
                raise RuntimeError(
                    f"业务错误: {body.get('message') or body.get('msg')}"
                )
            return body

    def _generate_jwt(self) -> str:
        """使用 AstrBot 本地配置生成 Dashboard JWT。"""
        dbc = self.context.get_config().get("dashboard", {})
        username = dbc.get("username")
        jwt_secret = dbc.get("jwt_secret")
        if not username or not jwt_secret:
            raise RuntimeError("Dashboard 用户名或 jwt_secret 未配置，无法执行重启")

        payload = {
            "username": username,
            "exp": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=5),
        }
        logger.debug("已为重启请求生成本地 Dashboard JWT")
        token = jwt.encode(payload, jwt_secret, algorithm="HS256")
        return token.decode("utf-8") if isinstance(token, bytes) else token

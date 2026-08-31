# dashboard_client.py

import datetime
import os
from typing import Any

import aiohttp
import jwt

from astrbot.api import logger
from astrbot.core.star.context import Context


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

    async def check_astrbot_update(self) -> dict[str, Any]:
        """检查 AstrBot 框架是否有可用更新。"""
        body = await self._request("GET", self.core_update_check_url)
        data = body.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Dashboard 返回了无效的框架更新检查结果")
        return {**data, "message": str(body.get("message") or "")}

    async def start_astrbot_update(
        self, *, proxy: str = "", reboot: bool = False
    ) -> str:
        """启动 AstrBot 框架更新并返回 Dashboard 进度任务 ID。"""
        body = await self._request(
            "POST",
            self.core_update_url,
            json={"version": "latest", "proxy": proxy or None, "reboot": reboot},
        )
        data = body.get("data")
        progress_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(progress_id, str) or not progress_id:
            raise RuntimeError("Dashboard 未返回框架更新任务 ID")
        return progress_id

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

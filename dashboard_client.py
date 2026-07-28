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

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any] | None:
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
            if body.get("status") != "ok":
                raise RuntimeError(
                    f"业务错误: {body.get('message') or body.get('msg')}"
                )
            return body.get("data")

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

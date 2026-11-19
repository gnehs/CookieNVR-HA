"""Async client for the CookieNVR HTTP API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import REQUEST_TIMEOUT, SSE_READ_TIMEOUT

type Json = dict[str, Any]


class CookieNVRError(Exception):
    """Base error."""


class CookieNVRConnectionError(CookieNVRError):
    """The NVR could not be reached."""


class CookieNVRAuthError(CookieNVRError):
    """The token was rejected."""


class CookieNVRApiError(CookieNVRError):
    """The NVR answered with an HTTP error."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


def normalize_url(raw: str) -> str:
    """Return `scheme://host[:port]` with no trailing slash."""
    value = raw.strip()
    if "://" not in value:
        value = f"http://{value}"
    return value.rstrip("/")


class CookieNVRClient:
    """Thin wrapper around the endpoints the integration uses."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        self._session = session
        self.base_url = normalize_url(base_url)
        self._token = token

    @property
    def token(self) -> str:
        return self._token

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def token_url(self, path: str) -> str:
        """A URL that carries the token in the query, for clients that cannot send headers."""
        return f"{self.base_url}{path}?token={quote(self._token, safe='')}"

    async def _raise_for_status(self, resp: aiohttp.ClientResponse) -> None:
        if resp.status < 400:
            return
        message = resp.reason or ""
        try:
            body = await resp.json(content_type=None)
            if isinstance(body, dict) and isinstance(body.get("error"), str):
                message = body["error"]
        except (aiohttp.ClientError, ValueError):
            pass
        if resp.status == 401:
            raise CookieNVRAuthError(message)
        raise CookieNVRApiError(resp.status, message)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: Json | None = None,
        params: dict[str, Any] | None = None,
        auth: bool = True,
        raw: bool = False,
        timeout: float = REQUEST_TIMEOUT,
    ) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers if auth else None,
                json=body,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                await self._raise_for_status(resp)
                if raw:
                    return await resp.read()
                if resp.status == 204:
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            if isinstance(err, aiohttp.ClientResponseError) and err.status == 401:
                raise CookieNVRAuthError(str(err)) from err
            raise CookieNVRConnectionError(str(err) or type(err).__name__) from err

    async def info(self) -> Json:
        return await self._request("GET", "/api/info", auth=False)

    async def create_link(self, app: str) -> Json:
        """Ask the NVR for a pending link request: a short code for the user, a poll secret for us."""
        return await self._request("POST", "/api/link", body={"app": app}, auth=False)

    async def poll_link(self, poll_secret: str) -> Json:
        return await self._request("POST", "/api/link/poll", body={"pollSecret": poll_secret}, auth=False)

    async def me(self) -> Json:
        return await self._request("GET", "/api/me")

    async def cameras(self) -> list[Json]:
        return await self._request("GET", "/api/cameras")

    async def system(self) -> Json:
        return await self._request("GET", "/api/system")

    async def events(self, start_ms: int, limit: int = 5000) -> list[Json]:
        return await self._request("GET", "/api/events", params={"start": start_ms, "limit": limit})

    async def snapshot(self, camera_id: str) -> bytes | None:
        try:
            return await self._request("GET", f"/api/cameras/{quote(camera_id, safe='')}/snapshot.jpg", raw=True)
        except CookieNVRApiError as err:
            if err.status in (404, 503):
                return None
            raise

    async def event_thumb(self, event_id: int) -> bytes | None:
        try:
            return await self._request("GET", f"/api/events/{event_id}/thumb.jpg", raw=True)
        except CookieNVRApiError as err:
            if err.status == 404:
                return None
            raise

    async def ptz_info(self, camera_id: str) -> Json:
        return await self._request("GET", f"/api/cameras/{quote(camera_id, safe='')}/ptz", timeout=20)

    async def ptz_command(self, camera_id: str, command: Json) -> Json | None:
        return await self._request("POST", f"/api/cameras/{quote(camera_id, safe='')}/ptz", body=command)

    async def ptz_save_preset(self, camera_id: str, name: str, token: str | None) -> Json:
        body: Json = {"name": name}
        if token is not None:
            body["token"] = token
        return await self._request("POST", f"/api/cameras/{quote(camera_id, safe='')}/ptz/presets", body=body)

    async def ptz_remove_preset(self, camera_id: str, token: str) -> Json:
        return await self._request(
            "DELETE", f"/api/cameras/{quote(camera_id, safe='')}/ptz/presets", body={"token": token}
        )

    async def put_camera(self, camera_id: str, config: Json) -> Json:
        return await self._request("PUT", f"/api/cameras/{quote(camera_id, safe='')}", body=config)

    async def create_export(self, camera_id: str, start_ms: int, end_ms: int, tz: str) -> Json:
        return await self._request(
            "POST", "/api/exports", body={"cameraId": camera_id, "startMs": start_ms, "endMs": end_ms, "tz": tz}
        )

    async def get_export(self, export_id: str) -> Json:
        return await self._request("GET", f"/api/exports/{quote(export_id, safe='')}")

    async def delete_export(self, export_id: str) -> None:
        await self._request("DELETE", f"/api/exports/{quote(export_id, safe='')}")

    async def download_export(self, export_id: str, dest: Path) -> None:
        loop = asyncio.get_running_loop()
        try:
            async with self._session.get(
                f"{self.base_url}/api/exports/{quote(export_id, safe='')}/file",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=None, sock_read=60),
            ) as resp:
                await self._raise_for_status(resp)
                handle = await loop.run_in_executor(None, dest.open, "wb")
                try:
                    async for chunk in resp.content.iter_chunked(1 << 20):
                        await loop.run_in_executor(None, handle.write, chunk)
                finally:
                    await loop.run_in_executor(None, handle.close)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CookieNVRConnectionError(str(err) or type(err).__name__) from err

    async def stream(self) -> AsyncIterator[tuple[str, Any]]:
        """Yield `(event, data)` pairs from `GET /api/stream` until the connection ends."""
        try:
            async with self._session.get(
                f"{self.base_url}/api/stream",
                headers={**self._headers, "Accept": "text/event-stream"},
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=REQUEST_TIMEOUT, sock_read=SSE_READ_TIMEOUT),
            ) as resp:
                await self._raise_for_status(resp)
                event = "message"
                data: list[str] = []
                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
                    if line == "":
                        if data:
                            try:
                                yield event, json.loads("\n".join(data))
                            except ValueError:
                                pass
                        event, data = "message", []
                    elif line.startswith(":"):
                        continue
                    elif line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip(" "))
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CookieNVRConnectionError(str(err) or type(err).__name__) from err

"""A small in-process CookieNVR that speaks the subset of the HTTP API the integration uses."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
import time
from copy import deepcopy
from typing import Any

from aiohttp import web

TOKEN = "cnvr_testtoken"
INSTANCE_ID = "6f1c2a9e-3b4d-4e5f-8a7b-9c0d1e2f3a4b"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"
THUMB = b"\xff\xd8\xff\xe0fake-thumb\xff\xd9"
MP4 = b"\x00\x00\x00\x18ftypmp42fake-clip-bytes"


def make_camera(camera_id: str, name: str, position: int = 0, **config: Any) -> dict[str, Any]:
    cfg = {
        "name": name,
        "enabled": True,
        "connection": {"type": "tapo", "host": "192.168.1.50", "username": "cam-user", "password": "cam-secret"},
        "record": {"enabled": True, "audio": False, "direct": False},
    }
    cfg.update(config)
    return {
        "id": camera_id,
        "position": position,
        "config": cfg,
        "online": True,
        "recording": True,
        "rtsp": {"main": f"rtsp://nvr.local:8554/{camera_id}/main", "sub": f"rtsp://nvr.local:8554/{camera_id}/sub"},
    }


def make_event(event_id: int, camera_id: str, start: int, end: int | None = None, labels=(), thumb=True):
    return {
        "id": event_id,
        "cameraId": camera_id,
        "source": "pixel",
        "startMs": start,
        "endMs": end,
        "score": 0.42,
        "labels": list(labels),
        "hasThumb": thumb,
    }


def make_state_sensor(sensor_id: str, camera_id: str, name: str, classes=("closed", "moving", "open"), **fields: Any):
    sensor = {
        "id": sensor_id,
        "cameraId": camera_id,
        "config": {
            "name": name,
            "classes": list(classes),
            "crop": [0.1, 0.1, 0.9, 0.9],
            "threshold": 0.8,
            "intervalSec": 10,
            "onMotion": True,
            "enabled": True,
        },
        "state": None,
        "since": None,
        "score": None,
        "candidate": None,
        "problem": None,
        "createdAt": 1,
    }
    sensor.update(fields)
    return sensor


PTZ_FULL = {
    "supported": True,
    "continuous": True,
    "relative": True,
    "zoom": True,
    "maxPresets": 8,
    "presets": [{"token": "1", "name": "Door"}, {"token": "2", "name": "Gate"}],
    "prefs": {"mode": "auto", "speed": 0.5},
}
PTZ_NONE = {"supported": False, "reason": "no-onvif"}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeNVR:
    def __init__(self) -> None:
        self.port = free_port()
        self.info: dict[str, Any] = {
            "version": "1.2.3",
            "apiVersion": 1,
            "needsSetup": False,
            "siteName": "Home NVR",
            "instanceId": INSTANCE_ID,
        }
        self.token = TOKEN
        self.role = "admin"
        self.username = "alice"
        self.cameras: list[dict[str, Any]] = [
            make_camera("cam1", "Front Door", 0),
            make_camera("cam2", "Garage", 1),
        ]
        self.events: list[dict[str, Any]] = []
        self.state_sensors: list[dict[str, Any]] = []
        self.legacy = False  # an NVR from before state classification: no route, no snapshot field
        self.ptz: dict[str, dict[str, Any]] = {"cam1": deepcopy(PTZ_FULL), "cam2": deepcopy(PTZ_NONE)}
        self.system = {
            "workers": [
                {"id": "restream", "kind": "restream", "state": "running", "unstable": False},
                {"id": "record:cam1", "kind": "record", "state": "running", "unstable": False},
                {"id": "record:cam2", "kind": "record", "state": "backoff", "unstable": True},
            ],
            "disk": {"path": "/data", "totalBytes": 1000 * 1024**3, "freeBytes": 400 * 1024**3},
            "usage": [
                {"cameraId": "cam1", "bytes": 10 * 1024**3, "oldestMs": 1},
                {"cameraId": "cam2", "bytes": 5 * 1024**3, "oldestMs": 1},
            ],
            "logs": [],
        }
        self.requests: list[tuple[str, str, Any]] = []
        self.export_job: dict[str, Any] | None = None
        self.export_state = "done"
        self.snapshot_ok = True
        self.stream_status = 200
        self._streams: list[asyncio.Queue[bytes | None]] = []
        self._runner: web.AppRunner | None = None
        # Link requests: code -> {"secret", "status", "expiresAt"}; status follows the NVR's state machine.
        self.links: dict[str, dict[str, Any]] = {}
        self.link_interval = 0.1
        self.link_ttl_ms = 600_000
        self.link_create_status = 201
        self.link_token = "cnvr_linkedtoken"
        self.polls = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # ---- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        app = web.Application()
        r = app.router
        r.add_get("/api/info", self._info)
        r.add_get("/api/me", self._me)
        r.add_get("/api/cameras", self._cameras)
        r.add_put("/api/cameras/{id}", self._put_camera)
        r.add_get("/api/cameras/{id}/snapshot.jpg", self._snapshot)
        r.add_get("/api/cameras/{id}/ptz", self._ptz_get)
        r.add_post("/api/cameras/{id}/ptz", self._ptz_post)
        r.add_post("/api/cameras/{id}/ptz/presets", self._preset_save)
        r.add_delete("/api/cameras/{id}/ptz/presets", self._preset_remove)
        r.add_get("/api/events", self._events)
        r.add_get("/api/events/{id}/thumb.jpg", self._thumb)
        r.add_get("/api/states", self._states)
        r.add_get("/api/system", self._system)
        r.add_post("/api/exports", self._export_create)
        r.add_get("/api/exports/{id}", self._export_get)
        r.add_get("/api/exports/{id}/file", self._export_file)
        r.add_delete("/api/exports/{id}", self._export_delete)
        r.add_get("/api/stream", self._stream)
        r.add_post("/api/link", self._link_create)
        r.add_post("/api/link/poll", self._link_poll)
        r.add_post("/api/link/proof", self._link_proof)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()

    async def stop(self) -> None:
        self.drop_streams()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    # ---- helpers for tests ---------------------------------------------

    def drop_streams(self) -> None:
        for q in self._streams:
            q.put_nowait(None)

    @property
    def stream_count(self) -> int:
        return len(self._streams)

    def push(self, kind: str, data: Any) -> None:
        frame = f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode()
        for q in self._streams:
            q.put_nowait(frame)

    def snapshot(self) -> dict[str, Any]:
        admin = self.role == "admin"
        snapshot = {
            "cameras": self.cameras,
            "workers": self.system["workers"] if admin else None,
            "events": [e for e in self.events if e["endMs"] is None],
        }
        if not self.legacy:
            snapshot["stateSensors"] = self.state_sensors
        return snapshot

    def calls(self, method: str, suffix: str) -> list[Any]:
        return [body for m, path, body in self.requests if m == method and path.endswith(suffix)]

    def decide_link(self, status: str, code: str | None = None) -> None:
        """Act as the person in the CookieNVR approval page: `approved`, `denied` or `expired`."""
        link = self.links[code] if code else next(reversed(self.links.values()))
        assert link["status"] == "pending"
        link["status"] = status

    # ---- handlers ------------------------------------------------------

    async def _link_create(self, request: web.Request) -> web.Response:
        body = await self._record(request)
        if self.link_create_status != 201:
            return web.json_response({"error": "too many"}, status=self.link_create_status)
        code = f"BCDF-{len(self.links):04d}".replace("0", "G")
        secret = f"secret-{len(self.links)}"
        self.links[code] = {"secret": secret, "status": "pending", "app": body["app"]}
        return web.json_response(
            {
                "code": code,
                "approveUrl": f"{self.url}/#/link/{code}",
                "pollSecret": secret,
                "expiresAt": int(time.time() * 1000) + self.link_ttl_ms,
                "expiresIn": self.link_ttl_ms / 1000,
                "interval": self.link_interval,
            },
            status=201,
        )

    async def _link_proof(self, request: web.Request) -> web.Response:
        body = await request.json()
        key = hashlib.sha256(self.token.encode()).hexdigest()
        if body.get("lookup") != hashlib.sha256(f"cnvr-proof-id:{key}".encode()).hexdigest():
            return web.json_response({"error": "找不到這個連線"}, status=404)
        message = f"{body['nonce']}|{self.info.get('instanceId')}".encode()
        return web.json_response({"proof": hmac.new(key.encode(), message, hashlib.sha256).hexdigest()})

    async def _link_poll(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.polls += 1
        link = next((x for x in self.links.values() if x["secret"] == body.get("pollSecret")), None)
        if link is None:
            return web.json_response({"error": "找不到這個連結請求"}, status=404)
        if link["status"] == "approved":
            link["status"] = "claimed"
            self.token = self.link_token
            return web.json_response(
                {"status": "approved", "token": self.link_token, "username": self.username, "role": self.role}
            )
        return web.json_response({"status": link["status"]})

    def _auth(self, request: web.Request) -> None:
        header = request.headers.get("Authorization", "")
        supplied = header.removeprefix("Bearer ") if header.startswith("Bearer ") else request.query.get("token")
        if supplied != self.token:
            raise web.HTTPUnauthorized(text=json.dumps({"error": "unauthorized"}), content_type="application/json")

    def _admin(self) -> None:
        if self.role != "admin":
            raise web.HTTPForbidden(text=json.dumps({"error": "forbidden"}), content_type="application/json")

    async def _record(self, request: web.Request) -> Any:
        body = None
        if request.can_read_body:
            body = await request.json()
        self.requests.append((request.method, request.path, body))
        return body

    async def _info(self, request: web.Request) -> web.Response:
        return web.json_response(self.info)

    async def _me(self, request: web.Request) -> web.Response:
        self._auth(request)
        return web.json_response({"id": 1, "username": self.username, "role": self.role})

    async def _cameras(self, request: web.Request) -> web.Response:
        self._auth(request)
        return web.json_response(self.cameras)

    def _find(self, request: web.Request) -> dict[str, Any]:
        for cam in self.cameras:
            if cam["id"] == request.match_info["id"]:
                return cam
        raise web.HTTPNotFound(text=json.dumps({"error": "找不到攝影機"}), content_type="application/json")

    async def _put_camera(self, request: web.Request) -> web.Response:
        self._auth(request)
        self._admin()
        body = await self._record(request)
        cam = self._find(request)
        cam["config"] = body
        self.push("cameras", self.cameras)
        return web.json_response(cam)

    async def _snapshot(self, request: web.Request) -> web.Response:
        self._auth(request)
        self._find(request)
        if not self.snapshot_ok:
            return web.json_response({"error": "目前取不到畫面"}, status=503)
        return web.Response(body=JPEG, content_type="image/jpeg")

    async def _ptz_get(self, request: web.Request) -> web.Response:
        self._auth(request)
        return web.json_response(self.ptz[self._find(request)["id"]])

    async def _ptz_post(self, request: web.Request) -> web.Response:
        self._auth(request)
        body = await self._record(request)
        info = self.ptz[self._find(request)["id"]]
        if not info["supported"]:
            return web.json_response({"error": "這台攝影機不支援 PTZ"}, status=409)
        if body["action"] == "move":
            return web.json_response({"mode": body.get("mode", "relative")})
        return web.Response(status=204)

    async def _preset_save(self, request: web.Request) -> web.Response:
        self._auth(request)
        self._admin()
        body = await self._record(request)
        info = self.ptz[self._find(request)["id"]]
        token = body.get("token") or str(len(info["presets"]) + 10)
        info["presets"] = [p for p in info["presets"] if p["token"] != token] + [{"token": token, "name": body["name"]}]
        return web.json_response({"presets": info["presets"], "token": token}, status=200 if body.get("token") else 201)

    async def _preset_remove(self, request: web.Request) -> web.Response:
        self._auth(request)
        self._admin()
        body = await self._record(request)
        info = self.ptz[self._find(request)["id"]]
        info["presets"] = [p for p in info["presets"] if p["token"] != body["token"]]
        return web.json_response({"presets": info["presets"]})

    async def _events(self, request: web.Request) -> web.Response:
        self._auth(request)
        start = int(request.query.get("start", 0))
        return web.json_response([e for e in self.events if (e["endMs"] or 10**15) > start])

    async def _thumb(self, request: web.Request) -> web.Response:
        self._auth(request)
        self.requests.append(("GET", request.path, None))
        return web.Response(body=THUMB, content_type="image/jpeg")

    async def _states(self, request: web.Request) -> web.Response:
        self._auth(request)
        if self.legacy:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(self.state_sensors)

    async def _system(self, request: web.Request) -> web.Response:
        self._auth(request)
        self._admin()
        return web.json_response(self.system)

    async def _export_create(self, request: web.Request) -> web.Response:
        self._auth(request)
        body = await self._record(request)
        self.export_job = {
            "id": "exp1",
            "cameraId": body["cameraId"],
            "state": self.export_state,
            "error": "沒有錄影" if self.export_state == "failed" else None,
            "fileName": "Front Door_20260101-120000.mp4",
        }
        return web.json_response(self.export_job, status=201)

    async def _export_get(self, request: web.Request) -> web.Response:
        self._auth(request)
        assert self.export_job
        return web.json_response(self.export_job)

    async def _export_file(self, request: web.Request) -> web.Response:
        self._auth(request)
        return web.Response(body=MP4, content_type="video/mp4")

    async def _export_delete(self, request: web.Request) -> web.Response:
        self._auth(request)
        self.requests.append(("DELETE", request.path, None))
        return web.Response(status=204)

    async def _stream(self, request: web.Request) -> web.StreamResponse:
        self._auth(request)
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store"})
        await resp.prepare(request)
        q: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._streams.append(q)
        try:
            await resp.write(b"retry: 3000\n\n")
            await resp.write(f"event: snapshot\ndata: {json.dumps(self.snapshot())}\n\n".encode())
            await resp.write(b": ping\n\n")
            while True:
                try:
                    frame = await asyncio.wait_for(q.get(), 0.05)
                except TimeoutError:
                    if request.transport is None or request.transport.is_closing():
                        break
                    continue
                if frame is None:
                    break
                await resp.write(frame)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._streams.remove(q)
        return resp

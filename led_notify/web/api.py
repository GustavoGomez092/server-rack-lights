"""HTTP API + live stream for the web UI and webhooks."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..audio.library import SoundError
from ..audio.refs import CUSTOM_PREFIX
from ..events import Event
from ..patterns import LOOK_KEYS, PatternError, catalog, resolve
from ..store.settings import SettingsError


@dataclass
class Services:
    arbiter: Any
    settings: Any
    devices: Any
    supervisor: Any
    link: Any
    hub: Any
    clock: Any
    audio: Any = None


class LookIn(BaseModel):
    pattern: str | None = None
    effect: int | None = None
    color: list[int] | None = None

    def spec(self) -> dict:
        spec = {k: getattr(self, k) for k in LOOK_KEYS if getattr(self, k) is not None}
        if spec:
            try:
                resolve(spec)
            except PatternError as e:
                raise HTTPException(422, str(e))
        return spec


class EventIn(LookIn):
    name: str
    kind: Literal["oneshot", "start", "clear"] = "oneshot"
    priority: Literal["low", "normal", "high", "critical"] | None = None
    duration: float | None = None
    data: dict = Field(default_factory=dict)
    sound: str | None = None


class PreviewIn(LookIn):
    duration: float = 8.0
    sound: str | None = None


class SoundPlayIn(BaseModel):
    sound: str


class StripIn(LookIn):
    power: bool | None = None
    brightness: int | None = Field(default=None, ge=0, le=255)
    speed: int | None = Field(default=None, ge=1, le=10)


def create_app(svc: Services, lifespan=None) -> FastAPI:
    app = FastAPI(title="led-notify", lifespan=lifespan)

    def require_token(authorization: str | None = Header(default=None)) -> None:
        token = svc.settings.data["webhook_token"]
        if token and authorization != f"Bearer {token}":
            raise HTTPException(401, "missing or invalid bearer token")

    def state() -> dict:
        warnings = [w for w in (svc.settings.warning, svc.devices.warning) if w]
        return {**svc.arbiter.snapshot(), "strip": svc.link.status(),
                "monitors": svc.supervisor.health if svc.supervisor else {}, "warnings": warnings,
                "audio": svc.audio.status() if svc.audio else None}

    def make_event(**kwargs) -> Event:
        try:
            return Event(**kwargs)
        except ValueError as e:
            raise HTTPException(422, str(e))

    async def submit(ev: Event) -> dict:
        return {"accepted": True, "outcome": await svc.arbiter.submit(ev)}

    def check_sound(ref: str | None) -> None:
        if ref in (None, ""):
            return
        if svc.audio is None or not svc.audio.library.exists(ref):
            raise HTTPException(422, f"unknown sound {ref!r}")

    @app.post("/api/events", dependencies=[Depends(require_token)])
    async def post_event(body: EventIn):
        spec = body.spec()
        check_sound(body.sound)
        return await submit(make_event(name=body.name, kind=body.kind, source="webhook", priority=body.priority,
                                       look=spec or None, duration_s=body.duration, data=body.data,
                                       sound=body.sound))

    @app.post("/api/test/{name}")
    async def test_event(name: str, force: bool = False):
        if svc.settings.rule(name) is None:
            raise HTTPException(404, f"no rule named {name!r}")
        return await submit(make_event(name=name, kind="oneshot", source="test", duration_s=8, force=force))

    @app.post("/api/preview")
    async def preview(body: PreviewIn):
        spec = body.spec()
        if not spec:
            raise HTTPException(422, "give one of pattern, effect, color")
        check_sound(body.sound)
        return await submit(make_event(name="preview", kind="oneshot", source="test", look=spec,
                                       duration_s=body.duration, force=True, sound=body.sound or ""))

    @app.get("/api/state")
    async def get_state():
        return state()

    @app.post("/api/strip")
    async def manual(body: StripIn):
        spec = body.spec()
        if spec.get("effect") is not None and body.speed is not None:
            spec["speed"] = body.speed
        try:
            svc.arbiter.set_manual(spec or None, brightness=body.brightness, power=body.power is not False)
        except PatternError as e:
            raise HTTPException(422, str(e))
        return state()

    @app.delete("/api/strip/manual")
    async def resume():
        svc.arbiter.clear_manual()
        return state()

    @app.get("/api/rules")
    async def get_rules():
        return svc.settings.data["rules"]

    @app.put("/api/rules")
    async def put_rules(body: dict = Body(...)):
        for rule in body.values() if isinstance(body, dict) else []:
            if isinstance(rule, dict) and isinstance(rule.get("sound"), str) and rule["sound"].startswith(CUSTOM_PREFIX):
                check_sound(rule["sound"])
        try:
            svc.settings.update({"rules": body})
        except SettingsError as e:
            raise HTTPException(422, str(e))
        return svc.settings.data["rules"]

    @app.get("/api/settings")
    async def get_settings():
        return {k: v for k, v in svc.settings.public().items() if k != "rules"}

    @app.put("/api/settings")
    async def put_settings(body: dict = Body(...)):
        if "rules" in body:
            raise HTTPException(422, "edit rules with PUT /api/rules")
        try:
            svc.settings.update(body)
        except SettingsError as e:
            raise HTTPException(422, str(e))
        return await get_settings()

    @app.get("/api/devices")
    async def get_devices():
        now = svc.clock.now().timestamp()
        window = 3 * svc.settings.data["devices"]["interval_s"]
        return [{**d, "online": now - d.get("last_seen_ts", 0) <= window} for d in svc.devices.all()]

    @app.patch("/api/devices/{mac}")
    async def patch_device(mac: str, body: dict = Body(...)):
        try:
            return svc.devices.patch(mac, body)
        except KeyError:
            raise HTTPException(404, "unknown device")
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/devices/{mac}")
    async def delete_device(mac: str):
        if not svc.devices.delete(mac):
            raise HTTPException(404, "unknown device")
        return {"deleted": mac.lower()}

    @app.get("/api/patterns")
    async def patterns():
        return catalog()

    @app.get("/api/log")
    async def get_log():
        return list(svc.arbiter.log)

    @app.get("/api/sounds")
    async def list_sounds():
        return svc.audio.library.list() if svc.audio else []

    @app.put("/api/sounds/{name}")
    async def upload_sound(name: str, request: Request):
        if svc.audio is None:
            raise HTTPException(404, "audio is not available")
        data = await request.body()
        try:
            return svc.audio.library.import_upload(name, data, request.headers.get("content-type", ""))
        except SoundError as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/sounds/{name}")
    async def delete_sound(name: str):
        if svc.audio is None or not svc.audio.library.delete(name):
            raise HTTPException(404, "unknown sound")
        ref = CUSTOM_PREFIX + name
        reset = sorted(n for n, r in svc.settings.data["rules"].items() if r.get("sound") == ref)
        if reset:
            svc.settings.update({"rules": {n: {"sound": "default"} for n in reset}})
        return {"deleted": name, "rules_reset": reset}

    @app.post("/api/sounds/play")
    async def play_sound(body: SoundPlayIn):
        if svc.audio is None or not svc.audio.library.exists(body.sound):
            raise HTTPException(404, "unknown sound")
        svc.audio.chime(body.sound, svc.settings.data["audio"]["volume_pct"])
        return {"playing": body.sound}

    @app.get("/api/stream")
    async def stream(request: Request):
        q = svc.hub.subscribe()

        async def events():
            try:
                yield f"event: state\ndata: {json.dumps(state(), default=str)}\n\n"
                while not await request.is_disconnected():
                    try:
                        type_, data = await asyncio.wait_for(q.get(), 15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if type_ == "settings":
                        continue
                    payload = state() if type_ == "state" else data
                    yield f"event: {type_}\ndata: {json.dumps(payload, default=str)}\n\n"
            finally:
                svc.hub.unsubscribe(q)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
    return app

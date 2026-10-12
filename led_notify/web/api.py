"""HTTP API + live stream for the web UI and webhooks."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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


class PreviewIn(LookIn):
    duration: float = 8.0


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
                "monitors": svc.supervisor.health if svc.supervisor else {}, "warnings": warnings}

    def make_event(**kwargs) -> Event:
        try:
            return Event(**kwargs)
        except ValueError as e:
            raise HTTPException(422, str(e))

    async def submit(ev: Event) -> dict:
        return {"accepted": True, "outcome": await svc.arbiter.submit(ev)}

    @app.post("/api/events", dependencies=[Depends(require_token)])
    async def post_event(body: EventIn):
        spec = body.spec()
        return await submit(make_event(name=body.name, kind=body.kind, source="webhook", priority=body.priority,
                                       look=spec or None, duration_s=body.duration, data=body.data))

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
        return await submit(make_event(name="preview", kind="oneshot", source="test", look=spec,
                                       duration_s=body.duration, force=True))

    @app.get("/api/state")
    def get_state():
        return state()

    @app.post("/api/strip")
    def manual(body: StripIn):
        spec = body.spec()
        if spec.get("effect") is not None and body.speed is not None:
            spec["speed"] = body.speed
        try:
            svc.arbiter.set_manual(spec or None, brightness=body.brightness, power=body.power is not False)
        except PatternError as e:
            raise HTTPException(422, str(e))
        return state()

    @app.delete("/api/strip/manual")
    def resume():
        svc.arbiter.clear_manual()
        return state()

    @app.get("/api/rules")
    def get_rules():
        return svc.settings.data["rules"]

    @app.put("/api/rules")
    def put_rules(body: dict = Body(...)):
        try:
            svc.settings.update({"rules": body})
        except SettingsError as e:
            raise HTTPException(422, str(e))
        return svc.settings.data["rules"]

    @app.get("/api/settings")
    def get_settings():
        return {k: v for k, v in svc.settings.public().items() if k != "rules"}

    @app.put("/api/settings")
    def put_settings(body: dict = Body(...)):
        if "rules" in body:
            raise HTTPException(422, "edit rules with PUT /api/rules")
        try:
            svc.settings.update(body)
        except SettingsError as e:
            raise HTTPException(422, str(e))
        return get_settings()

    @app.get("/api/devices")
    def get_devices():
        now = svc.clock.now().timestamp()
        window = 3 * svc.settings.data["devices"]["interval_s"]
        return [{**d, "online": now - d.get("last_seen_ts", 0) <= window} for d in svc.devices.all()]

    @app.patch("/api/devices/{mac}")
    def patch_device(mac: str, body: dict = Body(...)):
        try:
            return svc.devices.patch(mac, body)
        except KeyError:
            raise HTTPException(404, "unknown device")
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/devices/{mac}")
    def delete_device(mac: str):
        if not svc.devices.delete(mac):
            raise HTTPException(404, "unknown device")
        return {"deleted": mac.lower()}

    @app.get("/api/patterns")
    def patterns():
        return catalog()

    @app.get("/api/log")
    def get_log():
        return list(svc.arbiter.log)

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

    return app

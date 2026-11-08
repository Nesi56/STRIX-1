"""REST API for STRIX-1 (all routes under ``/api``)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from app import __version__
from app.api.auth import COOKIE_NAME, AuthManager
from app.core.logging_setup import ring_handler
from app.core.orientation import CalibrationError
from app.models.schemas import (
    AgentRunRequest,
    CalibrationRequest,
    CameraConfigRequest,
    DemoSceneRequest,
    LoginRequest,
    MemoryAimRequest,
    PromptsRequest,
    RecordingStartRequest,
    RecordingUpdateRequest,
    ServoAngleRequest,
    ServoLimitsRequest,
    ServoNudgeRequest,
    ServoSpeedRequest,
    VORequest,
    WatchRequest,
)
from app.services.agent_service import AgentBusy
from app.services.camera_service import CameraError, list_media, safe_media_path
from app.services.container import Container
from app.services.detector import COCO80
from app.services.detector_service import DetectorError
from app.services.recording_service import RecordingError
from app.services.servo_service import ServoError

log = logging.getLogger(__name__)
_STARTED = time.time()


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_auth(request: Request) -> AuthManager:
    return request.app.state.auth


def require_auth(request: Request) -> None:
    get_auth(request).require(request)


def err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


# ============================================================== public
@public.get("/health")
async def health(request: Request) -> dict[str, Any]:
    c = get_container(request)
    return {"status": "ok", "service": "strix-1", "version": __version__, "server_time": time.time(),
            "uptime_s": time.time() - _STARTED, "demo_mode": c.telemetry.demo_mode()}


@public.get("/auth/status")
async def auth_status(request: Request) -> dict[str, Any]:
    a = get_auth(request)
    return {"auth_enabled": a.enabled, "authenticated": a.is_authenticated(request.headers, request.cookies)}


@public.post("/auth/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    a = get_auth(request)
    client = request.client.host if request.client else "?"
    if not a.login_allowed(client):
        raise err(429, "rate_limited", "too many login attempts; wait a minute")
    if a.enabled and not a.check_token(body.token.strip()):
        raise err(401, "invalid_token", "invalid access token")
    response.set_cookie(COOKIE_NAME, a.make_cookie(), max_age=a.settings.session_ttl_hours * 3600, httponly=True,
                        samesite="strict", secure=request.url.scheme == "https", path="/")
    return {"authenticated": True}


@public.post("/auth/logout")
async def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"authenticated": False}


# ============================================================== system
@router.get("/system")
async def system(request: Request) -> dict[str, Any]:
    c = get_container(request)
    return {**c.system.latest(), "websocket_clients": len(c.telemetry.manager.clients),
            "db_bytes": c.db.size_bytes(), "version": __version__}


@router.get("/config")
async def config(request: Request) -> dict[str, Any]:
    """Non-secret configuration summary (no tokens or keys)."""
    s = get_container(request).settings
    return {
        "mock": s.mock, "host": s.host, "port": s.port, "telemetry_hz": s.telemetry_hz, "auth_enabled": s.auth_enabled,
        "imu": {"backend": s.imu_backend, "address": hex(s.imu_address), "rate_hz": s.imu_rate_hz,
                "accel_range_g": s.imu_accel_range_g, "gyro_range_dps": s.imu_gyro_range_dps, "dlpf_hz": s.imu_dlpf_hz,
                "filter_alpha": s.imu_filter_alpha, "axis_map": s.imu_axis_map},
        "dht": {"backend": s.dht_backend, "gpio": s.dht_gpio, "interval_s": s.dht_interval_s},
        "servo": {"backend": s.servo_backend, "gpio": s.servo_gpio, "pulse_us": [s.servo_min_pulse_us, s.servo_max_pulse_us],
                  "up_is_positive": s.servo_up_is_positive},
        "camera": {"backend": s.camera_backend, "width": s.camera_width, "height": s.camera_height, "fps": s.camera_fps,
                   "jpeg_quality": s.camera_jpeg_quality, "webrtc_enabled": s.webrtc_enabled},
        "recording": {"sample_hz": s.recording_sample_hz, "retention_days": s.recording_retention_days,
                      "max_db_mb": s.recording_max_db_mb},
        "qwen": {"enabled": s.qwen_enabled, "server_url": s.qwen_server_url, "manage_server": s.qwen_manage_server,
                 "model_id": s.qwen_model_id, "ctx_size": s.qwen_ctx_size, "threads": s.qwen_threads},
        "detector": {"backend": s.detector_backend, "model": s.detector_model or "auto (models/*.onnx)",
                     "conf": s.detector_conf, "hz": s.detector_hz, "hfov_deg": s.camera_hfov_deg},
        "agent": {"max_plan_steps": s.agent_max_plan_steps, "skill_timeout_s": s.agent_skill_timeout_s,
                  "standoff_m": s.agent_standoff_m},
        "gpio_pins_bcm": {"i2c_sda": 2, "i2c_scl": 3, "dht11_data": s.dht_gpio, "servo_signal": s.servo_gpio},
    }


@router.get("/events")
async def events(request: Request, limit: int = Query(100, ge=1, le=300)) -> list[dict[str, Any]]:
    return get_container(request).events.recent(limit)


@router.get("/logs")
async def logs(limit: int = Query(200, ge=1, le=500)) -> list[dict[str, Any]]:
    return list(ring_handler.records)[-limit:]


# ============================================================= sensors
@router.get("/sensors")
async def sensors(request: Request) -> dict[str, Any]:
    c = get_container(request)
    return {
        "ts": time.time(),
        "demo_mode": c.telemetry.demo_mode(),
        "imu": {"status": c.imu.status.to_dict(), "latest": c.imu.latest()},
        "environment": {"status": c.dht.status.to_dict(), "latest": c.dht.latest()},
        "devices": {d.name: d.to_dict() for d in (c.imu.status, c.dht.status, c.servo.status, c.camera.status)},
    }


@router.get("/imu")
async def imu(request: Request) -> dict[str, Any]:
    return get_container(request).imu.summary()


@router.get("/imu/history")
async def imu_history(request: Request, seconds: float = Query(60, gt=0, le=3600),
                      max_points: int = Query(600, ge=10, le=5000)) -> dict[str, Any]:
    c = get_container(request)
    return {"seconds": seconds, "rows": c.imu.history(seconds, max_points)}


@router.post("/imu/calibrate")
async def imu_calibrate(body: CalibrationRequest, request: Request) -> dict[str, Any]:
    try:
        return await get_container(request).imu.calibrate(body.duration_s, body.level)
    except CalibrationError as exc:
        raise err(409, "calibration_failed", str(exc)) from exc


@router.get("/imu/calibration")
async def imu_calibration(request: Request) -> dict[str, Any]:
    return get_container(request).imu.calibration_status()


@router.delete("/imu/calibration")
async def imu_calibration_clear(request: Request) -> dict[str, Any]:
    c = get_container(request)
    c.imu.clear_calibration()
    return c.imu.calibration_status()


@router.post("/imu/reset-yaw")
async def imu_reset_yaw(request: Request) -> dict[str, Any]:
    c = get_container(request)
    c.imu.reset_yaw()
    return {"ok": True, "ts": time.time()}


@router.get("/environment")
async def environment(request: Request) -> dict[str, Any]:
    c = get_container(request)
    return {**c.dht.summary(), "trend_5min": c.dht.trend(300)}


@router.get("/environment/history")
async def environment_history(request: Request, seconds: float = Query(3600, gt=0, le=86400)) -> dict[str, Any]:
    return {"seconds": seconds, "rows": get_container(request).dht.history(seconds)}


@router.post("/environment/reset-extremes")
async def environment_reset(request: Request) -> dict[str, Any]:
    c = get_container(request)
    c.dht.reset_session_extremes()
    return c.dht.summary()


# ============================================================== camera
@router.get("/camera/status")
async def camera_status(request: Request) -> dict[str, Any]:
    return get_container(request).camera.camera_status()


@router.get("/camera/stream.mjpg")
async def camera_stream(request: Request, fps: float | None = Query(None, ge=1, le=60)):
    cam = get_container(request).camera
    if not cam.available:
        raise err(503, "camera_unavailable", cam.status.error or "camera unavailable")
    boundary = "strixframe"
    min_interval = 1.0 / fps if fps else 0.0

    async def gen():
        seq = 0
        last = 0.0
        while cam.available:
            seq, frame = await cam.hub.wait_newer(seq, timeout=2.0)
            if frame is None:
                continue
            now = time.monotonic()
            if min_interval and now - last < min_interval * 0.9:
                continue
            last = now
            head = (f"--{boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame.jpeg)}\r\n"
                    f"X-Frame-Seq: {frame.seq}\r\nX-Capture-Time: {frame.capture_ts:.6f}\r\n"
                    f"X-Synthetic: {int(frame.synthetic)}\r\n\r\n").encode()
            yield head + frame.jpeg + b"\r\n"

    return StreamingResponse(gen(), media_type=f"multipart/x-mixed-replace; boundary={boundary}",
                             headers={"Cache-Control": "no-store, no-cache", "X-Accel-Buffering": "no"})


@router.get("/camera/frame.jpg")
async def camera_frame(request: Request) -> Response:
    cam = get_container(request).camera
    _, frame = cam.hub.latest()
    if frame is None:
        raise err(503, "no_frame", "no frame available")
    return Response(frame.jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store", "X-Capture-Time": f"{frame.capture_ts:.6f}",
                             "X-Synthetic": str(int(frame.synthetic))})


@router.post("/camera/snapshot")
async def camera_snapshot(request: Request) -> dict[str, Any]:
    c = get_container(request)
    try:
        snap = await asyncio.to_thread(c.camera.snapshot)
    except CameraError as exc:
        raise err(503, "camera_unavailable", str(exc)) from exc
    c.events.add("info", "camera", f"snapshot saved: {snap['id']}")
    snap.pop("path", None)
    return snap


@router.get("/camera/snapshots")
async def camera_snapshots(request: Request) -> list[dict[str, Any]]:
    return list_media(get_container(request).camera.snap_dir, (".jpg", ".jpeg"))


@router.get("/camera/snapshots/{name}")
async def camera_snapshot_file(name: str, request: Request):
    try:
        return FileResponse(safe_media_path(get_container(request).camera.snap_dir, name), media_type="image/jpeg")
    except CameraError as exc:
        raise err(404, "not_found", str(exc)) from exc


@router.delete("/camera/snapshots/{name}")
async def camera_snapshot_delete(name: str, request: Request) -> dict[str, Any]:
    try:
        safe_media_path(get_container(request).camera.snap_dir, name).unlink()
    except CameraError as exc:
        raise err(404, "not_found", str(exc)) from exc
    return {"deleted": name}


@router.post("/camera/recording/start")
async def camera_recording_start(request: Request) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(get_container(request).camera.start_recording)
    except CameraError as exc:
        raise err(409, "recording_error", str(exc)) from exc


@router.post("/camera/recording/stop")
async def camera_recording_stop(request: Request) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(get_container(request).camera.stop_recording)
    except CameraError as exc:
        raise err(409, "recording_error", str(exc)) from exc


@router.get("/camera/videos")
async def camera_videos(request: Request) -> list[dict[str, Any]]:
    return list_media(get_container(request).camera.video_dir, (".mp4", ".h264", ".avi", ".mkv"))


@router.get("/camera/videos/{name}")
async def camera_video_file(name: str, request: Request):
    try:
        path = safe_media_path(get_container(request).camera.video_dir, name)
    except CameraError as exc:
        raise err(404, "not_found", str(exc)) from exc
    return FileResponse(path, filename=name)


@router.delete("/camera/videos/{name}")
async def camera_video_delete(name: str, request: Request) -> dict[str, Any]:
    c = get_container(request)
    rec = c.camera.recording
    if rec.active and rec.path and rec.path.name == name:
        raise err(409, "recording_active", "stop the recording before deleting it")
    try:
        safe_media_path(c.camera.video_dir, name).unlink()
    except CameraError as exc:
        raise err(404, "not_found", str(exc)) from exc
    return {"deleted": name}


@router.post("/camera/config")
async def camera_config(body: CameraConfigRequest, request: Request) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(get_container(request).camera.reconfigure, body.width, body.height, body.fps)
    except CameraError as exc:
        raise err(409, "camera_config", str(exc)) from exc


@router.post("/camera/webrtc/whep")
async def camera_whep(request: Request) -> Response:
    """Proxy a WHEP SDP offer to the local MediaMTX instance (experimental)."""
    s = get_container(request).settings
    if not s.webrtc_enabled:
        raise err(404, "webrtc_disabled", "WebRTC is disabled (STRIX_WEBRTC_ENABLED=false)")
    sdp = await request.body()
    if not sdp or len(sdp) > 64_000:
        raise err(400, "bad_sdp", "missing or oversized SDP offer")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(s.mediamtx_whep_url, content=sdp, headers={"Content-Type": "application/sdp"})
    except httpx.HTTPError as exc:
        raise err(502, "mediamtx_unreachable", f"MediaMTX not reachable: {exc.__class__.__name__}") from exc
    return Response(r.content, status_code=r.status_code, media_type="application/sdp")


# =============================================================== servo
def _servo_call(fn, *args):
    try:
        return fn(*args)
    except ServoError as exc:
        raise err(409, "servo_error", str(exc)) from exc


@router.get("/servo")
async def servo_state(request: Request) -> dict[str, Any]:
    return get_container(request).servo.state()


@router.post("/servo/angle")
async def servo_angle(body: ServoAngleRequest, request: Request) -> dict[str, Any]:
    return _servo_call(get_container(request).servo.set_target, body.angle)


@router.post("/servo/center")
async def servo_center(request: Request) -> dict[str, Any]:
    return _servo_call(get_container(request).servo.center)


@router.post("/servo/arm")
async def servo_arm(request: Request) -> dict[str, Any]:
    return _servo_call(get_container(request).servo.arm)


@router.post("/servo/disarm")
async def servo_disarm(request: Request) -> dict[str, Any]:
    return _servo_call(get_container(request).servo.disarm)


@router.post("/servo/nudge")
async def servo_nudge(body: ServoNudgeRequest, request: Request) -> dict[str, Any]:
    return _servo_call(get_container(request).servo.nudge, body.up_deg)


@router.post("/servo/speed")
async def servo_speed(body: ServoSpeedRequest, request: Request) -> dict[str, Any]:
    return _servo_call(get_container(request).servo.set_speed, body.max_speed_dps)


@router.post("/servo/limits")
async def servo_limits(body: ServoLimitsRequest, request: Request) -> dict[str, Any]:
    return _servo_call(get_container(request).servo.update_limits, body.min_angle, body.max_angle, body.max_speed_dps,
                       body.center_angle)


# ============================================================ safety
@router.post("/estop")
async def emergency_stop(request: Request) -> dict[str, Any]:
    """Disable camera-servo control and cancel any running agent task."""
    c = get_container(request)
    c.servo.emergency_stop()
    cancelled = c.agent.stop("EMERGENCY STOP")
    c.events.add("error", "operator", "EMERGENCY STOP: servo control disabled" + (", agent cancelled" if cancelled else ""))
    return {"servo": c.servo.state(), "agent": c.agent.snapshot(), "cancelled": cancelled}


@router.post("/session/reset")
async def reset_session(request: Request) -> dict[str, Any]:
    """Cancel the agent, clear the chat, zero relative yaw and reset environment min/max."""
    c = get_container(request)
    c.agent.stop("session reset")
    await asyncio.to_thread(c.agent.clear_history)
    c.imu.reset_yaw()
    c.dht.reset_session_extremes()
    c.memory.clear()
    c.agent.state.update({"status": "idle", "node": None, "plan": [], "task": None, "step": None, "error": None})
    c.events.add("info", "operator", "session reset")
    return {"ok": True}


# ========================================================= detector
@router.get("/detector")
async def detector_state(request: Request) -> dict[str, Any]:
    c = get_container(request)
    return {**c.detector.state(), "latest": c.detector.latest()}


@router.get("/detector/classes")
async def detector_classes(request: Request) -> dict[str, Any]:
    c = get_container(request)
    return {"classes": c.detector.vocabulary, "coco80": COCO80}


@router.post("/detector/prompts")
async def detector_prompts(body: PromptsRequest, request: Request) -> dict[str, Any]:
    c = get_container(request)
    try:
        prompts = c.detector.set_prompts(body.prompts)
    except DetectorError as exc:
        raise err(422, "unknown_class", str(exc)) from exc
    return {"prompts": prompts}


# ============================================================ object memory
@router.get("/memory")
async def memory_state(request: Request) -> dict[str, Any]:
    """Seen / remembered / hidden objects with out-of-sight timers, in a drone-centred frame."""
    return get_container(request).memory.snapshot()


@router.post("/memory/clear")
async def memory_clear(request: Request) -> dict[str, Any]:
    c = get_container(request)
    c.memory.clear()
    return c.memory.snapshot()


@router.post("/memory/aim")
async def memory_aim(body: MemoryAimRequest, request: Request) -> dict[str, Any]:
    """Aim the camera (tilt servo) at a remembered object and return yaw/distance advice for the pilot.
    The aircraft is never moved."""
    c = get_container(request)
    try:
        c.servo.ensure_armed()
        out = c.memory.aim_at(body.target)
    except KeyError as exc:
        raise err(404, "not_in_memory", str(exc.args[0])) from exc
    except ServoError as exc:
        raise err(409, "servo_error", str(exc)) from exc
    c.events.add("info", "memory", f"camera aimed at remembered {out['target']} ({out['state']}); " + " ".join(out["guidance"]))
    return out


@router.get("/demo/scene")
async def demo_scene(request: Request) -> dict[str, Any]:
    return {"scene": get_container(request).camera.scene(), "available": ["outdoor", "cups"]}


@router.post("/demo/scene")
async def set_demo_scene(body: DemoSceneRequest, request: Request) -> dict[str, Any]:
    """Synthetic demo only: switch between the outdoor scene and the cups (shell game) scene."""
    try:
        return get_container(request).set_demo_scene(body.scene)
    except CameraError as exc:
        raise err(409, "not_synthetic", str(exc)) from exc


# ============================================================ agent / Qwen
@router.get("/ai/status")
async def ai_status(request: Request) -> dict[str, Any]:
    return get_container(request).qwen.status()


@router.get("/agent")
async def agent_state(request: Request) -> dict[str, Any]:
    return get_container(request).agent.snapshot()


@router.post("/agent/run")
async def agent_run(body: AgentRunRequest, request: Request):
    """Give Qwen a task. Streams NDJSON events: run, node, plan, step, progress, observation,
    suggestions, delta/replace, done | stopped | error."""
    c = get_container(request)
    if c.agent.running:
        raise err(409, "agent_busy", "the agent is already running a task — stop it first")

    async def gen():
        try:
            async for ev in c.agent.run(body.task.strip()):
                yield json.dumps(ev, default=str) + "\n"
        except AgentBusy as exc:
            yield json.dumps({"type": "error", "code": "agent_busy", "message": str(exc)}) + "\n"
        except Exception as exc:  # never let an AI failure break the API
            log.exception("agent run failed")
            yield json.dumps({"type": "error", "code": "internal", "message": str(exc)}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})


@router.post("/agent/stop")
async def agent_stop(request: Request) -> dict[str, Any]:
    c = get_container(request)
    return {"cancelled": c.agent.stop("stopped by operator"), "agent": c.agent.snapshot()}


@router.post("/agent/watch")
async def agent_watch(body: WatchRequest, request: Request) -> dict[str, Any]:
    """Turn automatic Qwen comments on new YOLO detections on/off."""
    return get_container(request).agent.set_watch(body.enabled)


@router.get("/agent/history")
async def agent_history(request: Request, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    return await asyncio.to_thread(get_container(request).agent.history, limit)


@router.delete("/agent/history")
async def agent_history_clear(request: Request) -> dict[str, Any]:
    await asyncio.to_thread(get_container(request).agent.clear_history)
    return {"cleared": True}


@router.post("/ai/qwen/{action}")
async def ai_qwen_control(action: str, request: Request) -> dict[str, Any]:
    c = get_container(request)
    if not c.settings.qwen_manage_server:
        raise err(409, "not_managed", "llama-server is managed externally (QWEN_MANAGE_SERVER=false)")
    if action == "start":
        return c.qwen.start_server()
    if action == "stop":
        await asyncio.to_thread(c.qwen.stop_server)
        return c.qwen.status()
    if action == "restart":
        return await c.qwen.restart_server()
    raise err(404, "unknown_action", f"unknown action {action}")


# ============================================================= mapping
@router.get("/mapping")
async def mapping(request: Request) -> dict[str, Any]:
    return get_container(request).mapping.state()


@router.post("/mapping/vo")
async def mapping_vo(body: VORequest, request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(get_container(request).mapping.set_vo, body.enabled)


@router.post("/mapping/vo/reset")
async def mapping_vo_reset(request: Request) -> dict[str, Any]:
    return get_container(request).mapping.reset_vo()


# ========================================================== recordings
def _rec_call(fn, *args):
    try:
        return fn(*args)
    except RecordingError as exc:
        status = 404 if "not found" in str(exc) else 409
        raise err(status, "recording_error", str(exc)) from exc


@router.post("/record/start")
async def record_start(request: Request, body: RecordingStartRequest | None = None) -> dict[str, Any]:
    """One button: start a telemetry session and, if the camera is available, a video recording."""
    c = get_container(request)
    body = body or RecordingStartRequest()
    try:
        st = await c.recording.start_session(body.name or time.strftime("Flight %Y-%m-%d %H:%M:%S"), body.notes)
    except RecordingError as exc:
        raise err(409, "recording_error", str(exc)) from exc
    sid = st["session"]["id"]
    video, video_error = None, None
    if c.camera.available:
        try:
            if not c.camera.recording.active:
                await asyncio.to_thread(c.camera.start_recording)
            video = c.camera.recording_status()["file"]
            c.recording.set_metadata(sid, "video", video)
        except CameraError as exc:
            video_error = str(exc)
    else:
        video_error = "camera unavailable"
    return {"session_id": sid, "video": video, "video_error": video_error, "status": c.recording.status()}


@router.post("/record/stop")
async def record_stop(request: Request) -> dict[str, Any]:
    c = get_container(request)
    video = None
    if c.camera.recording.active:
        try:
            video = (await asyncio.to_thread(c.camera.stop_recording))["file"]
        except CameraError:
            pass
    if c.recording.active_session:
        res = await c.recording.stop_session()
    else:
        res = c.recording.status()
    return {**res, "video": video}


@router.get("/recordings")
async def recordings(request: Request) -> dict[str, Any]:
    c = get_container(request)
    sessions = await asyncio.to_thread(c.recording.list_sessions)
    return {"status": c.recording.status(), "sessions": sessions,
            "videos": list_media(c.camera.video_dir, (".mp4", ".h264", ".avi", ".mkv")),
            "snapshots": list_media(c.camera.snap_dir, (".jpg", ".jpeg"))}


@router.get("/recordings/status")
async def recordings_status(request: Request) -> dict[str, Any]:
    return get_container(request).recording.status()


@router.post("/recordings/start")
async def recordings_start(body: RecordingStartRequest, request: Request) -> dict[str, Any]:
    c = get_container(request)
    try:
        return await c.recording.start_session(body.name, body.notes)
    except RecordingError as exc:
        raise err(409, "recording_error", str(exc)) from exc


@router.post("/recordings/stop")
async def recordings_stop(request: Request) -> dict[str, Any]:
    try:
        return await get_container(request).recording.stop_session()
    except RecordingError as exc:
        raise err(409, "recording_error", str(exc)) from exc


@router.post("/recordings/retention")
async def recordings_retention(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(get_container(request).recording.apply_retention)


@router.get("/recordings/{session_id}")
async def recording_get(session_id: int, request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(_rec_call, get_container(request).recording.get_session, session_id)


@router.patch("/recordings/{session_id}")
async def recording_update(session_id: int, body: RecordingUpdateRequest, request: Request) -> dict[str, Any]:
    return _rec_call(get_container(request).recording.update_session, session_id, body.name, body.notes)


@router.delete("/recordings/{session_id}")
async def recording_delete(session_id: int, request: Request) -> dict[str, Any]:
    await asyncio.to_thread(_rec_call, get_container(request).recording.delete_session, session_id)
    return {"deleted": session_id}


@router.get("/recordings/{session_id}/telemetry")
async def recording_telemetry(session_id: int, request: Request, start: float | None = None, end: float | None = None,
                              max_points: int = Query(2000, ge=10, le=20000)) -> dict[str, Any]:
    return await asyncio.to_thread(_rec_call, get_container(request).recording.telemetry, session_id, start, end, max_points)


@router.get("/recordings/{session_id}/export.csv")
async def recording_export_csv(session_id: int, request: Request):
    rec = get_container(request).recording
    _rec_call(rec.get_session, session_id)
    return StreamingResponse(rec.export_csv(session_id), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="strix1_session_{session_id}.csv"'})


@router.get("/recordings/{session_id}/export.json")
async def recording_export_json(session_id: int, request: Request):
    rec = get_container(request).recording
    _rec_call(rec.get_session, session_id)
    return StreamingResponse(rec.export_json(session_id), media_type="application/json",
                             headers={"Content-Disposition": f'attachment; filename="strix1_session_{session_id}.json"'})

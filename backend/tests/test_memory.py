"""Object memory: seen / remembered / hidden, containment, re-identification, the shell-game demo and the API."""

import math

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app
from app.services.agent_service import rule_plan
from app.services.ai_context import remembered_targets
from app.services.camera_service import CUP_SLOTS, MockCameraBackend, cups_scene
from app.services.detector import COCO80, estimate_geometry, parse_size_priors
from app.services.memory_service import ObjectMemory, deroll, to_world
from tests.conftest import TOKEN, make_settings, wait_for

H, V = 51.0, 35.0  # half FOV used by the unit tests


def det(label, az, el, w=6.0, h=7.0, r=1.0, conf=0.9):
    return {"label": label, "confidence": conf, "az": az, "el": el, "w_deg": w, "h_deg": h, "range_m": r, "range_kind": "estimate"}


def feed(mem, frames, t0=0.0, dt=0.25, cam=(0.0, 0.0)):
    t = t0
    for dets in frames:
        mem.update(dets, cam[0], cam[1], H, V, t)
        t += dt
    return t


def by_label(mem, label):
    return [o for o in mem.snapshot(1e9, 0, 0, H, V) if o["label"] == label]


# ------------------------------------------------------------------ unit: ObjectMemory
def test_ball_hidden_under_cup_moves_with_it_and_is_revealed():
    mem = ObjectMemory()
    cup, ball = (0.0, -20.0), (0.0, -24.0)
    t = feed(mem, [[det("cup", *cup), det("sports ball", *ball, w=3, h=3)]] * 3)
    assert {o["state"] for o in mem.snapshot(t, 0, 0, H, V)} == {"visible"}
    t = feed(mem, [[det("cup", *cup)]] * 3, t0=t)  # ball vanishes where the cup is
    snap = {o["label"]: o for o in mem.snapshot(t, 0, 0, H, V)}
    assert snap["sports ball"]["state"] == "hidden" and snap["sports ball"]["memory"] == "hidden"
    assert snap["sports ball"]["contained_by_name"] == snap["cup"]["name"]
    assert snap["cup"]["contains"] == [snap["sports ball"]["id"]]
    # the cup slides 20° left in small steps: the hidden ball follows
    for i in range(1, 11):
        t = feed(mem, [[det("cup", -2.0 * i, -20.0)]], t0=t)
    ball_o = by_label(mem, "sports ball")[0]
    assert ball_o["state"] == "hidden" and ball_o["az"] == pytest.approx(-20.0, abs=1.0)
    assert any(e["kind"] == "carried" for e in mem.events)
    # the cup is lifted and the ball is there
    t = feed(mem, [[det("cup", -20.0, -12.0), det("sports ball", -20.0, -24.0, w=3, h=3)]], t0=t)
    ball_o = by_label(mem, "sports ball")[0]
    assert ball_o["state"] == "visible"
    assert any(e["kind"] == "revealed" and "memory was right" in e["text"] for e in mem.events)


def test_out_of_view_is_remembered_with_timer_and_reseen():
    mem = ObjectMemory()
    t = feed(mem, [[det("person", 10.0, 0.0, r=4.0)]] * 3)
    # the camera tilts up 60°: the person is out of view, not "lost"
    t = feed(mem, [[]] * 8, t0=t, cam=(0.0, 60.0))
    o = mem.snapshot(t, 0.0, 60.0, H, V)[0]
    assert o["state"] == "out_of_view" and o["memory"] == "remembered"
    assert o["out_of_sight_s"] == pytest.approx(2.25, abs=0.06)
    assert o["projected_bbox"] is None  # not inside the current view
    # look back: re-seen, same id
    t = feed(mem, [[]], t0=t + 2.0)
    t = feed(mem, [[det("person", 10.5, 0.2, r=4.0)]], t0=t)
    o2 = mem.snapshot(t, 0, 0, H, V)[0]
    assert o2["id"] == o["id"] and o2["state"] == "visible" and o2["out_of_sight_s"] == 0.0
    assert any(e["kind"] == "reseen" for e in mem.events)


def test_occluded_in_view_without_container_has_projected_box():
    mem = ObjectMemory()
    t = feed(mem, [[det("person", 12.0, 3.0)]] * 3)
    t = feed(mem, [[]] * 4, t0=t)
    o = mem.snapshot(t, 0, 0, H, V)[0]
    assert o["state"] == "occluded" and o["contained_by"] is None
    x, y, w, h = o["projected_bbox"]
    assert 0.5 < x + w / 2 < 0.65 and 0.4 < y + h / 2 < 0.5  # right of and slightly above center


def test_relocation_corrects_wrong_memory():
    mem = ObjectMemory()
    t = feed(mem, [[det("cup", 0, -20), det("sports ball", 0, -24, w=3, h=3)]] * 3)
    t = feed(mem, [[det("cup", 0, -20)]] * 3, t0=t)
    assert by_label(mem, "sports ball")[0]["state"] == "hidden"
    t = feed(mem, [[det("cup", 0, -20), det("sports ball", 25, -24, w=3, h=3)]], t0=t)
    balls = by_label(mem, "sports ball")
    assert len(balls) == 1 and balls[0]["state"] == "visible" and balls[0]["relocated_by_deg"] > 18
    assert any(e["kind"] == "relocated" for e in mem.events)


def test_lifted_container_without_object_drops_containment():
    mem = ObjectMemory()
    t = feed(mem, [[det("cup", 0, -20), det("sports ball", 0, -24, w=3, h=3)]] * 3)
    t = feed(mem, [[det("cup", 0, -20)]] * 3, t0=t)
    for i in range(1, 13):  # the cup is lifted (and held up), nothing underneath
        t = feed(mem, [[det("cup", 0, -20 + min(i, 8))]], t0=t)
    assert by_label(mem, "sports ball")[0]["state"] == "occluded"
    assert any(e["kind"] == "not_there" for e in mem.events)


def test_unconfirmed_tracks_and_ttl():
    mem = ObjectMemory(ttl_s=30.0)
    feed(mem, [[det("dog", 5, 0)]])  # one-frame flicker
    feed(mem, [[]] * 6, t0=0.25)
    assert mem.tracks == {}
    t = feed(mem, [[det("dog", 5, 0)]] * 3, t0=10)
    mem.update([], 0, 90, H, V, t + 31)
    assert mem.tracks == {} and mem.events[-1]["kind"] == "forgot"


def test_find_by_label_and_id():
    mem = ObjectMemory()
    feed(mem, [[det("cup", 0, -20), det("cup", 20, -20)]] * 3)
    ids = sorted(mem.tracks)
    assert mem.find(f"#{ids[1]}").id == ids[1]
    assert mem.find("cup") is not None and mem.find("dog") is None and mem.find("#999") is None


def test_deroll_inverts_image_rotation():
    # rolling right by r: a level point at bearing b appears at (b cos r, b sin r)
    r = 15.0
    b_img, e_img = 20 * math.cos(math.radians(r)), 20 * math.sin(math.radians(r))
    b, e = deroll(b_img, e_img, r)
    assert b == pytest.approx(20.0, abs=1e-6) and e == pytest.approx(0.0, abs=1e-6)


def test_size_prior_overrides():
    assert parse_size_priors("sports ball=0.067, cup = 0.11, unicorn=1, car=abc") == {"sports ball": 0.067, "cup": 0.11}


# ------------------------------------------------------------------ the synthetic shell game
def _render_dets(cam, s, t, roll, look_up, yaw):
    cam._frame_cups(t, roll, look_up, yaw)
    w, h, gt = cam._last_gt
    out = []
    for label, conf, (x, y, bw, bh) in gt:
        dist, kind, b, e = estimate_geometry(label, (x, y, bw, bh), w, h, s.camera_hfov_deg, cam.size_priors())
        out.append({"label": label, "confidence": conf, "bbox": [x / w, y / h, bw / w, bh / h], "distance_m": dist,
                    "distance_kind": kind, "bearing_deg": b, "elevation_deg": e})
    return out


@pytest.mark.parametrize("hz", [2.0, 4.0])
def test_shell_game_memory_follows_the_right_cup(hz):
    s = Settings(_env_file=None, mock=True, mock_scene="cups", camera_width=960, camera_height=540)
    cam = MockCameraBackend(s, lambda f: None)
    half_h = s.camera_hfov_deg / 2
    half_v = math.degrees(math.atan(math.tan(math.radians(half_h)) * 540 / 960))
    mem = ObjectMemory()
    t = 0.0
    hidden_cup_at_29 = None
    while t < 33.0:
        roll = 2.0 * math.sin(0.3 * t)
        pitch = 1.2 * math.sin(0.21 * t + 1)
        yaw = 10 * math.sin(0.05 * t + 0.5)
        tilt = 45.0 if 20 <= t < 26 else 0.0  # look away while the ball is hidden
        dets = _render_dets(cam, s, t, roll, pitch + tilt, yaw)
        mem.update([to_world(d, yaw, pitch + tilt, roll, half_h, half_v) for d in dets], yaw, pitch + tilt, half_h, half_v, t)
        if 22 <= t < 22 + 1 / hz:
            snap = mem.snapshot(t, yaw, pitch + tilt, half_h, half_v)
            assert all(o["state"] == "out_of_view" for o in snap if o["label"] == "cup")
        if 29 <= t < 29 + 1 / hz:
            snap = {o["id"]: o for o in mem.snapshot(t, yaw, pitch + tilt, half_h, half_v)}
            ball = next(o for o in snap.values() if o["label"] == "sports ball")
            assert ball["state"] == "hidden" and ball["hidden_for_s"] > 15
            hidden_cup_at_29 = snap[ball["contained_by"]]
        t += 1 / hz
    # after two swaps the ball's cup (B) is in the left slot
    sc = cups_scene(29.0)
    assert sc["cups"][1][0] == pytest.approx(CUP_SLOTS[0])
    assert hidden_cup_at_29["az"] < -8
    assert sum(1 for tr in mem.tracks.values() if tr.label == "cup") == 3
    assert any(e["kind"] == "revealed" and "memory was right" in e["text"] for e in mem.events)
    assert not any(e["kind"] == "relocated" for e in mem.events)


def test_cups_scene_roll_does_not_move_objects_in_memory():
    s = Settings(_env_file=None, mock=True, mock_scene="cups", camera_width=960, camera_height=540)
    cam = MockCameraBackend(s, lambda f: None)
    half_h = s.camera_hfov_deg / 2
    half_v = math.degrees(math.atan(math.tan(math.radians(half_h)) * 540 / 960))
    pos = {}
    for roll in (0.0, 12.0, -12.0):
        dets = [to_world(d, 0.0, 0.0, roll, half_h, half_v) for d in _render_dets(cam, s, 1.0, roll, 0.0, 0.0)]
        pos[roll] = sorted((d["label"], round(d["az"], 1), round(d["el"], 1)) for d in dets)
    for roll in (12.0, -12.0):
        for a, b in zip(pos[0.0], pos[roll]):
            assert a[0] == b[0] and abs(a[1] - b[1]) < 1.5 and abs(a[2] - b[2]) < 1.5


# ------------------------------------------------------------------ planner
def test_rule_plan_goto_remembered():
    V = list(COCO80)
    assert rule_plan("where is the ball?", V, 0, ["sports ball"])[1] == [{"skill": "goto", "target": "sports ball"}]
    assert rule_plan("where is the ball?", V, 0, [])[1] == [{"skill": "find", "target": "sports ball"}]
    assert rule_plan("send it to the remembered target", V, 0, ["sports ball", "cup"])[1] == [{"skill": "goto", "target": "sports ball"}]
    assert rule_plan("aim at #4", V, 0, [])[1] == [{"skill": "goto", "target": "#4"}]
    assert rule_plan("what do you remember?", V, 0, ["sports ball"])[1] == []
    assert rule_plan("look at the floor", V, 0, ["sports ball"])[1] == [{"skill": "look", "tilt": -45.0}]
    assert rule_plan("aim it up", V, 0, ["sports ball"])[1] == [{"skill": "tilt", "delta": 15.0}]


def test_remembered_targets_order():
    objs = [
        {"label": "cup", "memory": "remembered", "container": True, "last_seen": 5},
        {"label": "person", "memory": "remembered", "container": False, "last_seen": 3},
        {"label": "sports ball", "memory": "hidden", "container": False, "last_seen": 1},
        {"label": "dog", "memory": "seen", "container": False, "last_seen": 9},
    ]
    assert remembered_targets({"memory": {"objects": objs}}) == ["sports ball", "person", "cup"]


# ------------------------------------------------------------------ API + agent (synthetic cups scene)
@pytest.fixture
def cups_client(tmp_path):
    app = create_app(make_settings(tmp_path, mock_scene="cups"))
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        wait_for(lambda: c.get("/api/imu").json().get("latest") is not None)
        yield c


def test_memory_api_and_demo_scene(cups_client):
    c = cups_client
    assert c.get("/api/demo/scene").json()["scene"]["scene"] == "cups"
    assert "sports ball" in c.get("/api/detector").json()["prompts"]
    wait_for(lambda: c.get("/api/memory").json()["counts"]["seen"] >= 4, timeout=8)
    m = c.get("/api/memory").json()
    labels = sorted(o["label"] for o in m["objects"])
    assert labels == ["cup", "cup", "cup", "sports ball"]
    ball = next(o for o in m["objects"] if o["label"] == "sports ball")
    assert ball["range_m"] == pytest.approx(0.58, rel=0.3)  # synthetic table-top ball sized correctly
    assert set(m["camera"]) == {"az", "el", "half_h", "half_v"}
    assert "assumes the drone holds position" in m["frame"].lower()
    # AIM is an operator action: it turns servo control on, unless an emergency stop is latched
    c.post("/api/estop")
    assert c.post("/api/memory/aim", json={"target": "sports ball"}).status_code == 409
    c.post("/api/servo/arm")
    assert c.post("/api/memory/aim", json={"target": "giraffe"}).status_code == 404
    c.post("/api/servo/disarm")
    r = c.post("/api/memory/aim", json={"target": "sports ball"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"].startswith("sports ball #") and body["tilt_deg"] < -10 and body["guidance"]
    # switching scenes clears the memory and changes prompts
    r = c.post("/api/demo/scene", json={"scene": "outdoor"})
    assert r.status_code == 200 and r.json()["prompts"] == ["person", "car"]
    assert c.get("/api/memory").json()["objects"] == []
    assert c.post("/api/demo/scene", json={"scene": "moon"}).status_code == 422
    assert c.post("/api/memory/clear").status_code == 200


def test_agent_goes_to_remembered_ball(cups_client):
    import json

    c = cups_client
    wait_for(lambda: any(o["label"] == "sports ball" for o in c.get("/api/memory").json()["objects"]), timeout=8)
    c.post("/api/servo/arm")
    c.post("/api/servo/speed", json={"max_speed_dps": 360})
    c.post("/api/servo/nudge", json={"up_deg": 60})  # look away: everything goes out of view
    wait_for(lambda: c.get("/api/memory").json()["counts"]["seen"] == 0, timeout=8)
    ball = next(o for o in c.get("/api/memory").json()["objects"] if o["label"] == "sports ball")
    assert ball["memory"] in ("remembered", "hidden")
    r = c.post("/api/agent/run", json={"task": "where is the ball?"})
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    plan = next(e for e in events if e["type"] == "plan")
    assert plan["steps"][0]["text"] == "aim at the remembered sports ball"
    step = [e for e in events if e["type"] == "step" and e["status"] != "running"][-1]
    assert step["status"] == "done" and "aimed the camera at the sports ball I remembered" in step["text"]
    final = next(e for e in events if e["type"] == "replace")["text"]
    assert "sports ball" in final and "[" not in final


def test_cut_off_boxes_do_not_corrupt_range_or_position():
    mem = ObjectMemory()
    t = feed(mem, [[det("cup", 0, -20, r=0.75)]] * 3)
    cut = det("cup", 0, -14, h=3.0, r=1.6)
    cut["cut_v"] = True  # box clipped by the bottom of the frame while the camera tilts up
    t = feed(mem, [[cut]] * 2, t0=t)
    o = mem.snapshot(t, 0, 0, H, V)[0]
    assert o["range_m"] == pytest.approx(0.75, abs=0.01)
    assert o["el"] < -18 and o["h_deg"] == pytest.approx(7.0, abs=0.1)

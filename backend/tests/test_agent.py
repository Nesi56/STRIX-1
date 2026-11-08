import json
import threading
import time

import httpx
import pytest

from app.services.agent_service import rule_plan
from app.services.ai_context import SKILLS, suggestions
from app.services.detector import COCO80
from tests.conftest import wait_for


def sse(chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


class FakeLlama:
    """Simulated llama-server: JSON-schema plans (non-stream) and streamed reports."""

    def __init__(self, plan=None, report="[MEASURED] roll 2.0°. SUGGEST: hold position.", raw_plan_text=None):
        self.plan = plan if plan is not None else {"thought": "tilt up", "target": "", "plan": [{"skill": "tilt", "delta": 15}]}
        self.report = report
        self.raw_plan_text = raw_plan_text
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/props":
            return httpx.Response(200, json={"modalities": {"vision": False}, "default_generation_settings": {"n_ctx": 4096}})
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            self.requests.append(body)
            if not body.get("stream"):
                content = self.raw_plan_text if self.raw_plan_text is not None else json.dumps(self.plan)
                return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                                 "usage": {"prompt_tokens": 700, "completion_tokens": 40},
                                                 "timings": {"predicted_per_second": 7.5}})
            text = self.report
            parts = [{"choices": [{"delta": {"content": text[i:i + 9]}}]} for i in range(0, len(text), 9)]
            parts.append({"choices": [{"delta": {}}], "timings": {"predicted_per_second": 6.0, "predicted_n": 30}})
            return sse(parts)
        return httpx.Response(404)


def use_fake(client, fake):
    q = client.app.state.container.qwen
    q._client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    q.state = "ready"


def run(client, task):
    r = client.post("/api/agent/run", json={"task": task})
    assert r.status_code == 200, r.text
    return [json.loads(l) for l in r.text.splitlines() if l.strip()]


def final_text(evs):
    text = ""
    for e in evs:
        if e["type"] == "delta":
            text += e["text"]
        elif e["type"] == "replace":
            text = e["text"]
    return text


def by_type(evs, t):
    return [e for e in evs if e["type"] == t]


def test_qwen_plans_with_json_schema_and_moves_servo(client):
    fake = FakeLlama()
    use_fake(client, fake)
    client.post("/api/servo/arm")
    evs = run(client, "look up a bit")
    plan = by_type(evs, "plan")[0]
    assert plan["planner"] == "qwen" and plan["steps"][0]["skill"] == "tilt"
    steps = by_type(evs, "step")
    assert steps[-1]["status"] == "done"
    assert client.get("/api/servo").json()["tilt_deg"] == pytest.approx(15, abs=1)
    planner_req = fake.requests[0]
    assert planner_req["response_format"]["type"] == "json_object"
    assert planner_req["response_format"]["schema"]["properties"]["plan"]["items"]["properties"]["skill"]["enum"] == SKILLS
    assert "ONLY the camera tilt servo" in planner_req["messages"][0]["content"]
    assert "TASK: look up a bit" in planner_req["messages"][1]["content"]
    assert "SYNTHETIC DEMO DATA" in planner_req["messages"][1]["content"]
    assert "roll 2.0" in final_text(evs)
    done = evs[-1]
    assert done["type"] == "done" and done["planner"] == "qwen" and done["reporter"] == "qwen"
    nodes = [e["node"] for e in by_type(evs, "node")]
    assert nodes[0] == "generator" and {"observer", "pilot", "until", "report"} <= set(nodes)
    hist = client.get("/api/agent/history").json()
    assert hist[-1]["role"] == "assistant" and hist[-1]["provider"] == "qwen"


def test_plan_is_validated_and_clamped(client):
    fake = FakeLlama(plan={"thought": "x", "target": "unicorn", "plan": [
        {"skill": "look", "tilt": 999}, {"skill": "find", "target": "unicorn"}, {"skill": "track", "target": "people", "seconds": 500}]})
    use_fake(client, fake)
    client.post("/api/servo/arm")
    evs = run(client, "do things")
    steps = by_type(evs, "plan")[0]["steps"]
    assert steps[0]["skill"] == "look" and steps[0]["tilt"] == pytest.approx(60)  # clamped to the servo range
    assert [s["skill"] for s in steps] == ["look", "track"]  # unknown target dropped
    assert steps[1]["target"] == "person" and steps[1]["seconds"] == 30
    assert client.get("/api/servo").json()["target_angle"] <= 150


def test_servo_disabled_means_no_motion(client):
    use_fake(client, FakeLlama())
    before = client.get("/api/servo").json()
    evs = run(client, "look up a bit")
    step = by_type(evs, "step")[-1]
    assert step["status"] == "skipped" and "enable" in step["text"]
    after = client.get("/api/servo").json()
    assert after["armed"] is False and after["commanded_angle"] == before["commanded_angle"]


def test_rule_planner_when_qwen_offline(client):
    client.post("/api/servo/arm")
    evs = run(client, "look down 10 degrees")
    plan = by_type(evs, "plan")[0]
    assert plan["planner"] == "rules" and plan["steps"][0] == {**plan["steps"][0], "skill": "tilt", "delta": -10}
    assert client.get("/api/servo").json()["tilt_deg"] == pytest.approx(-10, abs=1)
    assert "rule-based" in final_text(evs) and evs[-1]["reporter"] == "rules"


def test_bad_model_json_falls_back_to_rules(client):
    use_fake(client, FakeLlama(raw_plan_text="sure! I will look up"))
    client.post("/api/servo/arm")
    evs = run(client, "look up 10 degrees")
    plan = by_type(evs, "plan")[0]
    assert plan["planner"] == "rules" and "bad_json" in plan["thought"]
    assert plan["steps"][0]["skill"] == "tilt"


def test_track_keeps_target_centered(client):
    client.post("/api/servo/arm")
    client.post("/api/servo/limits", json={"min_angle": 30, "max_angle": 150, "max_speed_dps": 120})
    evs = run(client, "track the person")
    res = by_type(evs, "step")[-1]
    assert res["status"] == "done" and "vertically centered" in res["text"]
    pct = int(res["text"].split("centered in ")[1].split("%")[0])
    assert pct >= 50
    sugg = by_type(evs, "suggestions")[0]["items"]
    assert any(s.startswith(("Yaw", "Heading")) for s in sugg)


def test_report_shell_commands_are_removed(client):
    use_fake(client, FakeLlama(plan={"thought": "q", "target": "", "plan": []},
                               report="OK.\n```bash\nsudo rm -rf /\n```\nSUGGEST: hold."))
    evs = run(client, "status?")
    text = final_text(evs)
    assert "sudo rm" not in text and "command block removed" in text


def test_emergency_stop_cancels_run_and_disarms(client):
    client.post("/api/servo/arm")
    client.post("/api/servo/limits", json={"min_angle": 30, "max_angle": 150, "max_speed_dps": 20})
    out = {}

    def go():
        out["evs"] = run(client, "find a dog")  # no dog in the synthetic scene → long scan

    t = threading.Thread(target=go)
    t.start()
    wait_for(lambda: client.get("/api/agent").json()["status"] == "running")
    time.sleep(0.5)
    assert client.post("/api/agent/run", json={"task": "x"}).status_code == 409  # busy
    r = client.post("/api/estop").json()
    assert r["cancelled"] is True and r["servo"]["armed"] is False
    t.join(10)
    assert out["evs"][-1]["type"] == "stopped" and "EMERGENCY" in out["evs"][-1]["reason"]
    assert client.get("/api/agent").json()["status"] == "stopped"


def test_session_reset_clears_chat(client):
    run(client, "what is the temperature?")
    assert client.get("/api/agent/history").json()
    assert client.post("/api/session/reset").json()["ok"] is True
    assert client.get("/api/agent/history").json() == []


def test_ai_status_is_qwen_only(client):
    st = client.get("/api/ai/status").json()
    assert st["provider"] == "qwen-local"
    assert client.get("/api/ai/models").status_code == 404
    assert client.post("/api/ai/chat", json={"message": "x"}).status_code in (404, 405)


@pytest.mark.parametrize("task,expected", [
    ("look up 20 degrees", [{"skill": "tilt", "delta": 20.0}]),
    ("tilt down", [{"skill": "tilt", "delta": -15}]),
    ("center the camera", [{"skill": "center"}]),
    ("scan the area", [{"skill": "scan"}]),
    ("where are the people?", [{"skill": "find", "target": "person"}]),
    ("follow the dog", [{"skill": "find", "target": "dog"}, {"skill": "track", "target": "dog", "seconds": 8}]),
    ("what's the humidity?", []),
])
def test_rule_plan(task, expected):
    assert rule_plan(task, COCO80)[1] == expected


def _obs(dets, armed=True, roll=0.0):
    return {"detections": dets, "prompts": ["person"], "servo": {"armed": armed},
            "imu": {"roll": roll, "pitch": 0.0}, "env": None, "system": {"warnings": []}}


def test_suggestions_are_geometric_and_advisory():
    d = {"label": "person", "confidence": 0.9, "distance_m": 5.0, "distance_kind": "estimate", "bearing_deg": 20.0, "elevation_deg": -12.0}
    s = suggestions(_obs([d], armed=False), "person", 1.5)
    assert s[0].startswith("Yaw right ~20°")
    assert any("Advance ~3.5 m" in x for x in s)
    assert any("Tilt the camera down ~12°" in x for x in s)
    s = suggestions(_obs([], roll=30), "car", 1.5)
    assert any("No car detected" in x for x in s) and any("Level the airframe" in x for x in s)
    near = dict(d, distance_m=1.2, bearing_deg=2.0)
    assert any("hold position" in x for x in suggestions(_obs([near]), "person", 1.5))

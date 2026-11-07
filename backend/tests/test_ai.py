import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.ratelimit import SlidingWindowLimiter
from app.main import create_app
from app.services.ai_grok_service import GrokService
from app.services.ai_qwen_service import LLMError
from app.services.hub import EventLog
from tests.conftest import TOKEN, make_settings, wait_for

GROK_KEY = "xai-SECRET-test-key-000"


def sse(chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def text_chunks(text, timings=None, usage=None):
    parts = [{"choices": [{"delta": {"content": text[i:i + 7]}}]} for i in range(0, len(text), 7)]
    final = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
    if timings:
        final["timings"] = timings
    if usage:
        final["usage"] = usage
    return parts + [final]


class FakeLlama:
    def __init__(self, text="[MEASURED] roll 2.0°, pitch 3.1° at 12:00:00.", vision=False):
        self.text = text
        self.vision = vision
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/props":
            return httpx.Response(200, json={"modalities": {"vision": self.vision}, "model_path": "/models/q.gguf",
                                             "default_generation_settings": {"n_ctx": 4096}})
        if request.url.path == "/v1/chat/completions":
            self.requests.append(json.loads(request.content))
            return sse(text_chunks(self.text, timings={"predicted_per_second": 6.5, "predicted_n": 20, "prompt_n": 900},
                                   usage={"prompt_tokens": 900, "completion_tokens": 20}))
        return httpx.Response(404)


def use_fake_llama(client, fake):
    q = client.app.state.container.qwen
    q._client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    q.state = "ready"
    q.vision_capable = fake.vision


def chat(client, **body):
    r = client.post("/api/ai/chat", json=body)
    assert r.status_code == 200, r.text
    return [json.loads(l) for l in r.text.splitlines() if l.strip()]


def final_text(events):
    text = ""
    for e in events:
        if e["type"] == "delta":
            text += e["text"]
        elif e["type"] == "replace":
            text = e["text"]
    return text


def test_local_chat_is_grounded_and_labeled(client):
    fake = FakeLlama()
    use_fake_llama(client, fake)
    ev = chat(client, message="What is the current pitch and roll?", mode="local")
    meta = ev[0]
    assert meta["type"] == "meta" and meta["local"] is True and meta["external"] is False
    assert meta["provider"] == "qwen-local" and meta["label"] == "Local Qwen"
    assert "roll 2.0" in final_text(ev)
    done = ev[-1]
    assert done["type"] == "done" and done["stats"]["tokens_per_s"] == 6.5 and done["stats"]["prompt_tokens"] == 900
    req = fake.requests[0]
    assert req["messages"][0]["role"] == "system" and "[MEASURED]" in req["messages"][0]["content"]
    user = req["messages"][-1]["content"]
    assert "TELEMETRY CONTEXT" in user and "SYNTHETIC DEMO DATA" in user and "Position unavailable" in user
    hist = client.get("/api/ai/history").json()
    assert hist[-1]["role"] == "assistant" and hist[-1]["provider"] == "qwen-local"
    assert client.delete("/api/ai/history").json()["cleared"] is True


def test_model_shell_commands_are_removed(client):
    fake = FakeLlama(text="Check wiring.\n```bash\nsudo rm -rf / --no-preserve-root\n```\nThen retry.")
    use_fake_llama(client, fake)
    ev = chat(client, message="How do I fix the IMU?", mode="local")
    text = final_text(ev)
    assert "sudo rm" not in text and "command block removed" in text
    assert ev[-1]["sanitized"] is True


def test_unavailable_local_model_falls_back_to_digest_not_cloud(client):
    ev = chat(client, message="Summarize the last 60 seconds", mode="local")
    kinds = [e["type"] for e in ev]
    assert "error" in kinds and "fallback" in kinds
    fb = next(e for e in ev if e["type"] == "fallback")
    assert fb["provider"] == "strix-digest" and "SYNTHETIC" in fb["text"]


def test_text_only_model_never_receives_images(client):
    fake = FakeLlama(vision=False)
    use_fake_llama(client, fake)
    wait_for(lambda: client.get("/api/camera/status").json()["frame_seq"] > 0)
    snap = client.post("/api/camera/snapshot").json()["id"]
    ev = chat(client, message="What does the camera currently show?", mode="local", snapshot_id=snap)
    assert ev[0]["image_sent"] is False
    assert any("text-only" in n for n in ev[0]["notes"])
    content = fake.requests[0]["messages"][-1]["content"]
    assert isinstance(content, str) and "DETECTOR (OpenCV" in content


def test_vision_model_receives_snapshot(client):
    fake = FakeLlama(vision=True)
    use_fake_llama(client, fake)
    wait_for(lambda: client.get("/api/camera/status").json()["frame_seq"] > 0)
    snap = client.post("/api/camera/snapshot").json()["id"]
    ev = chat(client, message="Describe the scene", mode="local", snapshot_id=snap)
    assert ev[0]["image_sent"] is True
    content = fake.requests[0]["messages"][-1]["content"]
    assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_unknown_snapshot_rejected(client):
    r = client.post("/api/ai/chat", json={"message": "x", "snapshot_id": "../../etc/passwd"})
    assert r.status_code == 404


# ------------------------------------------------------------------ Grok
class FakeXAI:
    def __init__(self, status=200, raise_exc=None):
        self.status = status
        self.raise_exc = raise_exc
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {GROK_KEY}"
        if self.raise_exc:
            raise self.raise_exc(request)
        if request.url.path.endswith("/chat/completions"):
            self.requests.append(json.loads(request.content))
            if self.status != 200:
                return httpx.Response(self.status, json={"error": "nope"})
            return sse(text_chunks("[INTERPRETATION] Attitude is stable.", usage={"prompt_tokens": 1000, "completion_tokens": 50}))
        return httpx.Response(404)


@pytest.fixture
def grok_client(tmp_path):
    s = make_settings(tmp_path, grok_enabled=True, xai_api_key=GROK_KEY, grok_base_url="http://127.0.0.1:9/v1",
                      grok_price_input_per_mtok=3.0, grok_price_output_per_mtok=15.0, grok_rate_limit_per_min=3)
    with TestClient(create_app(s)) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        wait_for(lambda: c.get("/api/imu").json().get("latest") is not None)
        yield c


def use_fake_xai(client, fake):
    g = client.app.state.container.grok
    g._client = httpx.AsyncClient(base_url="http://127.0.0.1:9/v1", transport=httpx.MockTransport(fake),
                                  headers={"Authorization": f"Bearer {GROK_KEY}"})


def test_grok_requires_consent(grok_client):
    r = grok_client.post("/api/ai/chat", json={"message": "deep analysis", "mode": "grok"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "consent_required"
    r = grok_client.post("/api/ai/chat", json={"message": "x", "mode": "hybrid", "deep_analysis": True})
    assert r.status_code == 403


def test_grok_chat_labeled_with_cost(grok_client):
    fake = FakeXAI()
    use_fake_xai(grok_client, fake)
    ev = chat(grok_client, message="Is the IMU behaving abnormally?", mode="grok", external_consent=True)
    assert ev[0]["provider"] == "grok-cloud" and ev[0]["external"] is True and ev[0]["label"] == "Grok Cloud (xAI)"
    stats = ev[-1]["stats"]
    assert stats["prompt_tokens"] == 1000 and stats["cost_usd"] == pytest.approx(1000 / 1e6 * 3 + 50 / 1e6 * 15)


def test_grok_respects_telemetry_opt_out(grok_client):
    fake = FakeXAI()
    use_fake_xai(grok_client, fake)
    chat(grok_client, message="General question", mode="grok", external_consent=True, include_telemetry=False)
    content = fake.requests[0]["messages"][-1]["content"]
    assert "chose NOT to share telemetry" in content and "roll=" not in content
    assert len(fake.requests[0]["messages"]) == 2  # system + user, no history


def test_hybrid_routes_routine_locally(grok_client):
    fake_x = FakeXAI()
    use_fake_xai(grok_client, fake_x)
    fake_l = FakeLlama()
    use_fake_llama(grok_client, fake_l)
    ev = chat(grok_client, message="Has the temperature changed?", mode="hybrid")
    assert ev[0]["provider"] == "qwen-local" and not fake_x.requests
    ev = chat(grok_client, message="Deep analysis please", mode="hybrid", deep_analysis=True, external_consent=True)
    assert ev[0]["provider"] == "grok-cloud" and len(fake_x.requests) == 1


@pytest.mark.parametrize("status,code", [(401, "auth"), (404, "model"), (429, "rate_limited"), (503, "upstream")])
def test_grok_http_errors_are_mapped(grok_client, status, code):
    use_fake_xai(grok_client, FakeXAI(status=status))
    ev = chat(grok_client, message="x", mode="grok", external_consent=True)
    err = next(e for e in ev if e["type"] == "error")
    assert err["code"] == code and GROK_KEY not in json.dumps(ev)
    assert not any(e["type"] == "fallback" for e in ev)  # never silently swaps providers


def test_grok_timeout_handled(grok_client):
    use_fake_xai(grok_client, FakeXAI(raise_exc=lambda req: httpx.ReadTimeout("slow", request=req)))
    ev = chat(grok_client, message="x", mode="grok", external_consent=True)
    assert next(e for e in ev if e["type"] == "error")["code"] == "timeout"
    # Sensor acquisition unaffected by cloud failure.
    assert grok_client.get("/api/imu").json()["status"]["state"] == "synthetic"


def test_grok_rate_limit(grok_client):
    use_fake_xai(grok_client, FakeXAI())
    codes = []
    for _ in range(5):
        ev = chat(grok_client, message="x", mode="grok", external_consent=True)
        codes.append(next((e["code"] for e in ev if e["type"] == "error"), "ok"))
    assert codes[:3] == ["ok", "ok", "ok"] and codes[3] == "rate_limited"


def test_grok_status_never_exposes_key(grok_client):
    text = grok_client.get("/api/ai/models").text + grok_client.get("/api/config").text
    assert GROK_KEY not in text
    assert grok_client.get("/api/ai/models").json()["providers"]["grok"]["configured"] is True


async def test_grok_disabled_and_unconfigured(tmp_path):
    g = GrokService(make_settings(tmp_path, grok_enabled=True, xai_api_key=None), EventLog())
    await g.start()
    assert g.status()["state"] == "not_configured"
    with pytest.raises(LLMError) as exc:
        async for _ in g.stream_chat([{"role": "user", "content": "x"}]):
            pass
    assert exc.value.code == "not_configured"


def test_sliding_window_limiter():
    t = [0.0]
    lim = SlidingWindowLimiter(2, 60.0, clock=lambda: t[0])
    assert lim.check().allowed and lim.check().allowed
    d = lim.check()
    assert not d.allowed and d.retry_after_s == pytest.approx(60.0)
    t[0] = 61.0
    assert lim.check().allowed

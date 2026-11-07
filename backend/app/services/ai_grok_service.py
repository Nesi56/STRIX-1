"""Optional Grok (xAI) cloud analysis through a backend proxy.

* The API key lives only in the backend environment (``XAI_API_KEY``); it is
  never sent to the browser, logged, or included in error messages.
* Requests are rate limited (per minute and per day) and time out.
* Nothing is sent automatically: the AI router only calls this service for an
  explicit operator request that carries ``external_consent=true``.
* Image input is used only when the model reports ``image`` in its
  ``input_modalities`` (``GET /v1/language-models/{model}``) or when
  ``GROK_VISION=true`` forces it.
* Costs are approximate and shown only when per-token prices are configured
  (``GROK_PRICE_INPUT_PER_MTOK`` / ``GROK_PRICE_OUTPUT_PER_MTOK``).
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator

import httpx

from app.config.settings import Settings
from app.core.ratelimit import DailyCounter, SlidingWindowLimiter
from app.services.ai_qwen_service import LLMError, iter_sse_json
from app.services.hub import EventLog

log = logging.getLogger(__name__)


class GrokService:
    provider = "grok-cloud"

    def __init__(self, settings: Settings, events: EventLog, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.events = events
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self.minute_limiter = SlidingWindowLimiter(settings.grok_rate_limit_per_min, 60.0)
        self.daily = DailyCounter(settings.grok_daily_request_limit)
        self.model_info: dict[str, Any] | None = None
        self.model_info_error: str | None = None
        self.last_error: str | None = None
        self.usage = {"requests": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0,
                      "cost_usd": 0.0, "cost_known": True, "last": None}

    @property
    def configured(self) -> bool:
        key = self.settings.xai_api_key
        return bool(key and key.get_secret_value().strip())

    @property
    def enabled(self) -> bool:
        return self.settings.grok_enabled and self.configured

    async def start(self) -> None:
        if not self.enabled:
            return
        self._client = httpx.AsyncClient(
            base_url=self.settings.grok_base_url.rstrip("/"),
            timeout=httpx.Timeout(self.settings.grok_timeout_s, connect=10.0),
            headers={"Authorization": f"Bearer {self.settings.xai_api_key.get_secret_value()}"},  # type: ignore[union-attr]
            transport=self._transport,
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def refresh_model_info(self) -> dict[str, Any] | None:
        """Look up the configured model's modalities (best effort, no quota use)."""
        if self._client is None:
            return None
        try:
            r = await self._client.get(f"/language-models/{self.settings.grok_model}", timeout=10.0)
            if r.status_code == 200:
                d = r.json()
                self.model_info = {
                    "id": d.get("id"),
                    "aliases": d.get("aliases"),
                    "input_modalities": d.get("input_modalities"),
                    "output_modalities": d.get("output_modalities"),
                }
                self.model_info_error = None
            else:
                self.model_info_error = f"model lookup HTTP {r.status_code}"
        except httpx.HTTPError as exc:
            self.model_info_error = f"model lookup failed: {exc.__class__.__name__}"
        return self.model_info

    @property
    def vision_capable(self) -> bool:
        mode = self.settings.grok_vision
        if mode == "true":
            return True
        if mode == "false":
            return False
        mods = (self.model_info or {}).get("input_modalities") or []
        return "image" in mods

    def estimate_cost(self, prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
        pi, po = self.settings.grok_price_input_per_mtok, self.settings.grok_price_output_per_mtok
        if pi is None or po is None or prompt_tokens is None or completion_tokens is None:
            return None
        return prompt_tokens / 1e6 * pi + completion_tokens / 1e6 * po

    def _check_limits(self) -> None:
        d = self.daily.check(consume=False)
        if not d.allowed:
            raise LLMError(f"Grok {d.reason} ({self.settings.grok_daily_request_limit}/day)", "rate_limited")
        m = self.minute_limiter.check(consume=True)
        if not m.allowed:
            raise LLMError(f"Grok rate limit: retry in {m.retry_after_s:.0f}s", "rate_limited")
        self.daily.check(consume=True)

    @staticmethod
    def _describe_http_error(status: int, body: str) -> tuple[str, str]:
        if status in (401, 403):
            return "Grok rejected the API key (check XAI_API_KEY on the Pi)", "auth"
        if status == 404:
            return "Grok model not found (check GROK_MODEL)", "model"
        if status == 429:
            return "xAI rate limit or quota exceeded", "rate_limited"
        if status >= 500:
            return f"xAI service error (HTTP {status})", "upstream"
        return f"Grok request failed (HTTP {status}): {body[:200]}", "http_error"

    async def stream_chat(self, messages: list[dict[str, Any]], max_tokens: int | None = None) -> AsyncIterator[dict[str, Any]]:
        if not self.settings.grok_enabled:
            raise LLMError("Grok is disabled (GROK_ENABLED=false)", "disabled")
        if not self.configured or self._client is None:
            raise LLMError("Grok is not configured (XAI_API_KEY missing)", "not_configured")
        self._check_limits()
        payload = {
            "model": self.settings.grok_model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens or self.settings.grok_max_tokens,
            "temperature": 0.3,
        }
        t0 = time.perf_counter()
        ttft = None
        usage: dict[str, Any] = {}
        chars = 0
        self.usage["requests"] += 1
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="replace")
                    msg, code = self._describe_http_error(r.status_code, body)
                    raise LLMError(msg, code)
                async for chunk in iter_sse_json(r):
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for ch in chunk.get("choices") or []:
                        text = (ch.get("delta") or {}).get("content")
                        if text:
                            if ttft is None:
                                ttft = time.perf_counter() - t0
                            chars += len(text)
                            yield {"delta": text}
        except httpx.TimeoutException as exc:
            self._fail(f"Grok timed out after {self.settings.grok_timeout_s:.0f}s")
            raise LLMError(self.last_error or "timeout", "timeout") from exc
        except httpx.HTTPError as exc:
            self._fail(f"Grok connection failed: {exc.__class__.__name__}")
            raise LLMError(self.last_error or "connection failed", "connection") from exc
        except LLMError as exc:
            self._fail(str(exc))
            raise
        total = time.perf_counter() - t0
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        estimated = False
        if ct is None:
            ct, estimated = max(1, chars // 4), True
        cost = self.estimate_cost(pt, ct)
        if pt:
            self.usage["prompt_tokens"] += pt
        self.usage["completion_tokens"] += ct or 0
        if cost is None:
            self.usage["cost_known"] = False
        else:
            self.usage["cost_usd"] += cost
        stats = {
            "latency_ms": round(total * 1000),
            "ttft_ms": round(ttft * 1000) if ttft is not None else None,
            "tokens_per_s": round(ct / (total - ttft), 2) if ct and ttft is not None and total > ttft else None,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "tokens_estimated": estimated,
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "cost_usd": round(cost, 6) if cost is not None else None,
        }
        self.usage["last"] = {**stats, "ts": time.time()}
        self.last_error = None
        yield {"stats": stats}

    def _fail(self, msg: str) -> None:
        self.usage["errors"] += 1
        self.last_error = msg
        self.events.add("warning", "ai", msg)

    def status(self) -> dict[str, Any]:
        s = self.settings
        if not s.grok_enabled:
            state = "disabled"
        elif not self.configured:
            state = "not_configured"
        else:
            state = "ready" if self.last_error is None else "error"
        return {
            "provider": self.provider,
            "label": "Grok Cloud (xAI)",
            "enabled": s.grok_enabled,
            "configured": self.configured,  # never the key itself
            "state": state,
            "detail": self.last_error,
            "model": s.grok_model,
            "base_url": s.grok_base_url,
            "vision_capable": self.vision_capable,
            "vision_mode": s.grok_vision,
            "model_info": self.model_info,
            "model_info_error": self.model_info_error,
            "rate_limit_per_min": s.grok_rate_limit_per_min,
            "rate_remaining_min": self.minute_limiter.remaining(),
            "daily_limit": s.grok_daily_request_limit,
            "daily_used": self.daily.count,
            "usage": self.usage,
            "pricing_configured": s.grok_price_input_per_mtok is not None and s.grok_price_output_per_mtok is not None,
            "timeout_s": s.grok_timeout_s,
            "local": False,
        }

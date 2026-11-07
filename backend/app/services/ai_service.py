"""AI assistant router: Local Qwen, Grok Cloud and Hybrid analysis.

Hybrid policy: routine questions → local Qwen; operator-requested "deep
analysis" → Grok. Nothing ever goes to the cloud without the request
carrying ``external_consent=true`` (set by an explicit UI action), and the
router never falls back from local to cloud on its own.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from app.core.safety import StreamingSanitizer
from app.services.ai_context import SYSTEM_PROMPT, build_context, deterministic_digest, render_context
from app.services.ai_grok_service import GrokService
from app.services.ai_qwen_service import LLMError, QwenService
from app.services.camera_service import CameraError, safe_media_path

log = logging.getLogger(__name__)

Mode = Literal["local", "grok", "hybrid"]
_CAMERA_Q = re.compile(r"\b(camera|see|seeing|show|showing|visible|image|picture|view|scene|frame|look)\b", re.I)


class ConsentRequired(PermissionError):
    pass


class AIService:
    def __init__(self, container: Any, qwen: QwenService, grok: GrokService) -> None:
        self.c = container
        self.qwen = qwen
        self.grok = grok

    # ------------------------------------------------------------ catalog
    def models(self) -> dict[str, Any]:
        return {
            "modes": [
                {"id": "local", "label": "Local Qwen", "available": self.qwen.settings.qwen_enabled,
                 "description": "On-device llama.cpp inference. No data leaves the Pi."},
                {"id": "grok", "label": "Grok Cloud", "available": self.grok.enabled,
                 "description": "xAI cloud model. Sends the question (and, if you allow it, telemetry/snapshot) to xAI."},
                {"id": "hybrid", "label": "Hybrid", "available": self.qwen.settings.qwen_enabled,
                 "description": "Local Qwen for routine questions; Grok only when you request deep analysis."},
            ],
            "providers": {"qwen": self.qwen.status(), "grok": self.grok.status()},
            "vision_pipeline": {"opencv": True, "note": "Text-only models receive OpenCV detector results, never raw images."},
        }

    # ------------------------------------------------------------ history
    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        """Blocking (flushes pending writes): call via asyncio.to_thread."""
        self.c.db.flush(2.0)
        rows = self.c.db.query("SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["meta"] = json.loads(r["meta"] or "{}")
        return list(reversed(rows))

    def clear_history(self) -> None:
        self.c.db.flush(2.0)
        self.c.db.execute("DELETE FROM chat_messages")

    def _save(self, role: str, content: str, provider: str | None, model: str | None, meta: dict[str, Any]) -> None:
        self.c.db.enqueue(
            "INSERT INTO chat_messages(ts, role, content, provider, model, meta) VALUES (?,?,?,?,?,?)",
            (time.time(), role, content, provider, model, json.dumps(meta)),
        )

    def _history_messages(self, turns: int) -> list[dict[str, str]]:
        if turns <= 0:
            return []
        self.c.db.flush(2.0)
        rows = self.c.db.query(
            "SELECT role, content FROM chat_messages WHERE role IN ('user','assistant') ORDER BY id DESC LIMIT ?",
            (turns * 2,),
        )
        msgs = [{"role": r["role"], "content": r["content"][:2000]} for r in reversed(rows)]
        return msgs

    # --------------------------------------------------------------- chat
    def route(self, mode: Mode, deep_analysis: bool) -> str:
        if mode == "grok" or (mode == "hybrid" and deep_analysis):
            return "grok"
        return "qwen"

    async def chat(
        self,
        message: str,
        mode: Mode = "local",
        *,
        deep_analysis: bool = False,
        external_consent: bool = False,
        include_telemetry: bool = True,
        snapshot_id: str | None = None,
        history_turns: int = 4,
    ) -> AsyncIterator[dict[str, Any]]:
        target = self.route(mode, deep_analysis)
        if target == "grok" and not external_consent:
            raise ConsentRequired("sending data to Grok requires explicit operator consent (external_consent=true)")
        service = self.grok if target == "grok" else self.qwen
        model_name = self.grok.settings.grok_model if target == "grok" else self.qwen.settings.qwen_model_id
        label = "Grok Cloud (xAI)" if target == "grok" else "Local Qwen"

        notes: list[str] = []
        # Ground camera questions in a fresh local CV analysis.
        if _CAMERA_Q.search(message) and self.c.camera.available:
            latest = self.c.mapping.latest_observations(1)
            if not latest or time.time() - latest[-1]["ts"] > 30:
                try:
                    await self.c.mapping.analyze_now("ai-question")
                    notes.append("ran a fresh on-device OpenCV scene analysis")
                except Exception as exc:
                    notes.append(f"scene analysis failed: {exc}")

        image_part = None
        image_sent = False
        if snapshot_id:
            try:
                path = safe_media_path(self.c.camera.snap_dir, snapshot_id)
            except CameraError as exc:
                raise ValueError(f"snapshot not found: {snapshot_id}") from exc
            vision = self.grok.vision_capable if target == "grok" else self.qwen.vision_capable
            if vision:
                b64 = base64.b64encode(Path(path).read_bytes()).decode()
                image_part = {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                image_sent = True
                notes.append(f"snapshot {snapshot_id} attached as image input")
            else:
                try:
                    await self.c.mapping.analyze_now("snapshot-attachment", image_path=path)
                    notes.append(f"{label} is text-only: snapshot analysed locally with OpenCV; image NOT sent to the model")
                except Exception as exc:
                    notes.append(f"snapshot analysis failed: {exc}")

        ctx = build_context(self.c)
        share_context = include_telemetry or target == "qwen"
        context_text = render_context(ctx) if share_context else (
            "=== TELEMETRY CONTEXT ===\nThe operator chose NOT to share telemetry with this cloud model. "
            "All sensor values are UNAVAILABLE to you.\n=== END CONTEXT ===")
        history = await asyncio.to_thread(self._history_messages, history_turns) if share_context else []
        user_text = f"{context_text}\n\nQUESTION: {message}"
        user_content: Any = [{"type": "text", "text": user_text}, image_part] if image_part else user_text
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": user_content}]

        meta = {
            "provider": service.provider,
            "model": model_name,
            "label": label,
            "local": target == "qwen",
            "mode": mode,
            "telemetry_shared": share_context,
            "image_sent": image_sent,
            "external": target == "grok",
            "notes": notes,
            "context_chars": len(context_text),
            "synthetic_sources": ctx["synthetic_sources"],
        }
        self._save("user", message, None, None, {"mode": mode, "deep_analysis": deep_analysis, "snapshot_id": snapshot_id,
                                                 "external_consent": external_consent})
        yield {"type": "meta", **meta}

        san = StreamingSanitizer()
        stats: dict[str, Any] = {}
        try:
            async for ev in service.stream_chat(messages):
                if "delta" in ev:
                    out = san.feed(ev["delta"])
                    if out:
                        yield {"type": "delta", "text": out}
                elif "stats" in ev:
                    stats = ev["stats"]
        except LLMError as exc:
            self._usage(service.provider, model_name, stats, ok=False)
            yield {"type": "error", "code": exc.code, "message": str(exc), "provider": service.provider}
            if target == "qwen":
                digest = deterministic_digest(ctx, message)
                yield {"type": "fallback", "provider": "strix-digest", "label": "Telemetry digest (no AI model)",
                       "text": digest}
                self._save("assistant", digest, "strix-digest", None, {"fallback_for": service.provider, "error": str(exc)})
            return
        tail, replace = san.finish()
        if replace:
            yield {"type": "replace", "text": tail}
        elif tail:
            yield {"type": "delta", "text": tail}
        final_text = san.emitted if not replace else tail
        self._usage(service.provider, model_name, stats, ok=True)
        self._save("assistant", final_text, service.provider, model_name,
                   {**{k: meta[k] for k in ("label", "local", "telemetry_shared", "image_sent", "notes")},
                    "stats": stats, "sanitized": san.removed})
        yield {"type": "done", "stats": stats, "sanitized": san.removed}

    def _usage(self, provider: str, model: str, stats: dict[str, Any], ok: bool) -> None:
        self.c.db.enqueue(
            "INSERT INTO ai_usage(ts, provider, model, prompt_tokens, completion_tokens, cost_usd, latency_ms, ok) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (time.time(), provider, model, stats.get("prompt_tokens"), stats.get("completion_tokens"),
             stats.get("cost_usd"), stats.get("latency_ms"), int(ok)),
        )

    def usage_summary(self) -> list[dict[str, Any]]:
        return self.c.db.query(
            "SELECT provider, model, COUNT(*) AS requests, SUM(ok) AS ok, SUM(prompt_tokens) AS prompt_tokens, "
            "SUM(completion_tokens) AS completion_tokens, SUM(cost_usd) AS cost_usd, AVG(latency_ms) AS avg_latency_ms "
            "FROM ai_usage GROUP BY provider, model"
        )

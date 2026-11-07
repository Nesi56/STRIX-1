"""Local Qwen inference through llama.cpp's ``llama-server``.

The backend talks to llama-server's OpenAI-compatible API
(``/v1/chat/completions``) and its native ``/health`` (503 while the model is
loading) and ``/props`` (``modalities.vision``) endpoints. Optionally the
backend launches and supervises llama-server itself
(``QWEN_MANAGE_SERVER=true``).

Inference is serialized (one request at a time) so a 4-core Pi 5 is never
oversubscribed; the model never blocks sensor acquisition, which runs in
separate threads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx

from app.config.settings import Settings
from app.services.hub import EventLog

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    def __init__(self, message: str, code: str = "llm_error") -> None:
        super().__init__(message)
        self.code = code


async def iter_sse_json(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Parse an OpenAI-style ``text/event-stream`` into JSON chunks."""
    async for line in response.aiter_lines():
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            log.debug("skipping malformed SSE chunk: %r", data[:200])


class QwenService:
    provider = "qwen-local"

    def __init__(self, settings: Settings, events: EventLog, register_process=None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport
        self.events = events
        self._register_process = register_process or (lambda label, pid: None)
        self.base_url = settings.qwen_server_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._proc: subprocess.Popen | None = None
        self._proc_log: Any = None
        self._monitor_task: asyncio.Task | None = None
        self._sem = asyncio.Semaphore(1)
        self.waiting = 0
        self.state = "disabled" if not settings.qwen_enabled else "unknown"
        self.detail: str | None = None
        self.props: dict[str, Any] = {}
        self.vision_capable = False
        self.stats: dict[str, Any] = {"requests": 0, "errors": 0, "last": None}
        self._started_at: float | None = None

    # ------------------------------------------------------------ lifecycle
    def _headers(self) -> dict[str, str]:
        key = self.settings.qwen_api_key.get_secret_value() if self.settings.qwen_api_key else None
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def start(self) -> None:
        if not self.settings.qwen_enabled:
            return
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.settings.qwen_timeout_s, connect=5.0),
                                         headers=self._headers(), transport=self._transport)
        if self.settings.qwen_manage_server:
            self.start_server()
        self._monitor_task = asyncio.create_task(self._monitor(), name="qwen-monitor")

    async def stop(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
        self.stop_server()
        if self._client:
            await self._client.aclose()

    def server_command(self) -> list[str]:
        s = self.settings
        u = urlparse(self.base_url)
        cmd = [s.qwen_llama_server_bin, "-m", s.qwen_model_path, "-c", str(s.qwen_ctx_size), "-t", str(s.qwen_threads),
               "--host", u.hostname or "127.0.0.1", "--port", str(u.port or 8080)]
        if s.qwen_mmproj_path:
            cmd += ["--mmproj", s.qwen_mmproj_path]
        return cmd

    def start_server(self) -> dict[str, Any]:
        s = self.settings
        if self._proc and self._proc.poll() is None:
            return self.status()
        if not s.qwen_model_path or not Path(s.qwen_model_path).is_file():
            self.state, self.detail = "error", f"model file not found: {s.qwen_model_path or '(QWEN_MODEL_PATH unset)'}"
            return self.status()
        if shutil.which(s.qwen_llama_server_bin) is None and not Path(s.qwen_llama_server_bin).is_file():
            self.state, self.detail = "error", f"llama-server binary not found: {s.qwen_llama_server_bin}"
            return self.status()
        try:
            import psutil

            avail_mb = psutil.virtual_memory().available / 1e6
            need_mb = Path(s.qwen_model_path).stat().st_size / 1e6 * 1.25 + 300
            if avail_mb < need_mb:
                self.state, self.detail = "error", f"insufficient memory: {avail_mb:.0f} MB available, ≈{need_mb:.0f} MB needed"
                return self.status()
        except Exception:
            pass
        log_dir = s.data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._proc_log = open(log_dir / "llama-server.log", "ab")
        cmd = self.server_command()
        log.info("starting llama-server: %s", " ".join(cmd))
        # Lower priority (nice 5) so sensor/telemetry threads stay responsive.
        self._proc = subprocess.Popen(cmd, stdout=self._proc_log, stderr=subprocess.STDOUT,
                                      preexec_fn=lambda: __import__("os").nice(5))
        self._started_at = time.time()
        self._register_process("llama-server", self._proc.pid)
        self.state, self.detail = "loading", "llama-server starting"
        self.events.add("info", "ai", "local Qwen server starting")
        return self.status()

    def stop_server(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._proc:
            self._register_process("llama-server", None)
        self._proc = None
        if self._proc_log:
            self._proc_log.close()
            self._proc_log = None

    async def restart_server(self) -> dict[str, Any]:
        await asyncio.to_thread(self.stop_server)
        return self.start_server()

    async def _monitor(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(3.0 if self.state != "ready" else 10.0)

    async def refresh(self) -> None:
        if self._client is None:
            return
        if self._proc is not None and self._proc.poll() is not None:
            self.state, self.detail = "error", f"llama-server exited with code {self._proc.returncode} (see data/logs/llama-server.log)"
            self._register_process("llama-server", None)
            return
        try:
            r = await self._client.get(f"{self.base_url}/health", timeout=3.0)
        except httpx.HTTPError as exc:
            self.state = "loading" if self._proc is not None else "unreachable"
            self.detail = f"{self.base_url} not reachable: {exc.__class__.__name__}"
            return
        if r.status_code == 503:
            self.state, self.detail = "loading", "model loading"
            return
        if r.status_code != 200:
            self.state, self.detail = "error", f"/health returned HTTP {r.status_code}"
            return
        if self.state != "ready":
            self.events.add("info", "ai", "local Qwen model ready")
        self.state, self.detail = "ready", None
        try:
            p = (await self._client.get(f"{self.base_url}/props", timeout=3.0)).json()
            self.props = {
                "model_path": p.get("model_path"),
                "n_ctx": (p.get("default_generation_settings") or {}).get("n_ctx"),
                "total_slots": p.get("total_slots"),
                "build_info": p.get("build_info"),
                "modalities": p.get("modalities"),
            }
            self.vision_capable = bool((p.get("modalities") or {}).get("vision"))
        except Exception:
            pass

    # -------------------------------------------------------------- chat
    async def stream_chat(self, messages: list[dict[str, Any]], max_tokens: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """Yield {"delta": str} chunks then a final {"stats": {...}}."""
        if not self.settings.qwen_enabled or self._client is None:
            raise LLMError("local Qwen is disabled (QWEN_ENABLED=false)", "disabled")
        if self.state != "ready":
            raise LLMError(f"local model not ready ({self.state}{': ' + self.detail if self.detail else ''})", "not_ready")
        payload = {
            "model": self.settings.qwen_model_id,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens or self.settings.qwen_max_tokens,
            "temperature": self.settings.qwen_temperature,
            # Qwen3 hybrid models: disable <think> output; ignored by other templates.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        self.waiting += 1
        try:
            await self._sem.acquire()
        finally:
            self.waiting -= 1
        try:
            t0 = time.perf_counter()
            ttft = None
            n_chunks = 0
            timings: dict[str, Any] = {}
            usage: dict[str, Any] = {}
            self.stats["requests"] += 1
            try:
                async with self._client.stream("POST", f"{self.base_url}/v1/chat/completions", json=payload) as r:
                    if r.status_code != 200:
                        body = (await r.aread()).decode(errors="replace")[:300]
                        raise LLMError(f"llama-server HTTP {r.status_code}: {body}", "http_error")
                    async for chunk in iter_sse_json(r):
                        if chunk.get("timings"):
                            timings = chunk["timings"]
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        for ch in chunk.get("choices") or []:
                            text = (ch.get("delta") or {}).get("content")
                            if text:
                                if ttft is None:
                                    ttft = time.perf_counter() - t0
                                n_chunks += 1
                                yield {"delta": text}
            except httpx.TimeoutException as exc:
                self.stats["errors"] += 1
                raise LLMError(f"local model timed out after {self.settings.qwen_timeout_s:.0f}s", "timeout") from exc
            except httpx.HTTPError as exc:
                self.stats["errors"] += 1
                self.state = "unreachable"
                raise LLMError(f"local model connection failed: {exc.__class__.__name__}", "connection") from exc
            total = time.perf_counter() - t0
            tps = timings.get("predicted_per_second")
            if tps is None and ttft is not None and total > ttft:
                tps = n_chunks / (total - ttft)
            stats = {
                "latency_ms": round(total * 1000),
                "ttft_ms": round(ttft * 1000) if ttft is not None else None,
                "tokens_per_s": round(tps, 2) if tps else None,
                "prompt_tokens": usage.get("prompt_tokens", timings.get("prompt_n")),
                "completion_tokens": usage.get("completion_tokens", timings.get("predicted_n", n_chunks)),
                "prompt_eval_per_s": timings.get("prompt_per_second"),
                "cost_usd": 0.0,
            }
            self.stats["last"] = {**stats, "ts": time.time()}
            yield {"stats": stats}
        finally:
            self._sem.release()

    def status(self) -> dict[str, Any]:
        s = self.settings
        return {
            "provider": self.provider,
            "label": "Local Qwen (llama.cpp)",
            "enabled": s.qwen_enabled,
            "state": self.state,
            "detail": self.detail,
            "server_url": self.base_url,
            "managed": s.qwen_manage_server,
            "process_running": bool(self._proc and self._proc.poll() is None),
            "pid": self._proc.pid if self._proc and self._proc.poll() is None else None,
            "model_id": s.qwen_model_id,
            "model_path": s.qwen_model_path or None,
            "ctx_size": s.qwen_ctx_size,
            "threads": s.qwen_threads,
            "max_tokens": s.qwen_max_tokens,
            "vision_capable": self.vision_capable,
            "props": self.props,
            "busy": self._sem.locked(),
            "queued": self.waiting,
            "stats": self.stats,
            "local": True,
        }

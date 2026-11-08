"""Qwen camera agent.

Graph (mirrors the dashboard's AGENT GRAPH):

    GENERATOR ──plan──▶ EXECUTION LOOP [ OBSERVER → PILOT → UNTIL ] ──complete──▶ REPORT

* GENERATOR: local Qwen turns the operator's task into a short plan of camera skills,
  using grammar-constrained JSON (llama-server ``response_format`` + schema), so even a
  small model always returns a valid plan. If Qwen is offline a keyword planner is used
  and clearly labeled.
* EXECUTION LOOP, per step: OBSERVER snapshots sensors + detections; PILOT starts the
  skill on the camera tilt servo; UNTIL polls the skill's completion condition
  (reached / found / centered / timeout) at the detector rate.
* REPORT: Qwen answers from the final observation, the action log and deterministic
  movement suggestions (streamed). Offline: rule-based report.

Safety: the only actuator is the camera tilt servo, and only while the operator has
enabled servo control. Limits and slew rate are enforced by ServoService. Emergency
stop disarms the servo and cancels the run. Aircraft motion is only ever *suggested*.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, AsyncIterator

from app.core.safety import StreamingSanitizer
from app.services.ai_context import (
    PLAN_SCHEMA,
    PLANNER_PROMPT,
    REPORT_PROMPT,
    SKILLS,
    deterministic_report,
    describe_detection,
    observation_text,
    observe,
    suggestions,
)
from app.services.ai_qwen_service import LLMError
from app.services.detector import normalize_label
from app.services.servo_service import ServoError

log = logging.getLogger(__name__)

POLL_S = 0.2


class AgentBusy(RuntimeError):
    pass


class Cancelled(Exception):
    pass


def _num(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def rule_plan(task: str, vocabulary: list[str]) -> tuple[str, list[dict[str, Any]]]:
    """Keyword planner used when Qwen is offline (or returned an unusable plan)."""
    t = task.lower()
    target = ""
    words = re.findall(r"[a-z]+", t)
    for n in (2, 1):
        for i in range(len(words) - n + 1):
            lab = normalize_label(" ".join(words[i:i + n]), vocabulary)
            if lab:
                target = lab
                break
        if target:
            break
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°|deg|degree)", t) or re.search(r"\b(-?\d+(?:\.\d+)?)\b", t)
    num = float(m.group(1)) if m else None
    if re.search(r"\b(track|follow|keep\b.*\b(view|centered|centred|frame))", t) and target:
        return target, [{"skill": "find", "target": target}, {"skill": "track", "target": target, "seconds": 8}]
    if re.search(r"\b(find|look for|search|where is|where are|locate|spot)\b", t) and target:
        return target, [{"skill": "find", "target": target}]
    if re.search(r"\b(scan|look around|survey|sweep)\b", t):
        return target, [{"skill": "scan"}]
    if re.search(r"\b(level|reset the camera|center the camera|centre the camera|straight ahead)\b", t) or t.strip() in ("center", "centre"):
        return target, [{"skill": "center"}]
    if re.search(r"\b(center|centre)\b", t) and target:
        return target, [{"skill": "find", "target": target}, {"skill": "track", "target": target, "seconds": 5}]
    if re.search(r"\b(up|upward|raise)\b", t):
        return target, [{"skill": "tilt", "delta": abs(num) if num is not None else 15}]
    if re.search(r"\b(down|downward|lower)\b", t):
        return target, [{"skill": "tilt", "delta": -(abs(num) if num is not None else 15)}]
    if re.search(r"\b(look|tilt|point)\b", t) and num is not None:
        return target, [{"skill": "look", "tilt": num}]
    return target, []


class AgentService:
    def __init__(self, c: Any) -> None:
        self.c = c
        self.s = c.settings
        self._cancel = asyncio.Event()
        self._running = False
        self.state: dict[str, Any] = {"status": "idle", "node": None, "plan": [], "task": None, "run_id": None,
                                      "planner": None, "step": None, "error": None}

    # ----------------------------------------------------------- control
    def snapshot(self) -> dict[str, Any]:
        st = dict(self.state)
        st["plan"] = [dict(p) for p in self.state["plan"]]
        return st

    def stop(self, reason: str = "stopped by operator") -> bool:
        if not self._running:
            return False
        self.state["error"] = reason
        self._cancel.set()
        self.c.events.add("warning", "agent", f"agent run cancelled: {reason}")
        return True

    def _check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    async def _sleep(self, s: float) -> None:
        try:
            await asyncio.wait_for(self._cancel.wait(), timeout=s)
        except asyncio.TimeoutError:
            return
        raise Cancelled()

    def _node(self, node: str | None) -> dict[str, Any]:
        self.state["node"] = node
        return {"type": "node", "node": node}

    # ----------------------------------------------------------- history
    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        self.c.db.flush(2.0)
        rows = self.c.db.query("SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["meta"] = json.loads(r["meta"] or "{}")
        return list(reversed(rows))

    def clear_history(self) -> None:
        self.c.db.flush(2.0)
        self.c.db.execute("DELETE FROM chat_messages")

    def _save(self, role: str, content: str, provider: str | None, meta: dict[str, Any]) -> None:
        self.c.db.enqueue("INSERT INTO chat_messages(ts, role, content, provider, model, meta) VALUES (?,?,?,?,?,?)",
                          (time.time(), role, content, provider, self.s.qwen_model_id if provider == "qwen" else None,
                           json.dumps(meta)))

    # ----------------------------------------------------------- planning
    def _validate_plan(self, raw: list[Any]) -> list[dict[str, Any]]:
        vocab = self.c.detector.vocabulary
        sv = self.c.servo
        lo = (min(sv.min_angle, sv.max_angle) - sv.center_angle)
        hi = (max(sv.min_angle, sv.max_angle) - sv.center_angle)
        span = max(abs(lo), abs(hi))
        out: list[dict[str, Any]] = []
        for step in raw[: self.s.agent_max_plan_steps]:
            if not isinstance(step, dict) or step.get("skill") not in SKILLS:
                continue
            sk = step["skill"]
            if sk == "look":
                out.append({"skill": "look", "tilt": _num(step.get("tilt"), -span, span, 0.0)})
            elif sk == "tilt":
                out.append({"skill": "tilt", "delta": _num(step.get("delta"), -2 * span, 2 * span, 10.0)})
            elif sk in ("find", "track"):
                lab = normalize_label(str(step.get("target", "")), vocab)
                if lab is None:
                    continue
                d = {"skill": sk, "target": lab}
                if sk == "track":
                    d["seconds"] = _num(step.get("seconds"), 2, 30, 8)
                out.append(d)
            elif sk == "wait":
                out.append({"skill": "wait", "seconds": _num(step.get("seconds"), 0.2, 10, 1)})
            else:
                out.append({"skill": sk})
        return out

    async def _plan(self, task: str, obs_text: str) -> tuple[str, str, str, list[dict[str, Any]], dict[str, Any]]:
        """Return (planner, thought, target, plan, stats)."""
        q = self.c.qwen
        if q.state == "ready":
            msgs = [{"role": "system", "content": PLANNER_PROMPT},
                    {"role": "user", "content": f"{obs_text}\n\nTASK: {task}"}]
            job = asyncio.ensure_future(q.complete_json(msgs, PLAN_SCHEMA, max_tokens=300))
            canceller = asyncio.ensure_future(self._cancel.wait())
            done, _ = await asyncio.wait({job, canceller}, return_when=asyncio.FIRST_COMPLETED)
            canceller.cancel()
            if job not in done:
                job.cancel()
                raise Cancelled()
            try:
                data, stats = job.result()
                plan = self._validate_plan(data.get("plan") or [])
                target = normalize_label(str(data.get("target", "")), self.c.detector.vocabulary) or ""
                if not target:
                    target = next((p["target"] for p in plan if "target" in p), "")
                return "qwen", str(data.get("thought", ""))[:200], target, plan, stats
            except LLMError as exc:
                log.warning("Qwen planning failed, using rule planner: %s", exc)
                target, plan = rule_plan(task, self.c.detector.vocabulary)
                return "rules", f"Qwen planning failed ({exc.code}); keyword planner used", target, plan, {}
        target, plan = rule_plan(task, self.c.detector.vocabulary)
        return "rules", f"Qwen {q.state} — keyword planner used", target, plan, {}

    # ----------------------------------------------------------- skills
    async def _move(self, fn, *args) -> str | None:
        """Issue a servo command; returns an error string instead of raising."""
        sv = self.c.servo
        if not sv.state()["available"]:
            return "camera servo unavailable"
        if not sv.armed:
            return "servo control is disabled — enable it (E key or toggle) to let Qwen move the camera"
        try:
            fn(*args)
        except ServoError as exc:
            return str(exc)
        t0 = time.monotonic()
        while not sv.at_target() and time.monotonic() - t0 < self.s.agent_skill_timeout_s:
            await self._sleep(POLL_S)
        return None

    async def _fresh_detections(self, after_seq: int, timeout: float = 3.0) -> dict[str, Any] | None:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            r = self.c.detector.latest(prompted_only=False)
            if r and r["seq"] > after_seq:
                return r
            await self._sleep(0.1)
        return self.c.detector.latest(prompted_only=False)

    def _det_seq(self) -> int:
        r = self.c.detector.latest(prompted_only=False)
        return r["seq"] if r else 0

    def _tilt_steps(self) -> list[float]:
        sv = self.c.servo
        lo = (sv.min_angle - sv.center_angle) * sv.up_sign
        hi = (sv.max_angle - sv.center_angle) * sv.up_sign
        lo, hi = min(lo, hi), max(lo, hi)
        n = 5
        return [round(lo + (hi - lo) * i / (n - 1), 1) for i in range(n)]

    async def _skill(self, step: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Run one skill. Yields progress events; the last event has type 'result'."""
        sv = self.c.servo
        sk = step["skill"]
        if sk == "wait":
            yield self._node("until")
            await self._sleep(step["seconds"])
            yield {"type": "result", "status": "done", "text": f"waited {step['seconds']:.1f} s"}
            return
        if sk in ("look", "tilt", "center"):
            fn, args, label = {
                "look": (sv.tilt_to, (step.get("tilt", 0.0),), f"tilt to {step.get('tilt', 0.0):+.0f}°"),
                "tilt": (sv.nudge, (step.get("delta", 0.0),), f"tilt by {step.get('delta', 0.0):+.0f}°"),
                "center": (sv.center, (), "level the camera"),
            }[sk]
            yield self._node("until")
            err = await self._move(fn, *args)
            if err:
                yield {"type": "result", "status": "skipped", "text": f"{label}: {err}"}
            else:
                yield {"type": "result", "status": "done", "text": f"{label} → tilt {sv.tilt_deg():+.0f}°"}
            return
        if sk in ("scan", "find"):
            target = step.get("target")
            if target:
                hit = self.c.detector.find(target)
                if hit:
                    yield {"type": "result", "status": "done", "found": True,
                           "text": f"{target} already in view: {describe_detection(hit[0])}"}
                    return
            seen: dict[str, tuple[float, dict[str, Any]]] = {}
            start_tilt = sv.tilt_deg() or 0.0
            for tilt in self._tilt_steps():
                yield self._node("pilot")
                err = await self._move(sv.tilt_to, tilt)
                if err:
                    yield {"type": "result", "status": "skipped", "text": f"{sk}: {err}"}
                    return
                yield self._node("until")
                res = await self._fresh_detections(self._det_seq())
                res = await self._fresh_detections(res["seq"] if res else 0)
                for d in (res or {}).get("detections", []):
                    if d["label"] not in seen or d["confidence"] > seen[d["label"]][1]["confidence"]:
                        seen[d["label"]] = (tilt, d)
                yield {"type": "progress", "text": f"tilt {tilt:+.0f}°: {', '.join(sorted({d['label'] for d in (res or {}).get('detections', [])})) or 'nothing'}"}
                if target and target in seen:
                    t, d = seen[target]
                    yield {"type": "result", "status": "done", "found": True,
                           "text": f"found {target} at tilt {t:+.0f}°: {describe_detection(d)}"}
                    return
            if target:
                await self._move(sv.tilt_to, start_tilt)
                yield {"type": "result", "status": "failed", "found": False, "text": f"{target} not found in the tilt range"}
                return
            best = max(seen.values(), key=lambda v: v[1]["confidence"], default=None)
            if best:
                await self._move(sv.tilt_to, best[0])
            summary = "; ".join(f"{lab} at tilt {t:+.0f}°" for lab, (t, _) in sorted(seen.items())) or "no objects"
            yield {"type": "result", "status": "done", "text": f"scan complete — {summary}"}
            return
        if sk == "track":
            target = step["target"]
            if not sv.armed or not sv.state()["available"]:
                yield {"type": "result", "status": "skipped",
                       "text": "track: servo control is disabled — enable it to let Qwen move the camera"}
                return
            duration = step.get("seconds", 8.0)
            t0 = time.monotonic()
            last_seen = time.monotonic()
            centered = frames = 0
            seq = self._det_seq()
            yield self._node("until")
            while time.monotonic() - t0 < duration:
                res = await self._fresh_detections(seq, timeout=1.5)
                if res:
                    seq = res["seq"]
                cands = [d for d in (res or {}).get("detections", []) if d["label"] == target]
                if not cands:
                    if time.monotonic() - last_seen > 3.0:
                        yield {"type": "result", "status": "failed", "text": f"lost the {target} for >3 s"}
                        return
                    continue
                last_seen = time.monotonic()
                d = cands[0]
                frames += 1
                err = d["elevation_deg"]  # + target above image center → tilt up
                if abs(err) <= 3.0:
                    centered += 1
                else:
                    try:
                        sv.tilt_to((sv.tilt_deg() or 0.0) + 0.7 * err)
                    except ServoError as exc:
                        yield {"type": "result", "status": "skipped", "text": f"track: {exc}"}
                        return
            pct = 100.0 * centered / frames if frames else 0.0
            yield {"type": "result", "status": "done",
                   "text": f"tracked {target} for {duration:.0f} s — vertically centered in {pct:.0f}% of {frames} detections"}
            return
        yield {"type": "result", "status": "skipped", "text": f"unknown skill {sk}"}

    @staticmethod
    def describe_step(step: dict[str, Any]) -> str:
        sk = step["skill"]
        return {
            "look": lambda: f"look at tilt {step.get('tilt', 0):+.0f}°",
            "tilt": lambda: f"tilt camera {step.get('delta', 0):+.0f}°",
            "center": lambda: "level the camera",
            "scan": lambda: "scan the tilt range",
            "find": lambda: f"find {step.get('target')}",
            "track": lambda: f"track {step.get('target')} for {step.get('seconds', 8):.0f} s",
            "wait": lambda: f"wait {step.get('seconds', 1):.1f} s",
        }.get(sk, lambda: sk)()

    # ----------------------------------------------------------- run
    async def run(self, task: str) -> AsyncIterator[dict[str, Any]]:
        if self._running:
            raise AgentBusy("the agent is already running a task — stop it first")
        self._running = True
        self._cancel.clear()
        run_id = uuid.uuid4().hex[:8]
        self.state.update({"status": "running", "task": task, "run_id": run_id, "plan": [], "planner": None,
                           "step": None, "error": None, "started_at": time.time()})
        self._save("user", task, None, {"run_id": run_id})
        actions: list[str] = []
        t_start = time.perf_counter()
        try:
            yield {"type": "run", "run_id": run_id, "task": task}
            # ---------------- GENERATOR
            yield self._node("generator")
            obs = observe(self.c)
            planner, thought, target, plan, plan_stats = await self._plan(task, observation_text(obs))
            self.state["planner"] = planner
            self.state["plan"] = [{**p, "text": self.describe_step(p), "status": "pending"} for p in plan]
            yield {"type": "plan", "planner": planner, "thought": thought, "target": target,
                   "steps": self.state["plan"], "stats": plan_stats}
            # ---------------- EXECUTION LOOP
            for i, step in enumerate(plan):
                self._check()
                self.state["step"] = i
                yield self._node("observer")
                obs = observe(self.c)
                yield {"type": "observation", "text": "; ".join(describe_detection(d) for d in obs["detections"][:3]) or "no objects"}
                yield self._node("pilot")
                self.state["plan"][i]["status"] = "running"
                yield {"type": "step", "index": i, "status": "running", "text": self.state["plan"][i]["text"]}
                result: dict[str, Any] = {"status": "failed", "text": "no result"}
                async for ev in self._skill(step):
                    if ev["type"] == "result":
                        result = ev
                    elif ev["type"] == "node":
                        self.state["node"] = ev["node"]
                        yield ev
                    else:
                        yield ev
                self.state["plan"][i].update(status=result["status"], result=result["text"])
                actions.append(result["text"])
                yield {"type": "step", "index": i, "status": result["status"], "text": result["text"]}
            # ---------------- REPORT
            self._check()
            self.state["step"] = None
            yield self._node("observer")
            obs = observe(self.c)
            sugg = suggestions(obs, target or None, self.s.agent_standoff_m)
            yield {"type": "suggestions", "items": sugg}
            yield self._node("report")
            text, stats = "", {}
            provider = "rules"
            if self.c.qwen.state == "ready":
                provider = "qwen"
                content = (f"{observation_text(obs)}\n\nACTION LOG:\n" + ("\n".join(f"- {a}" for a in actions) or "- no camera motion")
                           + "\n\nPILOT SUGGESTIONS:\n" + ("\n".join(f"- {s}" for s in sugg) or "- none")
                           + f"\n\nTASK: {task}")
                san = StreamingSanitizer()
                gen = self.c.qwen.stream_chat([{"role": "system", "content": REPORT_PROMPT},
                                               {"role": "user", "content": content}], max_tokens=260)
                try:
                    async for ev in gen:
                        self._check()
                        if "delta" in ev:
                            out = san.feed(ev["delta"])
                            if out:
                                yield {"type": "delta", "text": out}
                        elif "stats" in ev:
                            stats = ev["stats"]
                    tail, replace = san.finish()
                    text = tail if replace else san.emitted
                    if replace:
                        yield {"type": "replace", "text": tail}
                    elif tail:
                        yield {"type": "delta", "text": tail}
                except LLMError as exc:
                    provider = "rules"
                    yield {"type": "error", "code": exc.code, "message": f"Qwen report failed: {exc}"}
                finally:
                    await gen.aclose()
            if provider == "rules":
                text = deterministic_report(obs, task, actions, sugg)
                yield {"type": "replace", "text": text}
            self._save("assistant", text, provider, {"run_id": run_id, "planner": planner, "plan": self.state["plan"],
                                                     "suggestions": sugg, "stats": stats, "thought": thought})
            self.state.update(status="done", node="complete")
            total = round((time.perf_counter() - t_start) * 1000)
            yield {"type": "done", "planner": planner, "reporter": provider, "stats": stats, "total_ms": total}
        except Cancelled:
            self.state.update(status="stopped", node=None)
            yield {"type": "stopped", "reason": self.state.get("error") or "stopped"}
        except Exception as exc:  # never leave the agent wedged
            log.exception("agent run failed")
            self.state.update(status="error", node=None, error=str(exc))
            yield {"type": "error", "code": "internal", "message": str(exc)}
        finally:
            self.state["finished_at"] = time.time()
            self._running = False

    @property
    def running(self) -> bool:
        return self._running

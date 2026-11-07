"""Guards applied to AI model output before it reaches the browser.

STRIX-1 never executes or relays model output as commands. As defence in
depth, fenced shell/script code blocks are replaced with a notice so the
assistant cannot hand the operator ready-to-paste system commands, and the
system prompt forbids flight/motor commands.
"""

from __future__ import annotations

import re

_SHELL_LANGS = {
    "sh", "bash", "shell", "zsh", "console", "terminal", "powershell", "ps", "ps1",
    "cmd", "bat", "fish", "shellsession",
}

_FENCE_RE = re.compile(r"```([A-Za-z0-9_+-]*)[^\n]*\n(.*?)(```|\Z)", re.DOTALL)
_SHELL_HINT_RE = re.compile(
    r"^\s*(\$ |sudo |rm |chmod |chown |curl |wget |systemctl |apt(-get)? |pip3? install |dd |mkfs|:\(\)\s*\{)",
    re.MULTILINE,
)

REMOVED_NOTICE = (
    "[command block removed — STRIX-1 does not relay model-generated shell commands. "
    "Consult the project documentation for verified procedures.]"
)


def sanitize_model_output(text: str) -> tuple[str, bool]:
    """Return (sanitized_text, removed_anything)."""
    removed = False

    def _repl(m: re.Match[str]) -> str:
        nonlocal removed
        lang = (m.group(1) or "").lower()
        body = m.group(2) or ""
        if lang in _SHELL_LANGS or (not lang and _SHELL_HINT_RE.search(body)):
            removed = True
            return REMOVED_NOTICE
        return m.group(0)

    out = _FENCE_RE.sub(_repl, text)
    return out, removed


_THINK_RE = re.compile(r"<think>.*?(</think>|\Z)", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning-capable models."""
    return _THINK_RE.sub("", text).lstrip()


class StreamingSanitizer:
    """Incrementally sanitize streamed model output.

    Text inside an unclosed code fence or ``<think>`` block is held back until
    the block completes, so a shell command is never shown even transiently.
    ``feed`` returns text that is safe to emit now; ``finish`` returns the
    final sanitized full text and whether a full replacement is needed.
    """

    _HOLD_CHARS = 8  # hold back a possible partial marker such as "``" or "<thi"

    def __init__(self) -> None:
        self.raw = ""
        self.emitted = ""
        self.removed = False
        self._diverged = False

    def _safe_prefix(self, final: bool) -> str:
        text = self.raw
        if final:
            return text
        cut = len(text)
        if text.count("```") % 2 == 1:
            cut = min(cut, text.rfind("```"))
        low = text.lower()
        open_think = low.rfind("<think>")
        if open_think != -1 and low.find("</think>", open_think) == -1:
            cut = min(cut, open_think)
        # Hold back a trailing fragment that could be the start of a marker.
        for k in range(min(self._HOLD_CHARS, cut), 0, -1):
            frag = text[cut - k:cut]
            if "```".startswith(frag) or "<think>".startswith(frag.lower()):
                cut -= k
                break
        return text[:cut]

    def _render(self, prefix: str) -> str:
        out, removed = sanitize_model_output(strip_reasoning(prefix))
        self.removed = self.removed or removed
        return out

    def feed(self, delta: str) -> str:
        self.raw += delta
        if self._diverged:
            return ""
        rendered = self._render(self._safe_prefix(final=False))
        if not rendered.startswith(self.emitted):
            self._diverged = True
            return ""
        new = rendered[len(self.emitted):]
        self.emitted = rendered
        return new

    def finish(self) -> tuple[str, bool]:
        """Return (final_text, needs_replace)."""
        final = self._render(self._safe_prefix(final=True))
        if self._diverged or not final.startswith(self.emitted):
            return final, True
        tail = final[len(self.emitted):]
        self.emitted = final
        return tail, False

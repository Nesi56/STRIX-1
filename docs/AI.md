# AI: local Qwen, Grok cloud and hybrid analysis

## Design principles

- **Grounded.** Every question is answered with a timestamped *telemetry context* built by
  `backend/app/services/ai_context.py`. It holds the latest IMU sample, 60 s statistics,
  deterministic IMU health findings, the DHT11 reading plus its 5-minute trend and session
  extremes, servo state, camera status, the latest OpenCV detector summaries, system health and
  an explicit list of **unavailable** quantities (GPS, altitude, ground speed, compass heading,
  battery %, scene distances). The data mode (*live hardware* or *SYNTHETIC DEMO DATA*) is
  stated explicitly.
- **Labeled claims.** The system prompt requires `[MEASURED]`, `[INTERPRETATION]` and
  `[UNAVAILABLE]` tags. The dashboard renders them as colored badges.
- **No hidden vision.** A text-only model never receives an image. Attached snapshots are
  analysed on the Pi with OpenCV and only the detector results are sent. Images go to a model
  only when it reports vision capability: llama-server `/props` → `modalities.vision`, or xAI
  `input_modalities` contains `image`.
- **No actions.** The assistant has no tools and cannot move the servo or the aircraft. The
  backend never executes model output. As defence in depth, fenced shell/command blocks are
  stripped from responses, even mid-stream (`app/core/safety.py`), and responses are labeled *FILTERED*.
- **No silent cloud fallback.** If the local model is down, the answer comes from a
  deterministic *telemetry digest*, never from Grok.

## Local Qwen (llama.cpp)

| Setting | Default | Meaning |
|---|---|---|
| `QWEN_SERVER_URL` | `http://127.0.0.1:8080` | llama-server address (keep it on localhost) |
| `QWEN_MODEL_ID` | `qwen2.5-3b-instruct-q4_k_m` | label shown in the UI |
| `QWEN_CTX_SIZE` | 4096 | context length (`-c`); the telemetry context uses ~1–1.5 k tokens |
| `QWEN_THREADS` | 3 | CPU threads (`-t`); leaves one core for sensors and camera |
| `QWEN_MAX_TOKENS` | 512 | answer length cap |
| `QWEN_MANAGE_SERVER` | false | let the backend start/stop llama-server itself |
| `QWEN_MODEL_PATH` / `QWEN_MMPROJ_PATH` | — | used when the backend manages the server |

**Model choice (start small, measure):**

| Model | Quant | RAM | Notes |
|---|---|---|---|
| Qwen2.5-3B-Instruct (`Qwen/Qwen2.5-3B-Instruct-GGUF`) | Q4_K_M ≈ 2 GB | ≈ 3 GB | default; good starting point |
| Qwen3-4B-Instruct-2507 (community GGUF) | Q4_K_M ≈ 2.5 GB | ≈ 3.5 GB | stronger, slower; non-thinking variant |
| Qwen2.5-VL-3B-Instruct (`ggml-org/Qwen2.5-VL-3B-Instruct-GGUF`) | Q4_K_M + mmproj | ≈ 4 GB | **vision**: enables snapshot image input |

Expect low single-digit tokens/s for 3–4 B Q4 models on the Pi 5's four Cortex-A76 cores (CPU
only), plus several seconds of prompt processing for the ~1.5 k-token context. These are
estimates; the dashboard measures the real numbers (TTFT, tokens/s, latency) per answer.
If the camera is streaming 1080p and recording at the same time, expect lower throughput.

Thinking-capable Qwen3 checkpoints get `chat_template_kwargs: {"enable_thinking": false}`, and
any `<think>` blocks are stripped.

### Setup

```bash
scripts/build_llama_cpp.sh                       # LLAMA_CPP_REF=<tag> to pin a release
scripts/download_qwen.sh                         # or: llama-server -hf Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M
scripts/install_llama_service.sh ~/STRIX-1/models/qwen2.5-3b-instruct-q4_k_m.gguf 3 4096
```

The `strix1-llama` unit binds to `127.0.0.1:8080`, runs at nice 5 with `MemoryMax=9G`, and
restarts on failure. The backend polls `/health` (503 = *loading*) and `/props`. The dashboard
stays fully usable while the model loads.

**Vision model:** edit the unit's `ExecStart` to the commented `-hf ggml-org/Qwen2.5-VL-3B-Instruct-GGUF`
line (or `-m <model> --mmproj <projector>`). The capability is detected automatically.

**Low memory:** with `QWEN_MANAGE_SERVER=true` the backend refuses to start llama-server if
available RAM is below the model size × 1.25 + 300 MB. With the systemd unit, `MemoryMax`
protects the rest of the system. The dashboard warns when available memory gets low.

## Grok cloud

| Setting | Default | Meaning |
|---|---|---|
| `GROK_ENABLED` | false | master switch |
| `XAI_API_KEY` | — | backend-only secret (never returned by any endpoint) |
| `GROK_MODEL` | `grok-4` | any model id your xAI account can use |
| `GROK_BASE_URL` | `https://api.x.ai/v1` | OpenAI-compatible endpoint |
| `GROK_TIMEOUT_S` | 60 | request timeout |
| `GROK_RATE_LIMIT_PER_MIN` / `GROK_DAILY_REQUEST_LIMIT` | 4 / 200 | local rate limits |
| `GROK_VISION` | auto | `auto` asks `GET /v1/language-models/{model}` for `input_modalities` |
| `GROK_PRICE_INPUT_PER_MTOK` / `…OUTPUT…` | unset | USD per 1 M tokens for the cost estimate |

xAI model names change over time. Use any id listed in the xAI console. Costs are approximate:
they combine the `usage` returned by the API with the prices you configure. If usage isn't
returned, completion tokens are estimated (≈ chars/4) and marked *est.*

**Privacy and consent:** a request reaches xAI only if all of these hold:

1. you selected *Grok Cloud* mode, or clicked *Deep analysis (Grok)* in Hybrid mode
2. you confirmed the dialog that lists exactly what will be sent
3. the request carries `external_consent=true`. The backend returns 403 without it, so a
   buggy client cannot bypass the dialog.

Telemetry sharing can be switched off per request. Live video and location are never sent,
and nothing is uploaded automatically or periodically.

**Failure handling:** timeouts, connection errors, 401/403 (bad key), 404 (bad model),
429 (quota) and 5xx are reported in the chat with a clear message. Sensor acquisition runs in
separate threads and is unaffected.

## Hybrid mode

- Routine questions → local Qwen.
- *Deep analysis (Grok)* → Grok, with consent and telemetry (plus the snapshot, if attached and the model supports vision).
- Each answer is labeled with the model that produced it (LOCAL / CLOUD badge, model label,
  latency, tokens/s, cost).

## Example questions

- "What is the current pitch and roll?"
- "Summarize the last 60 seconds of sensor readings."
- "Has the temperature changed?"
- "What does the camera currently show?" (runs a fresh on-device OpenCV analysis if the last one is older than 30 s)
- "Is the IMU behaving abnormally?" (uses the deterministic health checks)
- "Summarize the latest recorded observations."

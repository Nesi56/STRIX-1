// Incremental parser for the backend's multipart/x-mixed-replace MJPEG stream.
// Each part carries X-Frame-Seq, X-Capture-Time (server UNIX seconds) and
// X-Synthetic headers, which lets the dashboard measure end-to-end latency
// (something a plain <img src=…mjpg> cannot do).

export interface MjpegFrame {
  jpeg: Uint8Array;
  seq: number;
  captureTs: number; // server clock, seconds
  synthetic: boolean;
}

const CRLFCRLF = new Uint8Array([13, 10, 13, 10]);
const decoder = new TextDecoder("latin1");

export function indexOfSeq(buf: Uint8Array, seq: Uint8Array, from = 0): number {
  const n = buf.length - seq.length;
  outer: for (let i = Math.max(0, from); i <= n; i++) {
    for (let j = 0; j < seq.length; j++) {
      if (buf[i + j] !== seq[j]) continue outer;
    }
    return i;
  }
  return -1;
}

export function parseHeaders(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\r\n")) {
    const i = line.indexOf(":");
    if (i > 0) out[line.slice(0, i).trim().toLowerCase()] = line.slice(i + 1).trim();
  }
  return out;
}

export class MultipartJpegParser {
  private buf = new Uint8Array(0);
  private maxBuffer: number;

  constructor(maxBuffer = 16 * 1024 * 1024) {
    this.maxBuffer = maxBuffer;
  }

  get buffered(): number {
    return this.buf.length;
  }

  push(chunk: Uint8Array): MjpegFrame[] {
    const merged = new Uint8Array(this.buf.length + chunk.length);
    merged.set(this.buf, 0);
    merged.set(chunk, this.buf.length);
    this.buf = merged;
    const frames: MjpegFrame[] = [];
    for (;;) {
      const headerEnd = indexOfSeq(this.buf, CRLFCRLF);
      if (headerEnd < 0) break;
      const headerText = decoder.decode(this.buf.subarray(0, headerEnd));
      const headers = parseHeaders(headerText);
      const len = Number(headers["content-length"]);
      if (!Number.isFinite(len) || len <= 0) {
        // Malformed part: drop through the header block and resync.
        this.buf = this.buf.slice(headerEnd + 4);
        continue;
      }
      const bodyStart = headerEnd + 4;
      if (this.buf.length < bodyStart + len) break; // wait for more data
      const jpeg = this.buf.slice(bodyStart, bodyStart + len);
      let next = bodyStart + len;
      if (this.buf[next] === 13 && this.buf[next + 1] === 10) next += 2;
      this.buf = this.buf.slice(next);
      frames.push({
        jpeg,
        seq: Number(headers["x-frame-seq"] ?? 0),
        captureTs: Number(headers["x-capture-time"] ?? 0),
        synthetic: headers["x-synthetic"] === "1",
      });
    }
    if (this.buf.length > this.maxBuffer) this.buf = new Uint8Array(0); // corrupt stream guard
    return frames;
  }
}

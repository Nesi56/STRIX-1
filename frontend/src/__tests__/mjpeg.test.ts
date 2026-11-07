import { describe, expect, it } from "vitest";
import { MultipartJpegParser, indexOfSeq, parseHeaders } from "../services/mjpeg";

const enc = new TextEncoder();

function part(seq: number, ts: number, body: Uint8Array, synthetic = false): Uint8Array {
  const head = enc.encode(
    `--strixframe\r\nContent-Type: image/jpeg\r\nContent-Length: ${body.length}\r\nX-Frame-Seq: ${seq}\r\n` +
      `X-Capture-Time: ${ts.toFixed(6)}\r\nX-Synthetic: ${synthetic ? 1 : 0}\r\n\r\n`,
  );
  const out = new Uint8Array(head.length + body.length + 2);
  out.set(head, 0);
  out.set(body, head.length);
  out.set([13, 10], head.length + body.length);
  return out;
}

const jpeg = (n: number, fill: number) => {
  const b = new Uint8Array(n).fill(fill);
  b[0] = 0xff; b[1] = 0xd8; b[n - 2] = 0xff; b[n - 1] = 0xd9;
  return b;
};

describe("MultipartJpegParser", () => {
  it("parses whole frames with metadata", () => {
    const p = new MultipartJpegParser();
    const frames = p.push(part(7, 1700000000.25, jpeg(100, 1), true));
    expect(frames).toHaveLength(1);
    expect(frames[0].seq).toBe(7);
    expect(frames[0].captureTs).toBeCloseTo(1700000000.25, 6);
    expect(frames[0].synthetic).toBe(true);
    expect(frames[0].jpeg.length).toBe(100);
    expect(p.buffered).toBe(0);
  });

  it("handles frames split across arbitrary chunk boundaries", () => {
    const stream = new Uint8Array([...part(1, 1, jpeg(500, 2)), ...part(2, 2, jpeg(300, 3)), ...part(3, 3, jpeg(50, 4))]);
    for (const size of [1, 7, 64, 333, 4096]) {
      const p = new MultipartJpegParser();
      const got: number[] = [];
      for (let i = 0; i < stream.length; i += size) got.push(...p.push(stream.subarray(i, i + size)).map((f) => f.seq));
      expect(got).toEqual([1, 2, 3]);
    }
  });

  it("does not mistake CRLFCRLF bytes inside JPEG data for a header", () => {
    const body = jpeg(64, 0);
    body.set([13, 10, 13, 10], 20);
    const p = new MultipartJpegParser();
    const frames = p.push(new Uint8Array([...part(1, 1, body), ...part(2, 2, jpeg(10, 9))]));
    expect(frames.map((f) => f.seq)).toEqual([1, 2]);
    expect(frames[0].jpeg).toEqual(body);
  });

  it("resyncs after a malformed part", () => {
    const bad = enc.encode("--strixframe\r\nContent-Type: image/jpeg\r\n\r\n");
    const p = new MultipartJpegParser();
    const frames = p.push(new Uint8Array([...bad, ...part(5, 5, jpeg(20, 1))]));
    expect(frames.map((f) => f.seq)).toEqual([5]);
  });
});

describe("helpers", () => {
  it("indexOfSeq", () => {
    expect(indexOfSeq(new Uint8Array([1, 2, 3, 4, 3, 4]), new Uint8Array([3, 4]), 3)).toBe(4);
    expect(indexOfSeq(new Uint8Array([1, 2]), new Uint8Array([3]))).toBe(-1);
  });
  it("parseHeaders lowercases keys", () => {
    expect(parseHeaders("Content-Length: 12\r\nX-Frame-Seq: 3")).toEqual({ "content-length": "12", "x-frame-seq": "3" });
  });
});

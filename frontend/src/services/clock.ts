// NTP-style clock offset estimation over the telemetry WebSocket.
// offset = server_time - client_time, estimated from the ping sample with the
// smallest round-trip time (least affected by queuing delay).

export interface ClockSample { rttMs: number; offsetMs: number; at: number }

export function computeSample(clientSendMs: number, serverMs: number, clientRecvMs: number): ClockSample {
  const rttMs = clientRecvMs - clientSendMs;
  const offsetMs = serverMs - (clientSendMs + clientRecvMs) / 2;
  return { rttMs, offsetMs, at: clientRecvMs };
}

export class ClockSync {
  private samples: ClockSample[] = [];
  constructor(private window = 15) {}

  add(s: ClockSample): void {
    if (s.rttMs < 0 || !Number.isFinite(s.offsetMs)) return;
    this.samples.push(s);
    if (this.samples.length > this.window) this.samples.shift();
  }

  get best(): ClockSample | null {
    if (!this.samples.length) return null;
    return this.samples.reduce((a, b) => (b.rttMs < a.rttMs ? b : a));
  }

  get offsetMs(): number {
    return this.best?.offsetMs ?? 0;
  }

  get lastRttMs(): number | null {
    return this.samples.length ? this.samples[this.samples.length - 1].rttMs : null;
  }

  /** Current time on the server clock, in seconds. */
  serverNow(clientNowMs = Date.now()): number {
    return (clientNowMs + this.offsetMs) / 1000;
  }
}

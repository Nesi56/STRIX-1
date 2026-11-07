// Minimal WHEP (WebRTC-HTTP Egress Protocol) client for the optional
// MediaMTX path. Signalling goes through the authenticated backend proxy
// (/api/camera/webrtc/whep); media flows directly from MediaMTX over ICE/UDP.
// Uses non-trickle ICE: we wait for gathering to finish before POSTing.

export interface WhepSession {
  pc: RTCPeerConnection;
  close: () => void;
}

export async function startWhep(video: HTMLVideoElement, endpoint = "/api/camera/webrtc/whep"): Promise<WhepSession> {
  const pc = new RTCPeerConnection({ bundlePolicy: "max-bundle" });
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.ontrack = (ev) => {
    if (video.srcObject !== ev.streams[0]) video.srcObject = ev.streams[0];
  };
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  await new Promise<void>((resolve) => {
    if (pc.iceGatheringState === "complete") return resolve();
    const t = setTimeout(resolve, 2000);
    pc.addEventListener("icegatheringstatechange", () => {
      if (pc.iceGatheringState === "complete") {
        clearTimeout(t);
        resolve();
      }
    });
  });
  const r = await fetch(endpoint, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/sdp" },
    body: pc.localDescription?.sdp ?? "",
  });
  if (!r.ok) {
    pc.close();
    throw new Error(`WHEP failed: HTTP ${r.status}`);
  }
  await pc.setRemoteDescription({ type: "answer", sdp: await r.text() });
  return { pc, close: () => pc.close() };
}

/** Receiver-side latency estimate from WebRTC stats (jitter buffer + RTT/2). */
export async function webrtcLatencyMs(pc: RTCPeerConnection): Promise<number | null> {
  const stats = await pc.getStats();
  let jitter: number | null = null;
  let rtt: number | null = null;
  stats.forEach((s) => {
    if (s.type === "inbound-rtp" && s.kind === "video" && s.jitterBufferEmittedCount) {
      jitter = (s.jitterBufferDelay / s.jitterBufferEmittedCount) * 1000;
    }
    if (s.type === "candidate-pair" && s.state === "succeeded" && s.currentRoundTripTime !== undefined) {
      rtt = s.currentRoundTripTime * 1000;
    }
  });
  if (jitter === null) return null;
  return (jitter as number) + ((rtt as number | null) ?? 0) / 2;
}

// Records the STRIX-1 console (demo mode) as timestamped JPEG frames via the Chrome DevTools screencast,
// while driving the UI through each feature. Output: <out>/frames/*.jpg + <out>/timeline.json
const { chromium } = require("playwright");
const fs = require("fs");

const OUT = process.argv[2];
const BASE = process.env.STRIX_URL || "http://127.0.0.1:8000";
const TOKEN = process.env.STRIX_TOKEN || "";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(`${OUT}/frames`, { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 960 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto(`${BASE}/`);
  await page.fill("input[type=password]", TOKEN);
  await page.keyboard.press("Enter");
  await page.waitForSelector("text=Camera Servo", { timeout: 20000 });
  const api = (path, body) => page.evaluate(async ([p, b]) => {
    const r = await fetch(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b ?? {}) });
    return r.status;
  }, [path, body]);
  // clean start: servo off, chat empty, fresh shell game
  await api("/api/servo/disarm");
  await page.evaluate(() => fetch("/api/agent/history", { method: "DELETE" }));
  await api("/api/demo/scene", { scene: "cups" });
  await page.reload();
  await page.waitForSelector("text=Camera Servo", { timeout: 20000 });
  await sleep(6000); // let the memory see the cups, warm up the 3D

  const rect = (sel) => page.evaluate((s) => {
    const find = (txt) => [...document.querySelectorAll("h2, span, button")].find((e) => e.textContent.trim() === txt);
    let el = null;
    if (s === "video") el = document.querySelector("canvas.max-h-full");
    else if (s === "videoPanel") el = document.querySelector("canvas.max-h-full")?.closest("section");
    else if (s === "status") el = find("STATUS")?.closest("section");
    else if (s === "servo") el = find("Camera Servo")?.closest("section");
    else if (s === "memory") el = find("Memory")?.closest("section");
    else if (s === "live3d") el = find("Live 3D")?.closest("section");
    else if (s === "chat") el = find("Recordings")?.closest("section");
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return [r.x, r.y, r.width, r.height];
  }, sel);
  const regions = {};
  for (const k of ["video", "videoPanel", "status", "servo", "memory", "live3d", "chat"]) regions[k] = await rect(k);

  const cdp = await ctx.newCDPSession(page);
  const frames = [];
  let n = 0;
  cdp.on("Page.screencastFrame", async (f) => {
    const name = `${String(n++).padStart(5, "0")}.jpg`;
    fs.writeFileSync(`${OUT}/frames/${name}`, Buffer.from(f.data, "base64"));
    frames.push({ t: f.metadata.timestamp, name });
    try { await cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }); } catch {}
  });
  const marks = {};
  const mark = (k) => { marks[k] = Date.now() / 1000; };
  const ask = async (text) => {
    const box = page.locator("input[aria-label='Message for Qwen']");
    await box.click();
    await box.type(text, { delay: 45 });
    await box.press("Enter");
  };

  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: 1920, maxHeight: 960, everyNthFrame: 1 });
  await sleep(500);

  // A + B: overview, then the camera with YOLO boxes (same footage, different framing)
  mark("A"); await sleep(10000);
  mark("B"); await sleep(12000);

  // C: sensors — the drone close-up driven by the MPU6050, DHT11 readings
  mark("C");
  await page.getByRole("button", { name: "Drone", exact: true }).click();
  await sleep(12000);
  await page.getByRole("button", { name: "World", exact: true }).click();

  // D: camera servo — enable, nudge, then ask Qwen to tilt
  mark("D");
  await sleep(800);
  await page.getByRole("button", { name: /Enable control/ }).click();
  await sleep(1500);
  await page.getByRole("button", { name: "+5°" }).click();
  await sleep(1000);
  await page.getByRole("button", { name: "+5°" }).click();
  await sleep(1200);
  await ask("look up 20 degrees");
  await sleep(4500);
  await page.getByRole("button", { name: /Center/ }).first().click();
  await sleep(2500);

  // E: object memory — restart the shell game, look away, ask, reveal
  await api("/api/demo/scene", { scene: "cups" });
  mark("E");
  const t0 = Date.now();
  const at = async (s) => { const w = t0 + s * 1000 - Date.now(); if (w > 0) await sleep(w); };
  await at(19.5);
  await api("/api/servo/nudge", { up_deg: 45 });   // the camera looks away
  mark("E_away");
  await at(23.0);
  await ask("where is the ball?");
  mark("E_ask");
  await at(34.0);
  mark("END");

  await cdp.send("Page.stopScreencast");
  await sleep(300);
  fs.writeFileSync(`${OUT}/timeline.json`, JSON.stringify({ frames, marks, regions, errors }, null, 1));
  console.log(JSON.stringify({ frames: frames.length, seconds: (frames.at(-1).t - frames[0].t).toFixed(1), errors, regions }));
  await browser.close();
})();

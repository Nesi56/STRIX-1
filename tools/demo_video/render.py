"""Edit the recorded take into a 90-second 1080p demo: title card, five feature segments with zooms and
burned-in captions, an impact card, crossfades. Writes JPEG frames, then ffmpeg encodes the MP4."""
import bisect, json, math, os, subprocess, sys
from functools import lru_cache
from multiprocessing import Pool
from PIL import Image, ImageDraw, ImageFilter, ImageFont

V = sys.argv[1]
TAKE = f"{V}/take"
OUT_FRAMES = f"{V}/out"
FPS = 25
W, H = 1920, 1080
PAGE_W, PAGE_H = 1920, 960      # recorded page size; the picture area of the video
BAND = H - PAGE_H                # caption band height (120 px)

# palette from the STRIX-1 console
BG = (11, 17, 15); PANEL = (20, 28, 25); INK = (230, 238, 234); MUTED = (143, 163, 154)
GREEN = (61, 220, 132); AMBER = (255, 176, 32)

F = "/usr/share/fonts/opentype/inter/"
MONO = "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Medium.ttf"
if not os.path.exists(MONO):
    MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

@lru_cache(None)
def font(path, size):
    return ImageFont.truetype(path, size)

tl = json.load(open(f"{TAKE}/timeline.json"))
FRAMES = tl["frames"]; TS = [f["t"] for f in FRAMES]; M = tl["marks"]; R = tl["regions"]

def src_frame(t):
    i = max(0, bisect.bisect_right(TS, t) - 1)
    return f"{TAKE}/frames/{FRAMES[i]['name']}"

def fit(x, y, w, h, pad=16):
    """2:1 crop covering a region (+ padding), clamped to the page."""
    x, y, w, h = x - pad, y - pad, w + 2 * pad, h + 2 * pad
    cw = max(w, 2 * h); ch = cw / 2
    cw, ch = min(cw, PAGE_W), min(ch, PAGE_H)
    cx, cy = x + w / 2, y + h / 2
    nx = min(max(0, cx - cw / 2), PAGE_W - cw)
    ny = min(max(0, cy - ch / 2), PAGE_H - ch)
    return (nx, ny, cw, ch)

def union(*rs):
    x0 = min(r[0] for r in rs); y0 = min(r[1] for r in rs)
    x1 = max(r[0] + r[2] for r in rs); y1 = max(r[1] + r[3] for r in rs)
    return (x0, y0, x1 - x0, y1 - y0)

FULL = (0, 0, PAGE_W, PAGE_H)
vid = R["video"]
CUPS = fit(560, 470, 560, 300, 0)
VIDEO = fit(*vid, 8)
LIVE3D = fit(*R["live3d"], 12)
SERVO = fit(*union(R["servo"], (vid[0], vid[1], 800, vid[3])), 8)
CHATR = (700, 350, 1220, 610)
MEMV = fit(*union(R["memory"], (vid[0], 480, 1010, 300)), 6)
L3 = R["live3d"]
LIVE3D_TIGHT = (L3[0] - 6, L3[1] - 6, L3[2] + 12, L3[3] + 12)  # used by the split layout
SEE3D = fit(*union((560, 480, 560, 300), R["live3d"]), 6)

def ease(s):
    s = min(1, max(0, s))
    return s * s * (3 - 2 * s)

def crop_at(kfs, t):
    """kfs: [(t, rect), ...]; eased interpolation between consecutive keyframes."""
    if t <= kfs[0][0]:
        return kfs[0][1]
    for (t0, r0), (t1, r1) in zip(kfs, kfs[1:]):
        if t <= t1:
            s = ease((t - t0) / (t1 - t0)) if t1 > t0 else 1
            return tuple(a + (b - a) * s for a, b in zip(r0, r1))
    return kfs[-1][1]

# ---------------------------------------------------------------- segments (output timeline)
QWEN_NOTE = "In this recording Qwen isn't loaded, so STRIX-1's built-in fallback writes the replies."
SEGS = [
    dict(kind="title", dur=5.0),
    dict(kind="clip", dur=10.0, src=(M["A"], M["A"] + 10.0), label="01 · ONE SCREEN",
         kfs=[(0, FULL), (10, (60, 30, 1800, 900))],
         caps=[(0, "Camera, sensors, memory, 3D view and Qwen on one screen, all running on the drone's Raspberry Pi 5.", "")]),
    dict(kind="clip", dur=12.0, src=(M["B"], M["B"] + 12.0), label="02 · SEES",
         kfs=[(0, VIDEO), (2.5, VIDEO), (5.0, CUPS), (12, CUPS)],
         caps=[(0, "YOLO finds objects in the live camera feed…", ""),
               (4.5, "…and estimates how far away each one is: cup 0.90 · ~0.7 m.", "")]),
    dict(kind="clip", dur=12.0, src=(M["C"] + 0.3, M["C"] + 12.3), label="03 · FEELS", split=True,
         kfs=[(0, LIVE3D_TIGHT), (12, LIVE3D_TIGHT)],
         caps=[(0, "The MPU6050 IMU, read 100 times a second, moves the 3D drone with every tilt.", ""),
               (6.0, "The DHT11 adds temperature and humidity, so Qwen can answer “how warm is it?”", "")]),
    dict(kind="clip", dur=13.0, src=(M["D"] + 0.5, M["D"] + 14.5), label="04 · AIMS",
         kfs=[(0, SERVO), (4.0, SERVO), (5.2, CHATR), (11.5, CHATR), (12.6, SERVO)],
         caps=[(0, "The camera rides on an SG90 servo: enable control, then tilt it in 5° steps.", ""),
               (4.4, "Or just ask “look up 20 degrees”: Qwen plans the move and the servo tilts the camera.", QWEN_NOTE)]),
    dict(kind="clip", dur=22.0, src=(M["E"], M["END"]), label="05 · REMEMBERS", speed=True,
         kfs=None, caps=None),  # filled below (keyed on source seconds)
    dict(kind="impact", dur=16.0),
]
# memory segment: keyframes and captions keyed on seconds since the shell game restarted
E = SEGS[5]
e_len = M["END"] - M["E"]
def e_out(s):
    return s / e_len * E["dur"]
away, ask = M["E_away"] - M["E"], M["E_ask"] - M["E"]
E["kfs"] = [(e_out(0), MEMV), (e_out(9.5), MEMV), (e_out(11), SEE3D), (e_out(18.5), SEE3D), (e_out(19.6), MEMV),
            (e_out(ask), MEMV), (e_out(ask + 0.9), CHATR), (e_out(29.6), CHATR), (e_out(30.6), MEMV)]
E["caps"] = [(e_out(0), "A cup goes over the ball. The memory marks it HIDDEN, with a timer.", ""),
             (e_out(9.5), "The cups are shuffled. The hidden ball moves with its cup, shown inside it in 3D.", ""),
             (e_out(away), "The camera looks away. Everything stays REMEMBERED, with out-of-sight timers.", ""),
             (e_out(ask), "“Where is the ball?” The servo aims back at the right cup; the reply says which way to turn.", QWEN_NOTE),
             (e_out(29.8), "The cup is lifted: “found under cup … memory was right.”", "")]

starts = []
acc = 0.0
for s in SEGS:
    starts.append(acc); acc += s["dur"]
TOTAL = acc
XF = 0.4  # crossfade

# ---------------------------------------------------------------- drawing helpers
def wrap(draw, text, fnt, width):
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if draw.textlength(t, font=fnt) <= width:
            cur = t
        else:
            lines.append(cur); cur = w_
    if cur:
        lines.append(cur)
    return lines

@lru_cache(64)
def band(label, text, note, speed):
    im = Image.new("RGB", (W, BAND), BG)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 2], fill=PANEL)
    d.text((40, 14), label, font=font(MONO, 22), fill=GREEN)
    right = "DEMO MODE · SIMULATED CAMERA & SENSORS" + ("  ·  1.5× SPEED" if speed else "")
    rw = d.textlength(right, font=font(MONO, 17))
    d.text((W - 40 - rw, 18), right, font=font(MONO, 17), fill=MUTED)
    size = 36
    while size > 24 and d.textlength(text, font=font(F + "Inter-SemiBold.otf", size)) > W - 80:
        size -= 1
    d.text((40, 46 if note else 54), text, font=font(F + "Inter-SemiBold.otf", size), fill=INK)
    if note:
        d.text((40, 90), note, font=font(F + "Inter-Regular.otf", 19), fill=MUTED)
    return im

@lru_cache(4)
def blurred_bg():
    im = Image.open(src_frame(M["A"] + 1)).resize((W, H)).filter(ImageFilter.GaussianBlur(14))
    return Image.blend(im, Image.new("RGB", (W, H), BG), 0.78)

def pill(d, x, y, text, fnt, fg, bg, pad=(18, 10)):
    tw = d.textlength(text, font=fnt)
    d.rounded_rectangle([x, y, x + tw + 2 * pad[0], y + fnt.size + 2 * pad[1]], radius=8, fill=bg)
    d.text((x + pad[0], y + pad[1] - 2), text, font=fnt, fill=fg)
    return x + tw + 2 * pad[0]

def title_card(t):
    im = blurred_bg().copy()
    d = ImageDraw.Draw(im)
    d.text((140, 250), "AI CO-PILOT FOR A RASPBERRY PI 5 DRONE", font=font(MONO, 30), fill=GREEN)
    d.text((132, 300), "STRIX-1", font=font(F + "InterDisplay-Black.otf", 230), fill=INK)
    d.text((140, 580), "It sees, aims its own camera, feels the drone,", font=font(F + "InterDisplay-Medium.otf", 54), fill=INK)
    d.text((140, 650), "and remembers what it can no longer see.", font=font(F + "InterDisplay-Medium.otf", 54), fill=INK)
    x = 140
    for chip in ["Local Qwen", "YOLO", "SG90 servo", "MPU6050 IMU", "DHT11", "No cloud"]:
        x = pill(d, x, 790, chip, font(MONO, 26), INK, PANEL) + 16
    a = ease(t / 0.6)
    return Image.blend(Image.new("RGB", (W, H), BG), im, a)

IMPACT = [
    ("Finds and keeps track", "Could help search and rescue, inspections and finding lost things: it remembers people and objects after they leave the frame."),
    ("Private and offline", "All the AI runs on the Raspberry Pi 5 on the drone. No cloud and no internet needed."),
    ("Parts anyone can get", "Pi 5, Camera Module 3, an SG90 servo, an MPU6050 and a DHT11 on a 250 mm frame."),
    ("Honest about what it knows", "Distances are labelled as estimates. It never fakes GPS, altitude or battery level."),
]

def impact_card(t):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    if t < 12.0:
        d.text((140, 110), "WHY IT MATTERS", font=font(MONO, 30), fill=GREEN)
        d.text((134, 150), "An AI co-pilot that runs on the drone", font=font(F + "InterDisplay-Bold.otf", 76), fill=INK)
        for i, (head, body) in enumerate(IMPACT):
            a = ease((t - 0.8 - i * 2.2) / 0.6)
            if a <= 0:
                continue
            y = 330 + i * 170
            layer = Image.new("RGB", (W, 160), BG)
            ld = ImageDraw.Draw(layer)
            ld.rectangle([140, 8, 146, 140], fill=AMBER if i == 0 else GREEN)
            ld.text((176, 0), head, font=font(F + "InterDisplay-SemiBold.otf", 46), fill=INK)
            for j, line in enumerate(wrap(ld, body, font(F + "Inter-Regular.otf", 32), W - 400)):
                ld.text((176, 64 + j * 42), line, font=font(F + "Inter-Regular.otf", 32), fill=MUTED)
            im.paste(Image.blend(Image.new("RGB", (W, 160), BG), layer, a), (0, y))
        if t > 11.5:
            im = Image.blend(im, Image.new("RGB", (W, H), BG), ease((t - 11.5) / 0.5))
        return im
    # end card
    a = ease((t - 12.0) / 0.6)
    end = blurred_bg().copy()
    d = ImageDraw.Draw(end)
    tw = d.textlength("STRIX-1", font=font(F + "InterDisplay-Black.otf", 200))
    d.text(((W - tw) / 2, 330), "STRIX-1", font=font(F + "InterDisplay-Black.otf", 200), fill=INK)
    for k, (txt, fnt, col, y) in enumerate([
        ("Sees · aims · feels · remembers — on the drone", font(F + "InterDisplay-Medium.otf", 46), INK, 590),
        ("github.com/Nesi56/STRIX-1", font(MONO, 40), GREEN, 690)]):
        w_ = d.textlength(txt, font=fnt)
        d.text(((W - w_) / 2, y), txt, font=fnt, fill=col)
    out = Image.blend(Image.new("RGB", (W, H), BG), end, a)
    if t > 15.4:
        out = Image.blend(out, Image.new("RGB", (W, H), (0, 0, 0)), ease((t - 15.4) / 0.6))
    return out

@lru_cache(2)
def sensor_side(width):
    im = Image.new("RGB", (width, PAGE_H), BG)
    d = ImageDraw.Draw(im)
    blocks = [("MPU6050 IMU", GREEN, ["6-axis accelerometer + gyroscope", "I2C · pins 3 and 5 · 3.3 V", "100 readings per second",
                                      "roll · pitch · relative yaw"]),
              ("DHT11", AMBER, ["temperature + humidity", "GPIO4 · pin 7 · kernel driver", "a reading every 3 seconds"])]
    y = 150
    for head, col, lines in blocks:
        d.rectangle([90, y + 8, 96, y + 52 + 44 * len(lines)], fill=col)
        d.text((126, y), head, font=font(F + "InterDisplay-Bold.otf", 64), fill=INK)
        for j, line in enumerate(lines):
            d.text((128, y + 92 + j * 44), line, font=font(F + "Inter-Regular.otf", 32), fill=MUTED)
        y += 140 + 44 * len(lines) + 60
    return im

def clip_frame(seg, lt):
    s0, s1 = seg["src"]
    st = s0 + (lt / seg["dur"]) * (s1 - s0)
    img = Image.open(src_frame(st))
    x, y, w, h = crop_at(seg["kfs"], lt)
    frame = Image.new("RGB", (W, H), BG)
    if seg.get("split"):
        pw = int(round(w * PAGE_H / h))
        pic = img.resize((pw, PAGE_H), Image.BICUBIC, box=(x, y, x + w, y + h))
        frame.paste(sensor_side(W - pw), (0, 0))
        frame.paste(pic, (W - pw, 0))
    else:
        pic = img.resize((W, PAGE_H), Image.BICUBIC, box=(x, y, x + w, y + h))
        frame.paste(pic, (0, 0))
    cap = next((c for c in reversed(seg["caps"]) if lt >= c[0]), seg["caps"][0])
    frame.paste(band(seg["label"], cap[1], cap[2], bool(seg.get("speed"))), (0, PAGE_H))
    return frame

def render_seg(i, t):
    seg = SEGS[i]
    lt = min(max(0.0, t - starts[i]), seg["dur"] - 1e-3)
    if seg["kind"] == "title":
        return title_card(lt)
    if seg["kind"] == "impact":
        return impact_card(lt)
    return clip_frame(seg, lt)

def render(n):
    t = n / FPS
    i = max(0, bisect.bisect_right(starts, t) - 1)
    frame = render_seg(i, t)
    # crossfade around segment boundaries
    for b_i in (i, i + 1):
        if 0 < b_i < len(SEGS):
            b = starts[b_i]
            if abs(t - b) < XF / 2:
                a = (t - (b - XF / 2)) / XF
                prev, nxt = render_seg(b_i - 1, t), render_seg(b_i, t)
                frame = Image.blend(prev, nxt, ease(a))
    frame.save(f"{OUT_FRAMES}/{n:05d}.jpg", quality=92)
    return n

if __name__ == "__main__":
    os.makedirs(OUT_FRAMES, exist_ok=True)
    n_frames = int(round(TOTAL * FPS))
    only = [int(a) for a in sys.argv[2:]]
    todo = only or list(range(n_frames))
    with Pool(4) as pool:
        for k, _ in enumerate(pool.imap_unordered(render, todo, chunksize=8)):
            if k % 250 == 0:
                print(f"{k}/{len(todo)}", flush=True)
    print("frames:", len(todo), "total seconds:", TOTAL)

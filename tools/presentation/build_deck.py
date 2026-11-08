"""STRIX-1 'How it works' deck: 16:9 PDF with real (editable) text, vector shapes and embedded images,
made to import cleanly into Canva. Fonts: Barlow (in Canva's library) + JetBrains Mono.

Usage (see docs/PRESENTATION_SCRIPT.md):
  1. <dir>/fonts/Barlow-{Regular,Medium,SemiBold,Bold,ExtraBold}.ttf from github.com/google/fonts (ofl/barlow)
  2. node tools/presentation/capture.cjs <dir>/img   (with scripts/run_mock.sh running; STRIX_URL, STRIX_TOKEN)
  3. python3 tools/presentation/build_deck.py <dir> docs/presentation/STRIX-1_How_It_Works.pdf docs/img/wiring.png
"""
import os, sys
from PIL import Image
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

D = sys.argv[1]
OUT = sys.argv[2]
WIRING = sys.argv[3]
IMG, FONTS, TMP = f"{D}/img", f"{D}/fonts", f"{D}/tmp"
os.makedirs(TMP, exist_ok=True)

PW, PH = 960, 540
M = 48  # margin

for name in ("Regular", "Medium", "SemiBold", "Bold", "ExtraBold"):
    pdfmetrics.registerFont(TTFont(f"Barlow-{name}", f"{FONTS}/Barlow-{name}.ttf"))
pdfmetrics.registerFont(TTFont("JetBrainsMono-Medium", "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Medium.ttf"))
pdfmetrics.registerFont(TTFont("JetBrainsMono-Bold", "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Bold.ttf"))

BG = HexColor("#0B110F"); PANEL = HexColor("#141C19"); LINE = HexColor("#26332E")
INK = HexColor("#E6EEEA"); MUTED = HexColor("#8FA39A")
GREEN = HexColor("#3DDC84"); AMBER = HexColor("#FFB020")
SERVO = HexColor("#FF7A4D"); IMU = HexColor("#3FC1BA"); DHT = HexColor("#7FA8F0"); CAM = HexColor("#C9D3CF")

# ---------------------------------------------------------------- images (cropped, compressed)
def prep(name, box=None, max_w=1800, out=None):
    im = Image.open(f"{IMG}/{name}.png").convert("RGB") if os.path.exists(f"{IMG}/{name}.png") else Image.open(name).convert("RGB")
    if box:
        im = im.crop(box)
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    path = f"{TMP}/{out or os.path.basename(name)}.jpg"
    im.save(path, quality=88, optimize=True)
    return path, im.size

VIDEO_BOX = (2, 252, 2294, 1542)  # the camera image inside the captured video panel (2x scale)
P = {
    "console": prep("console", max_w=1800),
    "sees": prep("sees", VIDEO_BOX),
    "hidden": prep("hidden", VIDEO_BOX),
    "reveal": prep("reveal", VIDEO_BOX),
    "drone3d": prep("drone3d"),
    "world3d": prep("world3d"),
    "servo": prep("servo"),
    "chat_where": prep("chat_where", (0, 0, 880, 620)),
    "memory_reveal": prep("memory_reveal"),
    "wiring": prep(WIRING, max_w=1122, out="wiring"),
}

# ---------------------------------------------------------------- drawing helpers (y measured from the top)
c = canvas.Canvas(OUT, pagesize=(PW, PH))
c.setTitle("STRIX-1 — How it works")
c.setAuthor("STRIX-1 team")
c.setSubject("How STRIX-1 works and why it matters")

def Y(t):
    return PH - t

def text(x, top, s, font="Barlow-Regular", size=16, color=INK, anchor="l"):
    c.setFillColor(color); c.setFont(font, size)
    base = Y(top) - size * 0.8
    if anchor == "c":
        c.drawCentredString(x, base, s)
    elif anchor == "r":
        c.drawRightString(x, base, s)
    else:
        c.drawString(x, base, s)

def wrap(s, font, size, width):
    words, lines, cur = s.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if pdfmetrics.stringWidth(t, font, size) <= width:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines

def para(x, top, s, width, font="Barlow-Regular", size=16, color=MUTED, leading=1.3):
    for i, line in enumerate(wrap(s, font, size, width)):
        text(x, top + i * size * leading, line, font, size, color)
    return top + len(wrap(s, font, size, width)) * size * leading

def rect(x, top, w, h, fill=PANEL, stroke=None, r=0, sw=1):
    c.setFillColor(fill)
    if stroke is not None:
        c.setStrokeColor(stroke); c.setLineWidth(sw)
    if r:
        c.roundRect(x, Y(top + h), w, h, r, fill=1, stroke=1 if stroke is not None else 0)
    else:
        c.rect(x, Y(top + h), w, h, fill=1, stroke=1 if stroke is not None else 0)

def image(key, x, top, w=None, h=None, border=True):
    path, (iw, ih) = P[key]
    if w and not h:
        h = w * ih / iw
    elif h and not w:
        w = h * iw / ih
    c.drawImage(path, x, Y(top + h), w, h)
    if border:
        c.setStrokeColor(LINE); c.setLineWidth(1); c.rect(x, Y(top + h), w, h, fill=0, stroke=1)
    return w, h

def chip(x, top, s, color, size=11, fill=PANEL):
    w = pdfmetrics.stringWidth(s, "JetBrainsMono-Medium", size) + 18
    rect(x, top, w, size + 12, fill=fill, stroke=color, r=4)
    text(x + 9, top + 6.5, s, "JetBrainsMono-Medium", size, color)
    return x + w

def arrow(x0, t0, x1, t1, color=MUTED, width=1.6):
    import math
    c.setStrokeColor(color); c.setFillColor(color); c.setLineWidth(width)
    ya, yb = Y(t0), Y(t1)
    ang = math.atan2(yb - ya, x1 - x0)
    tip_back = 7
    xe, ye = x1 - tip_back * math.cos(ang), yb - tip_back * math.sin(ang)
    c.line(x0, ya, xe, ye)
    p = c.beginPath()
    p.moveTo(x1, yb)
    p.lineTo(x1 - 9 * math.cos(ang) + 4.5 * math.sin(ang), yb - 9 * math.sin(ang) - 4.5 * math.cos(ang))
    p.lineTo(x1 - 9 * math.cos(ang) - 4.5 * math.sin(ang), yb - 9 * math.sin(ang) + 4.5 * math.cos(ang))
    p.close()
    c.drawPath(p, fill=1, stroke=0)

page_no = [0]
TOTAL = 11

def slide(eyebrow, title=None, subtitle=None):
    if page_no[0]:
        c.showPage()
    page_no[0] += 1
    rect(0, 0, PW, PH, fill=BG)
    if eyebrow:
        text(M, 38, eyebrow, "JetBrainsMono-Medium", 11, GREEN)
    if title:
        text(M, 56, title, "Barlow-ExtraBold", 34, INK)
    if subtitle:
        text(M, 100, subtitle, "Barlow-Regular", 16, MUTED)
    text(M, PH - 26, "STRIX-1", "JetBrainsMono-Medium", 9, MUTED)
    text(PW - M, PH - 26, f"{page_no[0]:02d} / {TOTAL:02d}", "JetBrainsMono-Medium", 9, MUTED, anchor="r")

def bullet(x, top, head, body, color, width, head_size=17, body_size=14):
    rect(x, top + 3, 4, 16, fill=color)
    text(x + 16, top, head, "Barlow-SemiBold", head_size, INK)
    return para(x + 16, top + head_size * 1.35, body, width - 16, "Barlow-Regular", body_size, MUTED, 1.32)

# ================================================================ 1 · opener
slide("HOW IT WORKS")
hs = 50
while max(pdfmetrics.stringWidth(t_, "Barlow-ExtraBold", hs) for t_ in ("Sense. Understand.", "Remember. Advise.")) > 380:
    hs -= 1
text(M, 74, "Sense. Understand.", "Barlow-ExtraBold", hs, INK)
text(M, 74 + hs * 1.12, "Remember. Advise.", "Barlow-ExtraBold", hs, GREEN)
para(M, 196, "Everything runs on the Raspberry Pi 5 on the drone: camera, sensors, object memory and a local Qwen model. No cloud.",
     370, "Barlow-Regular", 18, MUTED, 1.35)
for row, items in enumerate([[("Camera Module 3", CAM), ("YOLO", GREEN), ("MPU6050", IMU)],
                              [("DHT11", DHT), ("SG90 servo", SERVO), ("Qwen", GREEN)]]):
    x = M
    for s_, col in items:
        x = chip(x, 300 + row * 36, s_, col) + 8
image("console", 452, 74, w=460)
text(452, 74 + 230 + 10, "The STRIX-1 console in a browser (demo mode)", "JetBrainsMono-Medium", 9, MUTED)

# ================================================================ 2 · the loop
slide("THE LOOP", "One loop, all on the drone", "Sensors in, AI on the Raspberry Pi 5, actions and advice out.")
cols = [(M, "SENSE"), (M + 300, "THINK · RASPBERRY PI 5"), (M + 640, "ACT")]
for x, lab in cols:
    text(x, 140, lab, "JetBrainsMono-Bold", 11, MUTED)
def box(x, top, w, h, head, body, color):
    rect(x, top, w, h, fill=PANEL, stroke=LINE, r=6)
    rect(x, top, 5, h, fill=color)
    text(x + 18, top + 13, head, "Barlow-SemiBold", 17, INK)
    text(x + 18, top + 36, body, "Barlow-Regular", 13, MUTED)
sense = [("Camera Module 3 Wide", "live video, 102° lens", CAM), ("MPU6050 IMU", "motion, 100 times a second", IMU),
         ("DHT11", "temperature + humidity", DHT)]
for i, (h_, b_, col) in enumerate(sense):
    box(M, 165 + i * 90, 240, 66, h_, b_, col)
think = [("YOLO", "finds objects + distance", GREEN), ("Sensor fusion", "roll · pitch · yaw", IMU),
         ("Object memory", "seen · remembered · hidden", AMBER), ("Qwen (local)", "plans, sees, answers", GREEN)]
for i, (h_, b_, col) in enumerate(think):
    box(M + 300, 165 + i * 68, 270, 56, h_, b_, col)
act = [("SG90 camera servo", "aims the camera", SERVO), ("Advice for the pilot", "turn, move closer, level", GREEN),
       ("Live console", "any browser on the Wi-Fi", CAM)]
for i, (h_, b_, col) in enumerate(act):
    box(M + 640, 165 + i * 90, 224, 66, h_, b_, col)
arrow(M + 246, 290, M + 292, 290, MUTED, 2)
arrow(M + 576, 290, M + 632, 290, MUTED, 2)
text(M, 452, "IMU 100 Hz  ·  YOLO about 3 Hz  ·  live updates 20 Hz  ·  the pilot always flies the drone",
     "JetBrainsMono-Medium", 11, MUTED)

# ================================================================ 3 · hardware
slide("THE HARDWARE", "What's on the drone")
image("wiring", M, 100, h=392)
x0 = M + 340
y = 110
y = bullet(x0, y, "SG90 camera servo", "Signal on GPIO18 (pin 12). Powered from the 52Pi PD board's 5 V, sharing ground with the Pi.", SERVO, 520) + 16
y = bullet(x0, y, "MPU6050 IMU", "I2C: SDA pin 3, SCL pin 5, 3.3 V on pin 1, ground on pin 6.", IMU, 520) + 16
y = bullet(x0, y, "DHT11 temperature + humidity", "Data on GPIO4 (pin 7), 3.3 V on pin 17, ground on pin 9. Read by the Pi's kernel driver.", DHT, 520) + 16
y = bullet(x0, y, "Camera Module 3 Wide", "CSI ribbon cable, 102° field of view, streamed at 720p.", CAM, 520) + 16
y = bullet(x0, y, "Raspberry Pi 5 (16 GB)", "On a 250 mm ZMR250 frame, powered by a USB-C PD power bank.", GREEN, 520)

# ================================================================ 4 · sees
slide("IT SEES", "YOLO finds objects and estimates distance")
w, h = image("sees", M, 100, w=540)
x0 = M + 570
y = 108
y = bullet(x0, y, "Object detection", "YOLOX-Tiny runs on the Pi about 3 times a second. An optional model adds 600 everyday objects, including pens.", GREEN, 296) + 18
y = bullet(x0, y, "Distance from the lens", "A known object size and the 102° lens give the distance:", CAM, 296) + 6
rect(x0 + 16, y, 270, 34, fill=PANEL, stroke=LINE, r=4)
text(x0 + 30, y + 10, "distance ≈ f · H / h", "JetBrainsMono-Bold", 14, INK)
y += 52
y = bullet(x0, y, "Honest numbers", "Every distance is shown as an estimate (about ±30 %), like “cup ~0.7 m”.", AMBER, 296)

# ================================================================ 5 · feels
slide("IT FEELS THE DRONE", "Motion and air from real sensors")
image("drone3d", M, 100, h=372)
x0 = M + 470
y = 108
y = bullet(x0, y, "MPU6050 IMU, 100 times a second", "Accelerometer and gyroscope fused into roll, pitch and yaw. The 3D drone moves with the real one.", IMU, 394) + 16
y = bullet(x0, y, "Used everywhere", "It keeps remembered objects in the right place when the drone turns, and lets Qwen say “level the drone first”.", IMU, 394) + 16
y = bullet(x0, y, "DHT11 temperature and humidity", "Read by the Pi's kernel driver every 3 seconds, with session min and max.", DHT, 394) + 16
y = bullet(x0, y, "Grounded answers", "Ask “how warm is it?” and Qwen answers from the sensor, not from a guess.", DHT, 394)

# ================================================================ 6 · aims
slide("IT AIMS THE CAMERA", "Qwen controls the SG90 servo")
image("servo", M, 100, w=300)
x0 = M + 340
steps = [("1  You ask", "“Find the person and keep them centered.”", "Barlow-Medium", 15, INK),
         ("2  Qwen plans", '{"plan": [{"skill": "find", "target": "person"}, {"skill": "track", "target": "person", "seconds": 8}]}', "JetBrainsMono-Medium", 11.5, GREEN),
         ("3  The servo moves", "Tilts the camera until the person is centered, then Qwen answers with distance and direction.", "Barlow-Regular", 14, MUTED)]
y = 100
for i, (head, body, f, s, col) in enumerate(steps):
    h_box = 92
    rect(x0, y, 520, h_box, fill=PANEL, stroke=LINE, r=6)
    text(x0 + 18, y + 14, head, "Barlow-SemiBold", 17, SERVO if i == 2 else INK)
    para(x0 + 18, y + 42, body, 484, f, s, col, 1.35)
    if i < 2:
        arrow(x0 + 40, y + h_box + 2, x0 + 40, y + h_box + 16, MUTED, 1.6)
    y += h_box + 18
para(M, 384, "Safe by design: limits 30–150°, at most 60° per second, and EMERGENCY STOP (Space) turns it off until the pilot enables it again.",
     300, "Barlow-Regular", 13.5, MUTED, 1.35)

# ================================================================ 7 · remembers
slide("IT REMEMBERS", "Object permanence: seen, remembered, hidden")
x = M
for s, col in [("SEEN  in view now", GREEN), ("REMEMBERED  out of view, with a timer", CAM), ("HIDDEN  under a cup, moves with it", AMBER)]:
    x = chip(x, 98, s, col, 11) + 10
pics = [("hidden", "1  A cup covers the ball: it's marked HIDDEN."),
        ("world3d", "2  The cups are shuffled: the ball moves with its cup."),
        ("chat_where", "3  “Where is the ball?” The camera aims back at the right cup.")]
pw_ = 278
for i, (k, cap) in enumerate(pics):
    x = M + i * (pw_ + 13)
    path, (iw, ih) = P[k]
    hh = 200
    # fit inside pw_ x hh
    s = min(pw_ / iw, hh / ih)
    dw, dh = iw * s, ih * s
    rect(x, 140, pw_, hh, fill=PANEL)
    c.drawImage(path, x + (pw_ - dw) / 2, Y(140 + (hh + dh) / 2), dw, dh)
    c.setStrokeColor(LINE); c.rect(x, Y(140 + hh), pw_, hh, fill=0, stroke=1)
    para(x, 352, cap, pw_, "Barlow-Medium", 14, INK, 1.3)
rect(M, 420, 864, 46, fill=PANEL, stroke=AMBER, r=6)
text(M + 18, 434, "When the cup is lifted:  “found under cup after 22 s — memory was right.”", "Barlow-SemiBold", 17, AMBER)

# ================================================================ 8 · edge
slide("BUILT FOR THE EDGE", "Small, fast, cool enough to fly")
tiles = [("0", "cloud calls: video never leaves the drone"), ("2B", "parameter Qwen vision model, local"),
         ("100 Hz", "IMU readings"), ("160+", "automated tests")]
for i, (big, small) in enumerate(tiles):
    x = M + i * 219
    rect(x, 100, 207, 120, fill=PANEL, stroke=LINE, r=6)
    text(x + 18, 116, big, "Barlow-ExtraBold", 44, GREEN)
    para(x + 18, 170, small, 175, "Barlow-Regular", 13.5, MUTED, 1.3)
text(M, 250, "Heat guard", "Barlow-SemiBold", 19, INK)
para(M, 276, "The Pi watches its own temperature and slows the AI down before it overheats.", 860, "Barlow-Regular", 15, MUTED)
bx, bw_, bt = M, 864, 316
lo, hi = 50, 90
def tx(t):
    return bx + (t - lo) / (hi - lo) * bw_
segs = [(50, 70, HexColor("#1F6E46")), (70, 75, HexColor("#8A7A1E")), (75, 85, HexColor("#A5541E")), (85, 90, HexColor("#9C2F2F"))]
for a, b, col in segs:
    rect(tx(a), bt, tx(b) - tx(a), 18, fill=col)
for t, lab in [(70, "70 °C  automatic comments pause"), (75, "75 °C  YOLO slows down"), (85, "85 °C  the Pi throttles")]:
    c.setStrokeColor(INK); c.setLineWidth(1.2); c.line(tx(t), Y(bt - 4), tx(t), Y(bt + 22))
for t, lab, row in [(70, "70 °C · comments pause", 0), (75, "75 °C · YOLO slows", 1), (85, "85 °C · Pi throttles", 0)]:
    text(tx(t), bt + 28 + row * 20, lab, "JetBrainsMono-Medium", 11, INK, anchor="c")
text(M, 410, "Defaults chosen for heat: 720p camera stream · light YOLO model · 2B Qwen · text-only automatic comments",
     "JetBrainsMono-Medium", 11, MUTED)

# ================================================================ 9 · impact
slide("WHY IT MATTERS", "Impact on today's world")
cards = [
    ("Search, rescue and inspection", "Drones already help search for people and inspect roofs, bridges and crops. A co-pilot that remembers what it saw, even after it leaves the frame, makes that job easier.", AMBER),
    ("Private and offline", "No cloud: the video stays on the drone, and it keeps working where there is no signal, which is often exactly where help is needed.", GREEN),
    ("Affordable and open", "Built from parts anyone can buy and open-source code anyone can learn from and extend.", IMU),
    ("Trustworthy AI", "It labels its estimates, never fakes GPS or battery levels, and the human pilot stays in control.", DHT),
]
for i, (h_, b_, col) in enumerate(cards):
    x = M + (i % 2) * 438
    top = 100 + (i // 2) * 180
    rect(x, top, 426, 164, fill=PANEL, stroke=LINE, r=6)
    rect(x, top, 426, 4, fill=col)
    text(x + 20, top + 20, h_, "Barlow-SemiBold", 20, INK)
    para(x + 20, top + 52, b_, 386, "Barlow-Regular", 14.5, MUTED, 1.36)

# ================================================================ 10 · next
slide("WHAT'S NEXT", "Where STRIX-1 goes from here")
nexts = [("Fly it", "Validate the servo, IMU and DHT11 in flight and tune the motion filter.", GREEN),
         ("Pan as well as tilt", "A second servo so the camera can turn without turning the drone.", SERVO),
         ("See depth", "A small depth sensor for better distances and a true 3D memory.", IMU),
         ("Faster vision", "A Raspberry Pi AI accelerator for quicker, cooler detection.", DHT)]
for i, (h_, b_, col) in enumerate(nexts):
    top = 106 + i * 86
    rect(M, top, 864, 72, fill=PANEL, stroke=LINE, r=6)
    rect(M, top, 5, 72, fill=col)
    text(M + 24, top + 14, h_, "Barlow-SemiBold", 20, INK)
    text(M + 24, top + 42, b_, "Barlow-Regular", 15, MUTED)

# ================================================================ 11 · thanks
slide(None)
text(M, 150, "STRIX-1", "Barlow-ExtraBold", 92, INK)
text(M, 262, "Sees · aims · feels · remembers — on the drone.", "Barlow-Medium", 24, MUTED)
text(M, 318, "Thank you", "Barlow-ExtraBold", 34, GREEN)
text(M, 372, "github.com/Nesi56/STRIX-1", "JetBrainsMono-Bold", 18, INK)
text(M, 400, "Try the demo without hardware:  scripts/run_mock.sh", "JetBrainsMono-Medium", 12, MUTED)
qw = qr.QrCodeWidget("https://github.com/Nesi56/STRIX-1")
qw.barFillColor = BG
b0, b1, b2, b3 = qw.getBounds()
size = 190
dr = Drawing(size, size, transform=[size / (b2 - b0), 0, 0, size / (b3 - b1), 0, 0])
dr.add(qw)
rect(PW - M - size - 24, 150, size + 24, size + 24, fill=HexColor("#FFFFFF"), r=8)
renderPDF.draw(dr, c, PW - M - size - 12, Y(150 + 12 + size))
text(PW - M - (size + 24) / 2, 150 + size + 34, "Scan for the code", "JetBrainsMono-Medium", 10, MUTED, anchor="c")

c.showPage()
c.save()
print("pages:", page_no[0], "size:", os.path.getsize(OUT))

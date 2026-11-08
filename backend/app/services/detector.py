"""Object detection ("YOLO stream") and monocular geometry.

Engines
-------
* ONNX detectors (COCO-80):
  - ``yolov8``: Ultralytics YOLOv8 / YOLO11 export, output ``(1, 4+nc, N)``, boxes decoded,
    inputs RGB scaled to 0..1.
  - ``yolox``: OpenCV Zoo YOLOX export, output ``(1, N, 5+nc)``, raw grid offsets that need
    decoding with strides 8/16/32, inputs RGB 0..255 (no normalisation).
  - ``yolov5``: decoded ``(1, N, 5+nc)`` with objectness, inputs RGB 0..1.
  Inference uses onnxruntime when installed (faster on ARM), otherwise OpenCV DNN.
* ``hog``: OpenCV's built-in HOG people detector — fallback when no model file exists.

Images are letterboxed top-left with grey (114) padding, so un-letterboxing is a division
by the resize ratio.

Distance / bearing
------------------
Monocular range from known object size with a pinhole model::

    f_px = (W / 2) / tan(HFOV / 2)          # Camera Module 3 Wide: HFOV ≈ 102°
    distance ≈ f_px · H_object / h_bbox_px  # H_object from a per-class size prior
    bearing  = atan((x_center − W/2) / f_px)  (+ = right of the optical axis)

This is an *estimate*: it assumes the typical object height (e.g. a standing adult of
1.7 m), an upright object fully inside the frame, and ignores lens distortion. Expect
±30 % or worse. Boxes cut off by the frame edge only give an upper bound. Classes without
a size prior get no distance.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

COCO80 = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light",
    "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard",
    "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
]

# Typical object heights in metres (upright, fully visible). Used only for range estimates.
SIZE_PRIORS_M: dict[str, float] = {
    "person": 1.70, "bicycle": 1.05, "car": 1.50, "motorcycle": 1.10, "bus": 3.00, "truck": 3.00,
    "boat": 1.50, "bird": 0.25, "cat": 0.30, "dog": 0.55, "horse": 1.60, "sheep": 0.90, "cow": 1.40,
    "backpack": 0.50, "umbrella": 1.00, "suitcase": 0.65, "sports ball": 0.22, "bottle": 0.25,
    "cup": 0.10, "chair": 0.90, "couch": 0.85, "potted plant": 0.60, "bed": 0.60, "dining table": 0.75,
    "toilet": 0.75, "tv": 0.55, "laptop": 0.25, "cell phone": 0.15, "refrigerator": 1.75, "book": 0.24,
    "clock": 0.30, "vase": 0.30, "teddy bear": 0.35, "bench": 0.85, "fire hydrant": 0.75,
}

SYNONYMS = {
    "people": "person", "persons": "person", "human": "person", "humans": "person", "man": "person",
    "woman": "person", "pedestrian": "person", "kid": "person", "child": "person", "bike": "bicycle",
    "motorbike": "motorcycle", "cars": "car", "phone": "cell phone", "mobile": "cell phone",
    "sofa": "couch", "table": "dining table", "plant": "potted plant", "television": "tv",
    "monitor": "tv", "ball": "sports ball", "chairs": "chair", "dogs": "dog", "cats": "cat",
    "bottles": "bottle", "cups": "cup", "bag": "backpack",
}


def normalize_label(text: str, vocabulary: list[str] | None = None) -> str | None:
    """Map free text ('people', 'Cars') to a vocabulary class, or None."""
    vocab = vocabulary or COCO80
    t = " ".join(text.lower().strip().replace("_", " ").split())
    if t in vocab:
        return t
    t = SYNONYMS.get(t, t)
    if t in vocab:
        return t
    if t.endswith("s") and t[:-1] in vocab:
        return t[:-1]
    return None


@dataclass
class Detection:
    label: str
    confidence: float
    bbox: list[float]  # normalised [x, y, w, h] in 0..1
    distance_m: float | None = None
    distance_kind: str = "none"  # "estimate" | "upper_bound" | "none"
    bearing_deg: float = 0.0  # + right of camera axis
    elevation_deg: float = 0.0  # + above camera axis
    source: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("extra")
        d.update(self.extra)
        return d


def focal_px(width: int, hfov_deg: float) -> float:
    return (width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


def estimate_geometry(label: str, box_px: tuple[float, float, float, float], frame_w: int, frame_h: int,
                      hfov_deg: float) -> tuple[float | None, str, float, float]:
    """Return (distance_m, kind, bearing_deg, elevation_deg) for a pixel box (x, y, w, h)."""
    x, y, w, h = box_px
    f = focal_px(frame_w, hfov_deg)
    cx, cy = x + w / 2.0, y + h / 2.0
    bearing = math.degrees(math.atan((cx - frame_w / 2.0) / f))
    elevation = math.degrees(math.atan((frame_h / 2.0 - cy) / f))
    prior = SIZE_PRIORS_M.get(label)
    if prior is None or h < 4:
        return None, "none", bearing, elevation
    dist = f * prior / h
    cropped = y <= 2 or y + h >= frame_h - 2
    return round(dist, 2), ("upper_bound" if cropped else "estimate"), bearing, elevation


# ----------------------------------------------------------------------------
# Pre/post-processing (pure numpy, unit-tested)
# ----------------------------------------------------------------------------
def letterbox(img, size: tuple[int, int]):
    """Resize keeping aspect ratio into a (h, w) canvas, padded bottom/right with 114."""
    import cv2
    import numpy as np

    th, tw = size
    h, w = img.shape[:2]
    r = min(th / h, tw / w)
    nw, nh = int(round(w * r)), int(round(h * r))
    out = np.full((th, tw, 3), 114, dtype=np.uint8)
    out[:nh, :nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    return out, r


def decode_yolov8(out, conf: float):
    """(1, 4+nc, N) or (1, N, 4+nc) → boxes xywh (input px), scores, class ids."""
    import numpy as np

    p = np.squeeze(out)
    if p.shape[0] < p.shape[1]:
        p = p.T  # → (N, 4+nc)
    scores_all = p[:, 4:]
    cls = scores_all.argmax(axis=1)
    scores = scores_all[np.arange(len(p)), cls]
    keep = scores >= conf
    b = p[keep, :4]
    boxes = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 2], b[:, 3]], axis=1)
    return boxes, scores[keep], cls[keep]


def _yolox_grids(input_h: int, input_w: int, strides=(8, 16, 32)):
    import numpy as np

    grids, expanded = [], []
    for s in strides:
        hs, ws = input_h // s, input_w // s
        xv, yv = np.meshgrid(np.arange(ws), np.arange(hs))
        g = np.stack((xv, yv), 2).reshape(-1, 2)
        grids.append(g)
        expanded.append(np.full((g.shape[0], 1), s))
    return np.concatenate(grids, 0), np.concatenate(expanded, 0)


def decode_yolox(out, input_h: int, input_w: int, conf: float):
    """Raw YOLOX head output (1, N, 5+nc) → boxes xywh (input px), scores, class ids."""
    import numpy as np

    p = np.squeeze(out).astype(np.float32).copy()
    grids, strides = _yolox_grids(input_h, input_w)
    if p.shape[0] != grids.shape[0]:
        raise ValueError(f"YOLOX output has {p.shape[0]} anchors, expected {grids.shape[0]} for {input_w}x{input_h}")
    p[:, :2] = (p[:, :2] + grids) * strides
    p[:, 2:4] = np.exp(p[:, 2:4]) * strides
    scores_all = p[:, 4:5] * p[:, 5:]
    cls = scores_all.argmax(axis=1)
    scores = scores_all[np.arange(len(p)), cls]
    keep = scores >= conf
    b = p[keep, :4]
    boxes = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 2], b[:, 3]], axis=1)
    return boxes, scores[keep], cls[keep]


def decode_yolov5(out, conf: float):
    """Decoded YOLOv5-style output (1, N, 5+nc) with objectness."""
    import numpy as np

    p = np.squeeze(out)
    scores_all = p[:, 4:5] * p[:, 5:]
    cls = scores_all.argmax(axis=1)
    scores = scores_all[np.arange(len(p)), cls]
    keep = scores >= conf
    b = p[keep, :4]
    boxes = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 2], b[:, 3]], axis=1)
    return boxes, scores[keep], cls[keep]


def nms(boxes, scores, cls, conf: float, iou: float):
    """Class-aware NMS (works on OpenCV 4.6 without NMSBoxesBatched)."""
    import cv2
    import numpy as np

    keep_all: list[int] = []
    for c in np.unique(cls):
        idx = np.where(cls == c)[0]
        k = cv2.dnn.NMSBoxes(boxes[idx].tolist(), scores[idx].astype(float).tolist(), conf, iou)
        keep_all.extend(idx[np.array(k).reshape(-1)].tolist() if len(k) else [])
    return sorted(keep_all, key=lambda i: -float(scores[i]))


# ----------------------------------------------------------------------------
# Engines
# ----------------------------------------------------------------------------
class OnnxDetector:
    name = "onnx"

    def __init__(self, model_path: str, fmt: str = "auto", labels: list[str] | None = None,
                 conf: float = 0.35, iou: float = 0.45, imgsz: int = 0) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"detector model not found: {model_path}")
        self.path = path
        self.labels = labels or COCO80
        self.conf, self.iou = conf, iou
        self.engine = "opencv-dnn"
        self._sess = None
        self._net = None
        in_h = in_w = imgsz or 640
        try:
            import onnxruntime as ort  # type: ignore[import-not-found]

            so = ort.SessionOptions()
            so.intra_op_num_threads = 2  # leave cores for camera encoding and the LLM
            self._sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
            inp = self._sess.get_inputs()[0]
            self._in_name = inp.name
            shp = inp.shape
            if len(shp) == 4 and isinstance(shp[2], int) and isinstance(shp[3], int):
                in_h, in_w = shp[2], shp[3]
            self.engine = "onnxruntime"
        except ImportError:
            import cv2

            self._net = cv2.dnn.readNetFromONNX(str(path))
        self.input_hw = (in_h, in_w)
        self.format = self._resolve_format(fmt)
        log.info("detector: %s (%s, %s, input %dx%d)", path.name, self.format, self.engine, in_w, in_h)

    def _forward(self, blob):
        if self._sess is not None:
            return self._sess.run(None, {self._in_name: blob})[0]
        self._net.setInput(blob)
        return self._net.forward()

    def _resolve_format(self, fmt: str) -> str:
        if fmt != "auto":
            return fmt
        if "yolox" in self.path.name.lower():
            return "yolox"
        import numpy as np

        out = self._forward(self._blob(np.zeros((self.input_hw[0], self.input_hw[1], 3), np.uint8), "yolov8"))
        s = np.squeeze(out).shape
        nc = len(self.labels)
        if len(s) == 2 and (s[0] == 4 + nc or s[1] == 4 + nc):
            return "yolov8"
        return "yolov5"

    def _blob(self, rgb_letterboxed, fmt: str):
        import numpy as np

        x = rgb_letterboxed.astype(np.float32)
        if fmt in ("yolov8", "yolov5"):
            x /= 255.0
        return np.ascontiguousarray(x.transpose(2, 0, 1)[None])

    def detect(self, bgr) -> list[tuple[str, float, tuple[float, float, float, float]]]:
        import cv2

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        lb, r = letterbox(rgb, self.input_hw)
        out = self._forward(self._blob(lb, self.format))
        if self.format == "yolox":
            boxes, scores, cls = decode_yolox(out, self.input_hw[0], self.input_hw[1], self.conf)
        elif self.format == "yolov5":
            boxes, scores, cls = decode_yolov5(out, self.conf)
        else:
            boxes, scores, cls = decode_yolov8(out, self.conf)
        if len(boxes) == 0:
            return []
        keep = nms(boxes, scores, cls, self.conf, self.iou)
        h, w = bgr.shape[:2]
        res = []
        for i in keep[:50]:
            x, y, bw, bh = (boxes[i] / r).tolist()
            x, y = max(0.0, x), max(0.0, y)
            bw, bh = min(bw, w - x), min(bh, h - y)
            c = int(cls[i])
            label = self.labels[c] if c < len(self.labels) else f"class {c}"
            res.append((label, float(scores[i]), (x, y, bw, bh)))
        return res

    @property
    def description(self) -> str:
        return f"{self.path.name} ({self.format}, {self.engine})"


class HogPersonDetector:
    """OpenCV HOG + linear SVM pedestrian detector (person only, upright, ≥ ~100 px tall)."""

    name = "hog"
    labels = ["person"]

    def __init__(self) -> None:
        import cv2

        if not hasattr(cv2, "HOGDescriptor"):
            raise RuntimeError("this OpenCV build has no HOGDescriptor")
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self.description = "OpenCV HOG person detector (fallback — install an ONNX model for YOLO)"

    def detect(self, bgr):
        import numpy as np

        rects, weights = self._hog.detectMultiScale(bgr, winStride=(8, 8), padding=(8, 8), scale=1.05)
        out = []
        for (x, y, w, h), wt in zip(rects, np.ravel(weights) if len(rects) else []):
            conf = float(1 / (1 + math.exp(-(float(wt) - 0.5) * 2)))
            if conf >= 0.5:
                out.append(("person", round(conf, 3), (float(x), float(y), float(w), float(h))))
        return out


def find_default_model(models_dir: Path) -> Path | None:
    """Prefer a YOLO11/YOLOv8 export, then YOLOX, in the repo's models/ folder."""
    if not models_dir.is_dir():
        return None
    files = sorted(models_dir.glob("*.onnx"))
    for pref in ("yolo11", "yolov8", "yolo", "yolox"):
        for f in files:
            if f.name.lower().startswith(pref):
                return f
    return files[0] if files else None


def timed(fn, *a):
    t0 = time.perf_counter()
    r = fn(*a)
    return r, (time.perf_counter() - t0) * 1000.0

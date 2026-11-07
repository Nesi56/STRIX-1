"""On-device scene analysis with OpenCV (no cloud, no LLM required).

Produces *image-relative* observations only: normalized bounding boxes and
area fractions. No real-world distances or sizes are inferred from pixels.

Methods and what they can honestly claim:

* ``exg-heuristic``  — Excess-Green colour index → vegetation regions
  (trees, shrubs, grass are not distinguished reliably; position in frame is a hint).
* ``sky-heuristic``  — bright, blue/low-saturation region attached to the top edge.
* ``hough-heuristic``— clusters of long straight edges → possible built structure.
* ``path-heuristic`` — low-saturation band connected to the bottom edge → path/road candidate.
* ``hog-svm``        — OpenCV's pretrained HOG pedestrian detector (upright people ≥ ~128 px tall).
* ``yolo-onnx``      — optional user-supplied YOLOv8-style ONNX model (COCO classes).
* ``frame-diff``     — motion between consecutive analysed frames.

Every result carries method + confidence so the UI/AI can present it as a
detector output, not ground truth.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

COCO_LABELS = (
    "person bicycle car motorcycle airplane bus train truck boat traffic_light fire_hydrant stop_sign "
    "parking_meter bench bird cat dog horse sheep cow elephant bear zebra giraffe backpack umbrella handbag "
    "tie suitcase frisbee skis snowboard sports_ball kite baseball_bat baseball_glove skateboard surfboard "
    "tennis_racket bottle wine_glass cup fork knife spoon bowl banana apple sandwich orange broccoli carrot "
    "hot_dog pizza donut cake chair couch potted_plant bed dining_table toilet tv laptop mouse remote keyboard "
    "cell_phone microwave oven toaster sink refrigerator book clock vase scissors teddy_bear hair_drier toothbrush"
).split()

OBSTACLE_CLASSES = {
    "bicycle", "car", "motorcycle", "bus", "train", "truck", "boat", "traffic_light", "fire_hydrant",
    "stop_sign", "parking_meter", "bench", "chair", "couch", "potted_plant", "dining_table", "kite",
}
ANIMAL_CLASSES = {"bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"}


def _region_name(x: float, y: float) -> str:
    v = "upper" if y < 0.33 else ("lower" if y > 0.66 else "middle")
    h = "left" if x < 0.33 else ("right" if x > 0.66 else "center")
    return f"{v}-{h}" if not (v == "middle" and h == "center") else "center"


class SceneAnalyzer:
    def __init__(self, onnx_model: str = "", labels_file: str = "", conf_threshold: float = 0.4) -> None:
        import cv2

        self.cv2 = cv2
        self.conf_threshold = conf_threshold
        self._hog = None
        if hasattr(cv2, "HOGDescriptor"):
            try:
                self._hog = cv2.HOGDescriptor()
                self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            except Exception as exc:  # pragma: no cover
                log.warning("HOG people detector unavailable: %s", exc)
                self._hog = None
        self._net = None
        self._labels = list(COCO_LABELS)
        if onnx_model:
            try:
                if not Path(onnx_model).is_file():
                    raise FileNotFoundError(onnx_model)
                self._net = cv2.dnn.readNetFromONNX(onnx_model)
                if labels_file and Path(labels_file).is_file():
                    self._labels = [l.strip() for l in Path(labels_file).read_text().splitlines() if l.strip()]
                log.info("loaded ONNX detector %s", onnx_model)
            except Exception as exc:
                log.warning("ONNX detector not loaded (%s); continuing with heuristics", exc)
                self._net = None
        self._prev_gray = None
        self._prev_ts = 0.0

    @property
    def methods(self) -> list[str]:
        m = ["exg-heuristic", "sky-heuristic", "hough-heuristic", "path-heuristic", "frame-diff", "image-quality"]
        if self._hog is not None:
            m.append("hog-svm")
        if self._net is not None:
            m.append("yolo-onnx")
        return m

    # ------------------------------------------------------------------
    def analyze(self, bgr, frame_meta: dict[str, Any] | None = None) -> dict[str, Any]:
        cv2 = self.cv2
        import numpy as np

        t0 = time.perf_counter()
        h, w = bgr.shape[:2]
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        detections: list[dict[str, Any]] = []
        area = float(w * h)

        def box(x, y, bw, bh) -> list[float]:
            return [round(x / w, 4), round(y / h, 4), round(bw / w, 4), round(bh / h, 4)]

        # ---- image quality
        brightness = float(hsv[..., 2].mean()) / 255.0
        contrast = float(gray.std()) / 128.0
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        quality_flags = []
        if brightness < 0.15:
            quality_flags.append("too dark")
        if brightness > 0.9:
            quality_flags.append("overexposed")
        if sharpness < 30:
            quality_flags.append("blurry / low detail")

        # ---- vegetation (Excess Green on normalised chromaticity)
        f = bgr.astype(np.float32) + 1.0
        s = f.sum(axis=2)
        b_, g_, r_ = f[..., 0] / s, f[..., 1] / s, f[..., 2] / s
        exg = 2 * g_ - r_ - b_
        veg = ((exg > 0.06) & (hsv[..., 2] > 35)).astype(np.uint8) * 255
        veg = cv2.morphologyEx(veg, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        veg_fraction = float(np.count_nonzero(veg)) / area
        n, labels, stats, _ = cv2.connectedComponentsWithStats(veg, 8)
        for i in range(1, n):
            x, y, bw, bh, a = stats[i]
            if a / area < 0.012:
                continue
            mean_exg = float(exg[labels == i].mean())
            cy = (y + bh / 2) / h
            kind = "tree-like vegetation" if cy < 0.6 and bh > bw * 0.6 else "vegetation (ground cover)"
            detections.append({
                "label": kind, "category": "vegetation", "method": "exg-heuristic",
                "confidence": round(min(0.9, 0.35 + mean_exg * 3), 2), "bbox": box(x, y, bw, bh),
                "area_fraction": round(a / area, 4),
            })

        # ---- sky: bright, low-saturation or blue-hued, connected to top edge
        sky_mask = (((hsv[..., 2] > 150) & ((hsv[..., 1] < 60) | ((hsv[..., 0] > 90) & (hsv[..., 0] < 130)))) &
                    (exg < 0.04)).astype(np.uint8)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(sky_mask, 4)
        sky = np.zeros_like(sky_mask)
        for i in range(1, n):
            if stats[i][1] == 0 and stats[i][4] / area > 0.02:
                sky[labels == i] = 1
        sky_fraction = float(sky.sum()) / area
        horizon_y = None
        if sky_fraction > 0.03:
            rows = np.where(sky.mean(axis=1) > 0.5)[0]
            if rows.size:
                horizon_y = round(float(rows.max()) / h, 3)
                ys, xs = np.where(sky > 0)
                detections.append({
                    "label": "sky", "category": "sky", "method": "sky-heuristic", "confidence": 0.6,
                    "bbox": box(xs.min(), ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1),
                    "area_fraction": round(sky_fraction, 4),
                })

        # ---- straight-edge clusters → possible built structures
        edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 60, 160)
        edge_density = float(np.count_nonzero(edges)) / area
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=50, minLineLength=max(25, w // 18), maxLineGap=6)
        struct_mask = np.zeros_like(gray)
        n_lines = 0
        if lines is not None:
            for x1, y1, x2, y2 in lines[:, 0]:
                ang = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 180
                if ang < 12 or ang > 168 or 78 < ang < 102:  # near horizontal / vertical
                    cv2.line(struct_mask, (x1, y1), (x2, y2), 255, 9)
                    n_lines += 1
        struct_mask[veg > 0] = 0
        struct_mask[sky > 0] = 0
        struct_mask = cv2.dilate(struct_mask, np.ones((15, 15), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(struct_mask, 8)
        for i in range(1, n):
            x, y, bw, bh, a = stats[i]
            if a / area < 0.02:
                continue
            segs = 0
            if lines is not None:
                for x1, y1, x2, y2 in lines[:, 0]:
                    if x <= x1 <= x + bw and y <= y1 <= y + bh:
                        segs += 1
            if segs < 4:
                continue
            detections.append({
                "label": "possible built structure (straight-edge cluster)", "category": "structure",
                "method": "hough-heuristic", "confidence": round(min(0.8, 0.25 + segs * 0.04), 2),
                "bbox": box(x, y, bw, bh), "area_fraction": round(a / area, 4), "line_segments": int(segs),
            })

        # ---- path / road candidate: low saturation, mid brightness, touches bottom edge, below horizon
        lower = int(h * (horizon_y if horizon_y else 0.5))
        path_mask = ((hsv[..., 1] < 45) & (hsv[..., 2] > 70) & (hsv[..., 2] < 210) & (veg == 0)).astype(np.uint8)
        path_mask[:lower, :] = 0
        path_mask = cv2.morphologyEx(path_mask, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(path_mask, 4)
        for i in range(1, n):
            x, y, bw, bh, a = stats[i]
            if y + bh >= h - 2 and a / area > 0.03:
                detections.append({
                    "label": "path / road candidate", "category": "path", "method": "path-heuristic",
                    "confidence": 0.4, "bbox": box(x, y, bw, bh), "area_fraction": round(a / area, 4),
                })

        # ---- people (HOG)
        if self._hog is not None:
            try:
                scale = 1.0 if h >= 360 else 360.0 / h
                img = bgr if scale == 1.0 else cv2.resize(bgr, None, fx=scale, fy=scale)
                rects, weights = self._hog.detectMultiScale(img, winStride=(8, 8), padding=(8, 8), scale=1.05)
                for (x, y, bw, bh), wt in zip(rects, np.ravel(weights) if len(rects) else []):
                    conf = float(1 / (1 + np.exp(-(float(wt) - 0.5) * 2)))
                    if conf < 0.5:
                        continue
                    detections.append({
                        "label": "person", "category": "person", "method": "hog-svm",
                        "confidence": round(conf, 2), "bbox": box(x / scale, y / scale, bw / scale, bh / scale),
                    })
            except Exception as exc:  # pragma: no cover
                log.debug("HOG failed: %s", exc)

        # ---- optional YOLO ONNX
        if self._net is not None:
            detections.extend(self._yolo(bgr))

        # ---- motion
        motion_fraction = None
        small = cv2.resize(gray, (160, max(1, int(160 * h / w))))
        now = time.time()
        if self._prev_gray is not None and self._prev_gray.shape == small.shape and now - self._prev_ts < 30:
            diff = cv2.absdiff(small, self._prev_gray)
            motion_fraction = round(float(np.count_nonzero(diff > 25)) / diff.size, 4)
        self._prev_gray, self._prev_ts = small, now

        summary = self._summarize(detections, veg_fraction, sky_fraction, quality_flags, motion_fraction)
        return {
            "kind": "scene_analysis",
            "frame": {"width": w, "height": h, **(frame_meta or {})},
            "detections": detections,
            "metrics": {
                "brightness": round(brightness, 3),
                "contrast": round(contrast, 3),
                "sharpness": round(sharpness, 1),
                "vegetation_fraction": round(veg_fraction, 4),
                "sky_fraction": round(sky_fraction, 4),
                "edge_density": round(edge_density, 4),
                "straight_lines": n_lines,
                "horizon_y": horizon_y,
                "motion_fraction": motion_fraction,
            },
            "quality_flags": quality_flags,
            "methods": self.methods,
            "summary": summary,
            "processing_ms": round((time.perf_counter() - t0) * 1000, 1),
            "image_relative_only": True,
            "note": "Image-relative detections from heuristics/classical detectors; no real-world distances are inferred.",
        }

    def _yolo(self, bgr) -> list[dict[str, Any]]:
        cv2 = self.cv2
        import numpy as np

        h, w = bgr.shape[:2]
        blob = cv2.dnn.blobFromImage(bgr, 1 / 255.0, (640, 640), swapRB=True, crop=False)
        self._net.setInput(blob)
        out = self._net.forward()
        out = np.squeeze(out)
        if out.ndim != 2:
            return []
        if out.shape[0] < out.shape[1]:  # (84, 8400) -> (8400, 84)
            out = out.T
        boxes, confs, cls_ids = [], [], []
        for row in out:
            scores = row[4:]
            c = int(np.argmax(scores))
            conf = float(scores[c])
            if conf < self.conf_threshold:
                continue
            cx, cy, bw, bh = row[:4]
            x = (cx - bw / 2) * w / 640
            y = (cy - bh / 2) * h / 640
            boxes.append([int(x), int(y), int(bw * w / 640), int(bh * h / 640)])
            confs.append(conf)
            cls_ids.append(c)
        dets = []
        idx = cv2.dnn.NMSBoxes(boxes, confs, self.conf_threshold, 0.45) if boxes else []
        for i in np.ravel(idx) if len(idx) else []:
            label = self._labels[cls_ids[i]] if cls_ids[i] < len(self._labels) else f"class_{cls_ids[i]}"
            cat = "person" if label == "person" else "obstacle" if label in OBSTACLE_CLASSES else (
                "animal" if label in ANIMAL_CLASSES else "object")
            x, y, bw, bh = boxes[i]
            dets.append({
                "label": label.replace("_", " "), "category": cat, "method": "yolo-onnx",
                "confidence": round(confs[i], 2),
                "bbox": [round(max(0, x) / w, 4), round(max(0, y) / h, 4), round(bw / w, 4), round(bh / h, 4)],
            })
        return dets

    @staticmethod
    def _summarize(dets, veg_fraction, sky_fraction, quality_flags, motion_fraction) -> str:
        parts = []
        cats: dict[str, list[dict[str, Any]]] = {}
        for d in dets:
            cats.setdefault(d["category"], []).append(d)
        if veg_fraction > 0.01:
            parts.append(f"vegetation ≈{veg_fraction * 100:.0f}% of frame ({len(cats.get('vegetation', []))} region(s))")
        if sky_fraction > 0.03:
            parts.append(f"sky ≈{sky_fraction * 100:.0f}%")
        for cat, name in (("person", "person candidate"), ("structure", "possible structure"),
                          ("path", "path/road candidate"), ("obstacle", "obstacle-class object"), ("animal", "animal")):
            items = cats.get(cat, [])
            if items:
                where = ", ".join(sorted({_region_name(d["bbox"][0] + d["bbox"][2] / 2, d["bbox"][1] + d["bbox"][3] / 2) for d in items}))
                methods = "/".join(sorted({d["method"] for d in items}))
                parts.append(f"{len(items)} {name}(s) [{methods}] at {where}")
        if motion_fraction is not None and motion_fraction > 0.02:
            parts.append(f"motion in ≈{motion_fraction * 100:.0f}% of frame since previous analysis")
        if quality_flags:
            parts.append("image quality: " + ", ".join(quality_flags))
        return "; ".join(parts) if parts else "no notable features detected by the heuristics"

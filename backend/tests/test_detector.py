import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from app.config.settings import REPO_ROOT  # noqa: E402
from app.services.detector import (  # noqa: E402
    COCO80,
    OnnxDetector,
    _yolox_grids,
    decode_yolov8,
    decode_yolox,
    estimate_geometry,
    find_default_model,
    focal_px,
    letterbox,
    nms,
    normalize_label,
)
from tests.conftest import wait_for  # noqa: E402


def test_normalize_label():
    assert normalize_label("People") == "person"
    assert normalize_label("cars") == "car"
    assert normalize_label("phone") == "cell phone"
    assert normalize_label("dining_table") == "dining table"
    assert normalize_label("unicorn") is None
    assert normalize_label("car", ["person"]) is None


def test_distance_round_trip_and_bearing():
    w, h, hfov = 640, 360, 102.0
    f = focal_px(w, hfov)
    true_d = 4.0
    hp = f * 1.7 / true_d
    box = (w / 2 + 50, h / 2 - hp / 2, hp * 0.4, hp)
    dist, kind, bearing, elev = estimate_geometry("person", box, w, h, hfov)
    assert dist == pytest.approx(true_d, rel=0.01) and kind == "estimate"
    assert bearing > 0 and bearing == pytest.approx(math.degrees(math.atan((50 + hp * 0.2) / f)), abs=0.1)
    assert abs(elev) < 0.5
    # Cut off by the bottom edge → only an upper bound.
    assert estimate_geometry("person", (10, 200, 30, 160), w, h, hfov)[1] == "upper_bound"
    # No size prior → no range.
    assert estimate_geometry("kite", (10, 10, 30, 30), w, h, hfov)[0] is None


def test_letterbox():
    img = np.zeros((360, 640, 3), np.uint8)
    out, r = letterbox(img, (640, 640))
    assert out.shape == (640, 640, 3) and r == 1.0 and out[400, 10, 0] == 114


def test_decode_yolov8_synthetic_tensor():
    n = 100
    out = np.zeros((1, 84, n), np.float32)
    out[0, :4, 7] = [320, 240, 100, 200]  # cx, cy, w, h
    out[0, 4 + 2, 7] = 0.9  # class 2 = car
    out[0, 4 + 0, 8] = 0.2  # below threshold
    boxes, scores, cls = decode_yolov8(out, 0.35)
    assert len(boxes) == 1 and COCO80[int(cls[0])] == "car"
    assert boxes[0].tolist() == pytest.approx([270, 140, 100, 200]) and scores[0] == pytest.approx(0.9)


def test_decode_yolox_synthetic_tensor():
    grids, strides = _yolox_grids(640, 640)
    out = np.full((1, grids.shape[0], 85), -10.0, np.float32)
    out[0, :, 4:] = 0.0
    i = int(np.where((grids[:, 0] == 10) & (grids[:, 1] == 20) & (strides[:, 0] == 8))[0][0])
    out[0, i, :4] = [0.5, 0.5, math.log(5), math.log(10)]
    out[0, i, 4] = 0.9  # objectness
    out[0, i, 5 + 0] = 0.8  # person
    boxes, scores, cls = decode_yolox(out, 640, 640, 0.35)
    assert len(boxes) == 1 and int(cls[0]) == 0 and scores[0] == pytest.approx(0.72)
    cx, cy = 10.5 * 8, 20.5 * 8
    assert boxes[0].tolist() == pytest.approx([cx - 20, cy - 40, 40, 80], abs=1e-3)


def test_class_aware_nms():
    boxes = np.array([[0, 0, 100, 100], [5, 5, 100, 100], [0, 0, 100, 100]], np.float32)
    scores = np.array([0.9, 0.8, 0.7], np.float32)
    cls = np.array([0, 0, 2])
    keep = nms(boxes, scores, cls, 0.3, 0.45)
    assert sorted(keep) == [0, 2]


def test_synthetic_detector_stream(client):
    c = client.app.state.container
    wait_for(lambda: (c.detector.latest() or {}).get("detections"))
    st = client.get("/api/detector").json()
    assert st["engine"] == "synthetic" and st["status"]["synthetic"] is True
    d = st["latest"]["detections"][0]
    assert d["label"] == "person" and 0 < d["confidence"] < 1
    assert d["distance_m"] and d["distance_kind"] in ("estimate", "upper_bound")
    assert all(0 <= v <= 1 for v in d["bbox"])


def test_prompts_api(client):
    r = client.post("/api/detector/prompts", json={"prompts": ["Cars", "people"]})
    assert r.json()["prompts"] == ["car", "person"]
    r = client.post("/api/detector/prompts", json={"prompts": ["unicorn"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_class"
    assert "person" in client.get("/api/detector/classes").json()["classes"]


MODEL = find_default_model(REPO_ROOT / "models")


@pytest.mark.skipif(MODEL is None, reason="no ONNX detector in models/ (scripts/get_detector_model.sh)")
def test_real_onnx_model_runs():
    det = OnnxDetector(str(MODEL))
    img = np.full((360, 640, 3), 128, np.uint8)
    assert isinstance(det.detect(img), list)


def test_everyday_model_labels_are_mapped_and_merged(tmp_path):
    """A 600-class Open Images model: names map to STRIX-1's names; mug + coffee cup boxes merge into one cup."""
    import numpy as np

    from app.services.detector import OnnxDetector, canonical_label, find_default_model, labels_for_model

    names = [f"thing {i}" for i in range(600)]
    names[10], names[11], names[12], names[13] = "Mug", "Coffee cup", "Pen", "Mobile phone"
    (tmp_path / "yolov8n-oiv7.onnx").write_bytes(b"x")
    (tmp_path / "yolox_s.onnx").write_bytes(b"x")
    (tmp_path / "yolox_tiny.onnx").write_bytes(b"x")
    (tmp_path / "yolov8n-oiv7.labels.txt").write_text("\n".join(names))
    assert find_default_model(tmp_path).name == "yolov8n-oiv7.onnx"
    (tmp_path / "yolov8n-oiv7.onnx").unlink()
    assert find_default_model(tmp_path).name == "yolox_tiny.onnx"  # lighter YOLOX before YOLOX-S
    labels = labels_for_model(tmp_path / "yolov8n-oiv7.onnx")
    assert len(labels) == 600 and canonical_label("Mobile phone") == "cell phone"

    det = OnnxDetector.__new__(OnnxDetector)  # skip loading a real network
    det.path = tmp_path / "m.onnx"
    det.raw_count = 600
    det.labels = [canonical_label(n) for n in labels]
    uniq = list(dict.fromkeys(det.labels))
    det._group = [uniq.index(l) for l in det.labels]
    det.conf, det.iou, det.input_hw, det.format = 0.3, 0.45, (416, 416), "yolov8"
    out = np.zeros((1, 604, 3549), np.float32)  # real 416-px export: 3549 anchors
    for j, (cx, cls, sc) in enumerate([(100, 10, 0.9), (102, 11, 0.8), (300, 12, 0.7)]):  # mug, coffee cup, pen
        out[0, :4, j] = [cx, 200, 40, 50]
        out[0, 4 + cls, j] = sc
    det._forward = lambda blob: out
    res = det.detect(np.zeros((416, 416, 3), np.uint8))
    assert sorted(l for l, _, _ in res) == ["cup", "pen"]  # the two cup boxes merged into one


def test_detector_slows_down_when_hot_or_qwen_busy(tmp_path):
    from app.services.detector_service import DetectorService
    from app.services.hub import EventLog
    from tests.conftest import make_settings

    s = make_settings(tmp_path, detector_hz=3.0)
    load = {"v": (55.0, False)}
    d = DetectorService(s, camera=None, events=EventLog(), load=lambda: load["v"])
    assert d.period() == pytest.approx(1 / 3) and d.slowed is None
    load["v"] = (60.0, True)
    assert d.period() == pytest.approx(1 / 2) and d.slowed == "Qwen is thinking"
    load["v"] = (77.0, False)
    assert d.period() == pytest.approx(1 / 1.5) and "77" in d.slowed
    load["v"] = (82.0, True)
    assert d.period() == pytest.approx(1.0)

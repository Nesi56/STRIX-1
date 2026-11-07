import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from app.services.mapping_service import MonocularVO  # noqa: E402
from app.services.vision import SceneAnalyzer  # noqa: E402


def landscape():
    img = np.zeros((360, 640, 3), np.uint8)
    img[:150] = (230, 180, 120)  # sky (BGR)
    img[150:] = (50, 140, 60)  # grass
    cv2.rectangle(img, (420, 60), (560, 150), (120, 120, 125), -1)  # building
    for x in range(430, 550, 25):
        for y in range(70, 140, 22):
            cv2.rectangle(img, (x, y), (x + 12, y + 12), (200, 220, 230), -1)
    return img


def test_scene_analysis_finds_sky_and_vegetation():
    a = SceneAnalyzer()
    out = a.analyze(landscape(), {"source": "test"})
    cats = {d["category"] for d in out["detections"]}
    assert "sky" in cats and "vegetation" in cats
    assert 0.25 < out["metrics"]["sky_fraction"] < 0.5
    assert 0.4 < out["metrics"]["vegetation_fraction"] < 0.7
    assert out["image_relative_only"] is True
    for d in out["detections"]:
        x, y, w, h = d["bbox"]
        assert 0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1
        assert "distance" not in d and d["method"] and 0 <= d["confidence"] <= 1
    assert "sky" in out["summary"]


def test_scene_analysis_flags_dark_frames():
    out = SceneAnalyzer().analyze(np.zeros((360, 640, 3), np.uint8))
    assert "too dark" in out["quality_flags"]


def test_missing_onnx_model_is_non_fatal(tmp_path):
    a = SceneAnalyzer(onnx_model=str(tmp_path / "missing.onnx"))
    assert "yolo-onnx" not in a.methods
    a.analyze(landscape())


def render_points(pts, cam, w=640, h=360, hfov=102):
    f = (w / 2) / np.tan(np.radians(hfov) / 2)
    K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]])
    p = pts - cam
    uv = (K @ p.T).T
    uv = uv[:, :2] / uv[:, 2:3]
    img = np.zeros((h, w), np.uint8)
    for (u, v), z in zip(uv, p[:, 2]):
        if 0 <= u < w and 0 <= v < h:
            cv2.circle(img, (int(u), int(v)), 2, int(255 * min(1, 4 / z)), -1)
    return cv2.GaussianBlur(img, (3, 3), 0)


def test_vo_recovers_direction_but_not_scale():
    rng = np.random.default_rng(0)
    pts = np.column_stack([rng.uniform(-4, 4, 3000), rng.uniform(-2.5, 2.5, 3000), rng.uniform(4, 12, 3000)])
    vo = MonocularVO(hfov_deg=102)
    still = render_points(pts, np.zeros(3))
    vo.process(still, 0.0)
    vo.process(still.copy(), 0.05)
    assert vo.state.startswith("stationary")
    for i in range(1, 10):  # true motion: +0.15 right, +0.20 forward per frame
        vo.process(render_points(pts, np.array([0.15 * i, 0.0, 0.2 * i])), 0.1 * i)
    d = vo.to_dict()
    assert d["accepted_steps"] >= 7 and vo.state == "tracking"
    end = d["trajectory"][-1]
    assert end["x"] > 0 and end["z"] > 0
    assert 0.5 < end["x"] / end["z"] < 1.0  # direction ≈ 0.75
    # Unit steps: path length counts steps, not metres (true length here is 2.25 m).
    assert d["path_length_units"] == pytest.approx(d["accepted_steps"], rel=1e-6)
    assert d["units"].startswith("arbitrary") and "unbounded" in d["drift"]

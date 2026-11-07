import math
import time

import pytest

from app.core.orientation import STANDARD_GRAVITY as G, ComplementaryFilter
from app.services.dht_service import DHTReadError, DHTService, MockDHT
from app.services.hub import EventLog
from app.services.imu_service import IMUService, MockIMUBackend
from tests.conftest import make_settings, wait_for


def test_mock_imu_is_physically_consistent():
    m = MockIMUBackend(seed=1)
    norms = []
    for _ in range(200):
        a, g, t = m.read()
        norms.append(math.sqrt(sum(v * v for v in a)) / G)
        time.sleep(0.001)
    assert 0.95 < sum(norms) / len(norms) < 1.05
    assert 25 < t < 40


def test_filter_tracks_mock_attitude():
    m = MockIMUBackend(seed=2)
    f = ComplementaryFilter(alpha=0.98)
    last = time.monotonic()
    errs = []
    for i in range(400):
        a, g, _ = m.read()
        now = time.monotonic()
        g = tuple(v - b for v, b in zip(g, m._bias))
        st = f.update(a, g, now - last)
        last = now
        true_roll, true_pitch, _ = m.attitude(now - m._t0)
        if i > 100:
            errs.append(max(abs(st.roll - true_roll), abs(st.pitch - true_pitch)))
        time.sleep(0.005)
    assert sum(errs) / len(errs) < 2.5  # degrees


def test_mock_dht_values_and_failures():
    d = MockDHT(seed=3, failure_rate=0.0)
    t, h = d.read()
    assert 15 < t < 30 and 30 < h < 60
    d = MockDHT(seed=3, failure_rate=1.0)
    with pytest.raises(DHTReadError, match="synthetic"):
        d.read()


def test_imu_service_mock_is_labeled_synthetic(tmp_path):
    s = make_settings(tmp_path, imu_rate_hz=100)
    svc = IMUService(s, EventLog())
    svc.start()
    try:
        wait_for(lambda: (svc.latest() or {}).get("rate_hz") is not None)
        wait_for(lambda: svc.latest()["rate_hz"] > 60, timeout=5)
        st = svc.status.to_dict()
        assert st["synthetic"] is True and st["state"] == "synthetic"
        latest = svc.latest()
        assert {"accel", "gyro", "orientation", "ts", "rate_hz"} <= latest.keys()
        assert abs(latest["ts"] - time.time()) < 1.0
    finally:
        svc.stop()


def test_hardware_failure_is_disconnected_not_mock(tmp_path):
    """auto backend without hardware must report DISCONNECTED, never silent synthetic data."""
    s = make_settings(tmp_path, mock=False, imu_backend="auto", dht_backend="auto")
    imu = IMUService(s, EventLog())
    imu.start()
    dht = DHTService(s, EventLog())
    dht.start()
    try:
        time.sleep(0.3)
        assert imu.status.state.value == "disconnected"
        assert imu.status.synthetic is False
        assert imu.latest() is None
        assert dht.status.state.value == "disconnected"
        assert dht.latest() is None
        assert any(f["code"] == "no_data" for f in imu.health())
    finally:
        imu.stop()
        dht.stop()


def test_dht_service_session_extremes(tmp_path):
    s = make_settings(tmp_path, dht_interval_s=2.0)
    svc = DHTService(s, EventLog())
    svc._backend = MockDHT(seed=4, failure_rate=0.0)
    svc.status.synthetic = True
    svc.start = lambda: None  # we drive reads manually below
    import threading

    stop = threading.Event()
    t = threading.Thread(target=svc._run, args=(stop,), daemon=True)
    t.start()
    try:
        wait_for(lambda: svc.latest() is not None)
        summ = svc.summary()
        assert summ["session"]["temp_min"] is not None
        assert summ["latest"]["temperature_f"] == pytest.approx(summ["latest"]["temperature_c"] * 9 / 5 + 32)
    finally:
        stop.set()

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import sys3grabber as g

CFG = {"memory_contains": "32g", "storage_contains": "960", "datacenters": ["lon"]}


def rows(lon_availability):
    """Fresh catalog rows each call, so tests never share mutable state."""
    return [
        {"fqn": "p.ram-32g-ecc-2666.softraid-2x960nvme", "memory": "ram-32g-ecc-2666",
         "storage": "softraid-2x960nvme",
         "datacenters": [{"datacenter": "lon", "availability": lon_availability},
                         {"datacenter": "gra", "availability": "1H-high"}]},
        {"fqn": "p.ram-64g.softraid-2x960nvme", "memory": "ram-64g", "storage": "softraid-2x960nvme",
         "datacenters": [{"datacenter": "lon", "availability": "1H-high"}]},
    ]

def test_none_in_stock():
    assert g.find_available(rows("unavailable"), CFG) == []

def test_in_stock():
    hits = g.find_available(rows("72H"), CFG)
    assert [(h[0], h[1]) for h in hits] == [("p.ram-32g-ecc-2666.softraid-2x960nvme", "lon")]


def test_availability_allowlist():
    # real in-stock shapes -> available
    for ok in ("1H-high", "24H", "72H", "480H", "high", "LOW"):
        assert g.is_available(ok), ok
    # unavailable / unknown / new-or-garbage statuses -> NOT available
    for bad in ("unavailable", "comingSoon", "unknown", "", None, "backorder", "1H-medium"):
        assert not g.is_available(bad), bad


def test_available_values_override():
    cfg = {"available_values": ["in-stock"]}
    assert g.is_available("in-stock", cfg)
    assert not g.is_available("72H", cfg)  # allowlist is now explicit


def test_server_mode_matches_ssd_and_carries_plancode():
    # server feed mixes plan codes; the SSD config lives under 24sys032-v1
    server_rows = [
        {"fqn": "24sys03-v1.ram-32g.softraid-2x960nvme", "memory": "ram-32g",
         "storage": "softraid-2x960nvme", "planCode": "24sys03-v1",
         "datacenters": [{"datacenter": "lon", "availability": "1H-high"}]},
        {"fqn": "24sys032-v1.ram-32g.hardraid-4x960ssd", "memory": "ram-32g",
         "storage": "hardraid-4x960ssd", "planCode": "24sys032-v1",
         "datacenters": [{"datacenter": "lon", "availability": "1H-high"}]},
    ]
    cfg = {"memory_contains": "32g", "storage_contains": "960ssd", "datacenters": ["lon"]}
    hits = g.find_available(server_rows, cfg)
    assert len(hits) == 1
    fqn, dc, av, row = hits[0]
    assert row["planCode"] == "24sys032-v1"  # the plan an order must use


def test_plan_code_filter_narrows_server_feed():
    server_rows = [
        {"fqn": "24sys03-v1.ram-32g.softraid-2x960nvme", "memory": "ram-32g",
         "storage": "softraid-2x960nvme", "planCode": "24sys03-v1",
         "datacenters": [{"datacenter": "lon", "availability": "1H-high"}]},
        {"fqn": "24sys032-v1.ram-32g.softraid-2x960nvme", "memory": "ram-32g",
         "storage": "softraid-2x960nvme", "planCode": "24sys032-v1",
         "datacenters": [{"datacenter": "lon", "availability": "1H-high"}]},
    ]
    cfg = {"memory_contains": "32g", "storage_contains": "960", "datacenters": ["lon"],
           "plan_code": "24sys032-v1"}
    hits = g.find_available(server_rows, cfg)
    assert [h[3]["planCode"] for h in hits] == ["24sys032-v1"]


def test_matrix_notifier(monkeypatch=None):
    import os
    os.environ.pop("MATRIX_TOKEN", None)
    try:
        g._matrix_send({"homeserver": "http://h:8008", "room_id": "!r:h"}, "x")
        assert False, "expected missing-token error"
    except RuntimeError as e:
        assert "token" in str(e)
    calls = []
    orig = g._post
    g._post = lambda url, payload=None, raw=None, headers=None, method="POST": calls.append((method, url, payload, headers))
    try:
        os.environ["MATRIX_TOKEN"] = "tok"
        g._matrix_send({"homeserver": "http://h:8008/", "room_id": "!a:h"}, "up")
    finally:
        g._post = orig
        os.environ.pop("MATRIX_TOKEN", None)
    method, url, payload, headers = calls[0]
    assert method == "PUT"
    assert payload == {"msgtype": "m.text", "body": "up"}
    assert headers["Authorization"] == "Bearer tok"
    assert "/_matrix/client/v3/rooms/%21a%3Ah/send/m.room.message/" in url


def test_order_lock():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        cfg = {"order_lock": os.path.join(d, ".ordered")}
        g.check_order_lock(cfg)  # no lock yet -> fine
        with open(cfg["order_lock"], "w") as f:
            f.write('{"status": "placed"}')
        try:
            g.check_order_lock(cfg)
            assert False, "expected refusal when lock exists"
        except RuntimeError as e:
            assert "already attempted" in str(e)

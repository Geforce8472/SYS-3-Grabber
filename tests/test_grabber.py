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

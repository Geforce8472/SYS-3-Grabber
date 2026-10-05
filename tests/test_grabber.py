import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import sys3grabber as g

CFG = {"memory_contains": "32g", "storage_contains": "960", "datacenters": ["lon"]}
ROWS = [
    {"fqn": "p.ram-32g-ecc-2666.softraid-2x960nvme", "memory": "ram-32g-ecc-2666",
     "storage": "softraid-2x960nvme",
     "datacenters": [{"datacenter": "lon", "availability": "unavailable"},
                     {"datacenter": "gra", "availability": "1H-high"}]},
    {"fqn": "p.ram-64g.softraid-2x960nvme", "memory": "ram-64g", "storage": "softraid-2x960nvme",
     "datacenters": [{"datacenter": "lon", "availability": "1H-high"}]},
]

def test_none_in_stock():
    assert g.find_available(ROWS, CFG) == []

def test_in_stock():
    ROWS[0]["datacenters"][0]["availability"] = "72H"
    hits = g.find_available(ROWS, CFG)
    assert [(h[0], h[1]) for h in hits] == [("p.ram-32g-ecc-2666.softraid-2x960nvme", "lon")]

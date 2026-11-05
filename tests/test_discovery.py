import csv

from discovery.analyse import EXPORT, analyse, replay
from discovery.generate import generate


def test_export_is_deterministic():
    assert generate()[:5] == generate()[:5]


def test_shadow_replay_gate():
    """CI gate on the discovery replay: no unsafe actions, no wrong auto-resolutions, seeded deviations found."""
    rows = list(csv.DictReader(EXPORT.open(encoding="utf-8")))
    s = analyse(replay(rows))
    assert s["unsafe"] == 0
    assert s["wrong_auto"] == []
    assert s["caught"] >= 0.9 * s["seeded"]

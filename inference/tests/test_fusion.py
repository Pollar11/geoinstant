from itertools import pairwise

import numpy as np
import pytest

from geoinstant.cells import CellTree
from geoinstant.config import DATA_DIR
from geoinstant.evidence.base import observation_evidence
from geoinstant.evidence.cues import CueKnowledgeBase
from geoinstant.fusion import fuse
from geoinstant.gazetteer import Gazetteer
from geoinstant.geo import haversine_km, mean_shift_mode

GAZ = Gazetteer()
TREE = CellTree.from_gazetteer(GAZ)
KB = CueKnowledgeBase(DATA_DIR / "cue_priors.json", GAZ.countries)


def cue(key: str, score: float):
    obs = KB.observation(key, score, (0.1, 0.2, 0.3, 0.6))
    assert obs is not None
    return observation_evidence(obs, TREE, "cue", 1.0)


def test_no_evidence_is_honest() -> None:
    f = fuse(TREE, GAZ, [])
    assert f.resolution == "world"
    assert f.confidence < 5
    assert f.radius_km >= 5000


def test_single_distinctive_tree_localises() -> None:
    """One confident Joshua tree detection is enough for a region-level answer in the Mojave."""
    f = fuse(TREE, GAZ, [cue("joshua_tree", 0.92)])
    assert f.place.country_code == "US"
    assert f.resolution in ("city", "region", "street")
    assert f.confidence > 50
    assert haversine_km(f.latitude, f.longitude, 34.9, -116.2) < 250
    assert f.evidence[0].direction == "supports" and f.evidence[0].likelihood_ratio > 10
    assert f.regions and f.regions[0].label.startswith("Joshua tree")


def test_weak_cue_only_nudges() -> None:
    f = fuse(TREE, GAZ, [cue("wooden_utility_pole", 0.6)])
    assert f.resolution in ("world", "continent")


def test_cues_combine_multiplicatively() -> None:
    one = fuse(TREE, GAZ, [cue("left_hand_traffic", 0.9)])
    two = fuse(TREE, GAZ, [cue("left_hand_traffic", 0.9), cue("eucalyptus", 0.9), cue("red_laterite_soil", 0.8)])
    assert two.place.country_code == "AU"
    assert two.hierarchy[1].code == "AU"
    assert two.hierarchy[1].probability > one.hierarchy[1].probability


def test_hierarchy_is_consistent() -> None:
    f = fuse(TREE, GAZ, [cue("onion_dome", 0.8), cue("birch_forest", 0.7)])
    probs = [h.probability for h in f.hierarchy]
    assert all(a >= b - 1e-9 for a, b in pairwise(probs))  # parents ≥ children
    assert [h.level for h in f.hierarchy] == ["continent", "country", "region", "city"]


def test_calibration_temperature_softens() -> None:
    sharp = fuse(TREE, GAZ, [cue("joshua_tree", 0.92)], temperature=1.0)
    soft = fuse(TREE, GAZ, [cue("joshua_tree", 0.92)], temperature=3.0)
    assert soft.hierarchy[1].probability < sharp.hierarchy[1].probability


def test_mean_shift_picks_a_mode_not_the_midpoint() -> None:
    lat = np.array([38.72, 38.73, 38.71, 41.15])
    lon = np.array([-9.14, -9.13, -9.15, -8.63])
    m_lat, m_lon = mean_shift_mode(lat, lon, np.ones(4), bandwidth_km=10)
    assert haversine_km(m_lat, m_lon, 38.72, -9.14) < 3  # Lisbon, not somewhere near Coimbra


@pytest.mark.parametrize("lon1,lon2", [(179.9, -179.9)])
def test_antimeridian(lon1: float, lon2: float) -> None:
    assert haversine_km(0, lon1, 0, lon2) < 25

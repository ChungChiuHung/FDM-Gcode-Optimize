import re
import pytest

from src.physics.kinematics import calculate_inertia_dampening

# ---------------------------------------------------------------------------
# Mock G-code: minimal file with outer-wall and infill feature blocks.
# The parser tags moves with the last "; FEATURE: …" comment it encountered,
# which mirrors what Bambu Studio / Orca Slicer emit.
# ---------------------------------------------------------------------------
_MOCK_GCODE = """\
; EXECUTABLE_BLOCK_START
G28
M83
; FEATURE: Outer wall
G1 X10.000 Y10.000 F3000
G1 X20.000 Y10.000 F3000
G1 X20.000 Y20.000 F3000
G1 X10.000 Y20.000 F3000
; FEATURE: Inner wall
G1 X11.000 Y11.000 F3000
G1 X19.000 Y19.000 F3000
; FEATURE: Sparse infill
G1 X12.500 Y12.500 F6000
G1 X17.500 Y17.500 F6000
G1 X12.500 Y17.500 F6000
"""

_FEATURE_RE = re.compile(r'^;\s*FEATURE:\s*(.+)$', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_gcode_file(tmp_path):
    """Write a minimal G-code file containing outer wall and infill features."""
    path = tmp_path / "mock_print.gcode"
    path.write_text(_MOCK_GCODE, encoding="utf-8")
    return path


@pytest.fixture
def gcode_features(mock_gcode_file):
    """Parse feature labels out of the mock G-code file and return them as a list."""
    features = []
    with open(mock_gcode_file, encoding="utf-8") as fh:
        for line in fh:
            m = _FEATURE_RE.match(line.strip())
            if m:
                features.append(m.group(1).strip())
    return features


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_mock_gcode_contains_required_features(gcode_features):
    """Fixture sanity: the mock file must contain at least one wall and one infill feature."""
    labels_lower = [f.lower() for f in gcode_features]
    assert any('outer wall' in lbl for lbl in labels_lower), "Mock G-code missing outer wall feature"
    assert any('infill' in lbl for lbl in labels_lower), "Mock G-code missing infill feature"


def test_outer_wall_acceleration(gcode_features):
    """Outer wall features must receive low acceleration (2000 mm/s²) to suppress ringing."""
    outer_walls = [f for f in gcode_features if 'outer wall' in f.lower()]
    assert outer_walls, "No outer wall features found in mock G-code"
    for feature in outer_walls:
        accel = calculate_inertia_dampening(feature)
        assert accel == 2000, (
            f"Expected 2000 mm/s² for '{feature}', got {accel}"
        )


def test_infill_acceleration(gcode_features):
    """Infill features must receive high acceleration (5000 mm/s²) for throughput."""
    infill_feats = [f for f in gcode_features if 'infill' in f.lower()]
    assert infill_feats, "No infill features found in mock G-code"
    for feature in infill_feats:
        accel = calculate_inertia_dampening(feature)
        assert accel == 5000, (
            f"Expected 5000 mm/s² for '{feature}', got {accel}"
        )


def test_wall_outer_alias_acceleration():
    """wall-outer keyword (Orca Slicer alias) must also map to 2000 mm/s²."""
    assert calculate_inertia_dampening("wall-outer") == 2000


def test_external_alias_acceleration():
    """external perimeter keyword must map to 2000 mm/s²."""
    assert calculate_inertia_dampening("External perimeter") == 2000


# ---------------------------------------------------------------------------
# TravelRouter — combing / avoid-crossing-perimeters
# ---------------------------------------------------------------------------

from src.safety.travel_router import TravelRouter
from src.safety.z_hop_injector import ZHopInjector


def _make_hop_injector(segments):
    """Build a ZHopInjector pre-loaded with the given (x1,y1,x2,y2) wall segments."""
    inj = ZHopInjector(z_hop=0.4)
    for seg in segments:
        inj.add_wall_seg(*seg)
    return inj


def test_router_clear_path_returns_end_only():
    """A direct path with no walls returns [end_pt] — no detour."""
    inj = _make_hop_injector([])
    result = TravelRouter.route_safe_travel((0.0, 0.0), (50.0, 50.0), inj)
    assert result == [(50.0, 50.0)]


def test_router_microglide_skips_routing():
    """Travels shorter than MIN_DETOUR_DIST (3 mm) return [end_pt] without routing."""
    # Wall directly in the path — router must skip because of the distance guard.
    inj = _make_hop_injector([(1.0, -1.0, 1.0, 1.0)])
    result = TravelRouter.route_safe_travel((0.0, 0.0), (2.0, 0.0), inj)
    assert result == [(2.0, 0.0)]


def test_router_blocked_path_returns_detour():
    """A horizontal wall blocking a straight travel must produce a 2-waypoint path."""
    # Wall: Y=5, X 0→100 — blocks any travel from (50,0) to (50,100).
    inj = _make_hop_injector([(0.0, 5.0, 100.0, 5.0)])
    result = TravelRouter.route_safe_travel((50.0, 0.0), (50.0, 100.0), inj)
    assert len(result) == 2, f"Expected detour, got: {result}"
    waypoint, final = result
    assert final == (50.0, 100.0)
    assert waypoint != (50.0, 0.0)
    assert waypoint != (50.0, 100.0)


def test_router_clear_path_no_wall_hit():
    """A travel parallel to a wall that does not cross it returns [end_pt]."""
    # Wall at X=100; travel from (0,0) to (50,0) never crosses it.
    inj = _make_hop_injector([(100.0, -10.0, 100.0, 10.0)])
    result = TravelRouter.route_safe_travel((0.0, 0.0), (50.0, 0.0), inj)
    assert result == [(50.0, 0.0)]


def test_router_waypoint_clears_both_legs():
    """Every waypoint in a detour must produce clear sub-paths."""
    # Vertical wall at X=25 blocks direct (0,50)→(50,50) travel.
    inj = _make_hop_injector([(25.0, 40.0, 25.0, 60.0)])
    result = TravelRouter.route_safe_travel((0.0, 50.0), (50.0, 50.0), inj)
    if len(result) == 2:
        wp, end = result
        # Validate each leg using the injector's own check_intersection API.
        assert not inj.check_intersection((0.0, 50.0), wp), "Leg 1 still blocked"
        assert not inj.check_intersection(wp, end), "Leg 2 still blocked"


# ---------------------------------------------------------------------------
# TravelRouter — Bug #9: inner circle / hole geometry (Phase 2 bridge)
# ---------------------------------------------------------------------------
# Geometry: a simplified hole outline at centre (50, 50).
# Four segments form a cross-shaped barrier that blocks all Phase-1 single-
# waypoint detours while leaving clear lanes directly above/below (y > 67 or
# y < 33) and directly to the sides (x < 33 or x > 67):
#
#   left wall  : x=33, y 48→52  (blocks horizontal travel at y≈50)
#   right wall : x=67, y 48→52  (blocks horizontal travel at y≈50)
#   top wall   : y=67, x 48→52  (verifies bridge clears the obstacle top)
#   bottom wall: y=33, x 48→52  (verifies bridge clears the obstacle bottom)
#
# Direct travel (20,50)→(80,50) crosses both left and right walls.
# Phase-1 AABB corners (≈ x=31/69, y=46/54) still have legs that cross the
# opposite wall, so Phase 1 exhausts all 6 candidates and Phase 2 fires.
# ---------------------------------------------------------------------------

_HOLE_SEGS = [
    (33.0, 48.0, 33.0, 52.0),  # left wall
    (67.0, 48.0, 67.0, 52.0),  # right wall
    (48.0, 67.0, 52.0, 67.0),  # top wall   (keeps bridge honest)
    (48.0, 33.0, 52.0, 33.0),  # bottom wall
]


def test_router_circle_hole_returns_bridge_path():
    """Horizontal travel across a simulated hole must produce a 3-point bridge."""
    inj = _make_hop_injector(_HOLE_SEGS)
    result = TravelRouter.route_safe_travel((20.0, 50.0), (80.0, 50.0), inj)
    # Phase 2 must fire: expect [wp1, wp2, end_pt] — a three-point path.
    assert len(result) == 3, (
        f"Expected 3-point bridge for circular hole, got {len(result)}-point path: {result}"
    )
    assert result[-1] == (80.0, 50.0), "Last waypoint must be the original end_pt"


def test_router_bridge_all_legs_clear():
    """Every leg of the perpendicular bridge must clear the hole outline."""
    inj = _make_hop_injector(_HOLE_SEGS)
    start = (20.0, 50.0)
    end = (80.0, 50.0)
    result = TravelRouter.route_safe_travel(start, end, inj)
    assert len(result) == 3, f"Expected bridge path, got: {result}"
    wp1, wp2, ep = result
    assert not inj.check_intersection(start, wp1),  "Bridge leg 1 (start→WP1) still blocked"
    assert not inj.check_intersection(wp1, wp2),    "Bridge leg 2 (WP1→WP2) still blocked"
    assert not inj.check_intersection(wp2, ep),     "Bridge leg 3 (WP2→end) still blocked"

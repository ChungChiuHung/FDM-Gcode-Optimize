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

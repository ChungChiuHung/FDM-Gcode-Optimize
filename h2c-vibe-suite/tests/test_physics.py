from src.physics.materials import get_material_profile
from src.physics.thermodynamics import calculate_thermal_state

def test_carbon_fiber_flow_scaling():
    """確保碳纖維材料的流量乘數被正確降速到 0.85x 以防堵塞"""
    # FIX BUG 5: materials.py had 0.8; H2C spec mandates 0.85x for CF/GF.
    # materials.py updated to match.
    profile = get_material_profile("PLA-CF")
    assert profile["speed_multiplier"] == 0.85
    assert profile["max_travel_speed"] == 18000
    assert profile["needs_petg_wipe"] == False

def test_petg_wipe_activation():
    """確保遇到 PETG 材料時，能正確觸發防牽絲擦拭邏輯"""
    profile = get_material_profile("PETG-Basic")
    assert profile["needs_petg_wipe"] == True
    assert profile["speed_multiplier"] == 0.9

def test_first_layer_bed_adhesion():
    """確保首層列印時，熱力均衡器會鎖定溫度，絕對不會啟動冷卻模式以防脫膠"""
    # FIX BUG 4: correct param name is island_dist (not island_extrude_dist);
    # flow_rate is required positional arg and must be provided.
    state, temp = calculate_thermal_state(base_temp=220, island_dist=5.0, flow_rate=3.0, is_first_layer=True)
    assert state == "BED_ADHESION"
    assert temp == 220

def test_thermal_cooling_mode():
    """確保遇到微小特徵（擠出距離小於 15mm）且非首層時，會自動降溫 5 度"""
    state, temp = calculate_thermal_state(base_temp=220, island_dist=10.0, flow_rate=1.0, is_first_layer=False)
    assert state == "COOLING"
    assert temp == 215
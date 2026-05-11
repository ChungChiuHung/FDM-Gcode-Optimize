import re
from typing import Tuple, Optional

class ThermalProfile:
    """
    物理熱力學常數設定 (H2C/X1C 高速列印標準)
    用來計算聚合物在加熱區內的熱量消耗 (Thermal Draw) 與冷卻需求。
    """
    BONDING_TEMP_BOOST = 7      # °C: 增加分子鏈纏結的升溫幅度
    COOLING_TEMP_DROP = 5       # °C: 防止微小特徵塌陷的降溫幅度
    
    HIGH_FLOW_THRESHOLD = 12.0  # mm³/s: 觸發高流速補償的門檻
    LONG_PATH_THRESHOLD = 150.0 # mm: 觸發長路徑強度強化的門檻
    MICRO_PATH_THRESHOLD = 15.0 # mm: 觸發微小特徵冷卻的門檻
    LOW_FLOW_THRESHOLD = 3.0    # mm³/s: 判斷為精細點補的流速門檻

def get_base_temperature(header_text: str) -> Optional[int]:
    """從 G-Code 標頭中提取噴嘴溫度。"""
    temp_match = re.search(r';\s*nozzle_temperature\s*=\s*([\d,\s]+)', header_text)
    if temp_match:
        vals = re.findall(r'\d+', temp_match.group(1))
        if vals: return int(vals[0])
    
    first_layer_match = re.search(r';\s*nozzle_temperature_initial_layer\s*=\s*([\d,\s]+)', header_text)
    if first_layer_match:
        vals = re.findall(r'\d+', first_layer_match.group(1))
        if vals: return int(vals[0])
    
    m104_match = re.search(r'M10[49]\s+S(\d+)', header_text)
    if m104_match:
        return int(m104_match.group(1))
        
    return None

def calculate_thermal_state(
    base_temp: int, 
    island_dist: float, 
    flow_rate: float, 
    is_first_layer: bool = False
) -> Tuple[str, int]:
    """品質優化核心：動態調整噴嘴溫度以維持穩定的腔體壓力。"""
    # 首層為了確保完美的流動性與物理黏附，強制鎖定在基礎高溫，不進行動態微調
    if is_first_layer:
        return "BED_ADHESION", base_temp

    # Bonding Mode (強固模式): 大流量或長距離直線，提溫確保塑料有足夠熱量融化並與下層纏結
    if flow_rate >= ThermalProfile.HIGH_FLOW_THRESHOLD or island_dist >= ThermalProfile.LONG_PATH_THRESHOLD:
        return "BONDING", base_temp + ThermalProfile.BONDING_TEMP_BOOST
    
    # Cooling Mode (冷卻模式): 極短距離與低流速 (如小圓柱、頂部尖端)，降溫防止塑料過熱塌陷
    if island_dist <= ThermalProfile.MICRO_PATH_THRESHOLD and flow_rate <= ThermalProfile.LOW_FLOW_THRESHOLD:
        return "COOLING", base_temp - ThermalProfile.COOLING_TEMP_DROP
        
    return "NORMAL", base_temp
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

def get_base_temperature(header_text: str) -> Optional[dict]:
    """從 G-Code 標頭中分別提取「首層溫度」與「一般層溫度」。"""
    temps = {"initial": 0, "normal": 0}
    
    # 1. 提取一般層溫度
    temp_match = re.search(r';\s*nozzle_temperature\s*=\s*([\d,\s]+)', header_text)
    if temp_match:
        vals = re.findall(r'\d+', temp_match.group(1))
        if vals: temps["normal"] = int(vals[0])
    
    # 2. 提取首層溫度
    first_layer_match = re.search(r';\s*nozzle_temperature_initial_layer\s*=\s*([\d,\s]+)', header_text)
    if first_layer_match:
        vals = re.findall(r'\d+', first_layer_match.group(1))
        if vals: temps["initial"] = int(vals[0])
        
    # 3. Fallback: 如果都沒有標籤，找 M104 指令
    if temps["normal"] == 0 and temps["initial"] == 0:
        m104_match = re.search(r'M10[49]\s+S(\d+)', header_text)
        if m104_match:
            fallback = int(m104_match.group(1))
            temps["normal"] = fallback
            temps["initial"] = fallback
        else:
            return None

    # 4. 防呆：補齊可能缺失的單一溫度
    if temps["initial"] == 0: temps["initial"] = temps["normal"]
    if temps["normal"] == 0: temps["normal"] = temps["initial"]
    
    # [!] 診斷系統：將抓取到的溫度印在日誌中，讓你確認切片軟體的數值是否正確
    print(f"  [Diagnostics] 噴嘴溫度讀取成功 -> 一般層: {temps['normal']}°C | 首層: {temps['initial']}°C")
    
    return temps

def calculate_thermal_state(
    base_temp_dict: dict, 
    island_dist: float, 
    flow_rate: float, 
    is_first_layer: bool = False
) -> Tuple[str, int]:
    """品質優化核心：動態調整噴嘴溫度以維持穩定的腔體壓力。"""
    
    # 首層會精準讀取切片軟體設定的「首層專用溫度」
    if is_first_layer:
        return "BED_ADHESION", base_temp_dict["initial"]

    # 後續的加溫/降溫計算，皆以「一般層溫度」為基礎浮動
    normal_temp = base_temp_dict["normal"]

    # Bonding Mode (強固模式): 大流量或長距離直線
    if flow_rate >= ThermalProfile.HIGH_FLOW_THRESHOLD or island_dist >= ThermalProfile.LONG_PATH_THRESHOLD:
        return "BONDING", normal_temp + ThermalProfile.BONDING_TEMP_BOOST
    
    # Cooling Mode (冷卻模式): 極短距離與低流速
    if island_dist <= ThermalProfile.MICRO_PATH_THRESHOLD and flow_rate <= ThermalProfile.LOW_FLOW_THRESHOLD:
        return "COOLING", normal_temp - ThermalProfile.COOLING_TEMP_DROP
        
    return "NORMAL", normal_temp
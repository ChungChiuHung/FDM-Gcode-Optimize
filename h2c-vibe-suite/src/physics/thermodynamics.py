import re
from typing import Tuple, Optional

def get_base_temperature(header_text: str) -> Optional[int]:
    """
    尋找切片軟體預期的初始噴嘴溫度。
    支援 Bambu Studio / Orca Slicer 的單一或陣列格式 (例如: 220, 220)
    """
    # 1. 尋找主要溫度標籤
    temp_match = re.search(r';\s*nozzle_temperature\s*=\s*(\d+)', header_text)
    if temp_match:
        return int(temp_match.group(1))
    
    # 2. 備用方案 A：尋找首層專屬溫度標籤
    first_layer_match = re.search(r';\s*nozzle_temperature_initial_layer\s*=\s*(\d+)', header_text)
    if first_layer_match:
        return int(first_layer_match.group(1))
    
    # 3. 備用方案 B：直接掃描標頭中的 M104/M109 加熱指令
    m104_match = re.search(r'M10[49]\s+S(\d+)', header_text)
    if m104_match:
        return int(m104_match.group(1))
        
    return None

def calculate_thermal_state(base_temp: Optional[int], island_extrude_dist: float, is_first_layer: bool) -> Tuple[str, int]:
    """
    根據特徵的物理大小，計算非阻塞的 M104 動態溫度調整。
    
    參數:
        base_temp: 基礎列印溫度
        island_extrude_dist: 該區塊的總擠出距離 (mm)
        is_first_layer: 是否為第一層 (Z <= 0.4)
        
    回傳: 
        Tuple[狀態名稱, 目標溫度]
    """
    # 安全防護：如果沒有擷取到基礎溫度，或該路徑無擠出行為，則維持現狀
    if not base_temp or island_extrude_dist <= 0:
        return "NORMAL", base_temp if base_temp else 0
        
    if is_first_layer:
        # 【首層鎖定】絕對鎖定基礎溫度，確保底層完美融化與熱床附著力 (Bed Squish)
        return "BED_ADHESION", base_temp
        
    elif island_extrude_dist < 15.0:
        # 【降溫模式】防止微小細節（如文字、小齒輪、極短柱）因為熱量不斷累積而融化糊掉
        return "COOLING", base_temp - 5
        
    elif island_extrude_dist > 150.0:
        # 【升溫模式】長直線、大面積外牆或填充，提高溫度讓高分子鏈能深度融合，極大化結構剪切強度
        return "BONDING", base_temp + 5
        
    else:
        # 【常態模式】中等尺寸特徵
        return "NORMAL", base_temp
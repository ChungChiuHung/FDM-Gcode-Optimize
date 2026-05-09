import re
from typing import Tuple

def get_hardware_bounds(header_text: str) -> Tuple[float, float, float, float, float]:
    """
    解析切片標頭找出當前列印機的物理極限 (適用於 A1 Mini, X1C 等不同尺寸)。
    防止 AI 運算出超出物理框架的路徑，避免發生撞機危險。
    
    參數:
        header_text: G-code 檔案的標頭字串內容
        
    回傳:
        Tuple[float, float, float, float, float]: (MIN_X, MAX_X, MIN_Y, MAX_Y, MAX_Z)
    """
    # 預設為 Bambu Lab X1C/P1S 標準列印尺寸作為安全底線
    bed_max_x, bed_max_y, bed_max_z = 256.0, 256.0, 256.0
    
    try:
        # 1. 解析印床面積 (通常格式如: 0x0, 256x0, 256x256, 0x256)
        area_match = re.search(r';\s*printable_area\s*=\s*(.*)', header_text, re.IGNORECASE)
        if area_match:
            coords_str = area_match.group(1)
            # 擷取字串中所有的正負浮點數或整數
            coords = [float(c) for c in re.findall(r'[-+]?\d*\.\d+|\d+', coords_str)]
            if len(coords) >= 2:
                # 偶數索引為 X，奇數索引為 Y，取最大值即為邊界
                bed_max_x = max(coords[::2])
                bed_max_y = max(coords[1::2])
                
        # 2. 解析最大列印高度
        height_match = re.search(r';\s*printable_height\s*=\s*([-+]?\d*\.\d+|\d+)', header_text, re.IGNORECASE)
        if height_match: 
            bed_max_z = float(height_match.group(1))
            
    except Exception:
        # 防禦性編程：若正則解析或浮點數轉換失敗，靜默攔截並退回 256.0 的預設安全尺寸
        pass

    # 3. 加入機台機構寬限值 (Tolerance)：
    # - 切線刀 (Filament Cutter) 觸發位置在 X 軸最左側，給予 X-5 寬容度
    # - 廢料槽 (Purge Chute) 通常在 Y 軸最後方，給予 Y+10 寬容度
    # - Z 軸安全間隙，給予 Z+2 防止列印頂部時頂穿框架
    HW_MIN_X = -5.0
    HW_MAX_X = bed_max_x + 5.0
    HW_MIN_Y = -5.0
    HW_MAX_Y = bed_max_y + 10.0
    HW_MAX_Z = bed_max_z + 2.0
    
    return HW_MIN_X, HW_MAX_X, HW_MIN_Y, HW_MAX_Y, HW_MAX_Z
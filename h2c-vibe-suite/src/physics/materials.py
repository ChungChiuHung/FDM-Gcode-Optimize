import re
import os

def detect_filament_type(header_text: str, file_path: str = "") -> str:
    """從 G-Code 標頭或檔名偵測線材種類 (支援 AMS 陣列解析)"""
    # 1. Official Slicer Tag Check
    match = re.search(r';\s*(?:filament_type|filament_settings_id)\s*[:=]\s*([^\n\r]+)', header_text, re.IGNORECASE)
    if match:
        val = match.group(1).upper()
        # 處理 Bambu Studio 的 AMS 陣列格式: ["PLA";"PETG";"TPU"]
        # 將其按分號切開，並去除多餘的引號與空白
        filaments = [f.strip(' "') for f in val.split(';') if any(c.isalpha() for c in f)]
        if filaments:
            # 優先回傳陣列中的第一個有效線材 (通常是主要列印件的線材)
            return filaments[0]
        
    # 2. Fallback: Broad Header Keyword Scan
    header_upper = header_text.upper()
    if "PLA-CF" in header_upper or "PLA_CF" in header_upper or "CARBON" in header_upper: return "PLA-CF"
    if "PETG" in header_upper: return "PETG"
    if "TPU" in header_upper or "FLEX" in header_upper: return "TPU"
    if "ABS" in header_upper: return "ABS"
    if "ASA" in header_upper: return "ASA"
    if "PA-CF" in header_upper or "PAHT" in header_upper: return "PA-CF"
    
    # 3. Fallback: Filename Check (Catch-all for custom slices)
    filename = os.path.basename(file_path).upper()
    if "PLA-CF" in filename or "PLA_CF" in filename: return "PLA-CF"
    if "PETG" in filename: return "PETG"
    if "TPU" in filename: return "TPU"
    if "ABS" in filename: return "ABS"

    return "UNKNOWN"

def get_material_profile(filament_type: str) -> dict:
    """
    根據材料物理特性回傳動態速度縮放與 Z-hop 規則。
    可在此處輕鬆新增新材料（如 Nylon-CF），無需修改核心引擎！
    """
    is_soft = any(kw in filament_type for kw in ["TPU", "TPE", "FLEX"])
    is_petg = "PETG" in filament_type
    is_cf_gf = any(kw in filament_type for kw in ["CF", "GF", "CARBON", "GLASS"])
    is_abs_asa = any(kw in filament_type for kw in ["ABS", "ASA"])
    is_nylon = "PA" in filament_type
    
    # 預設基準 Z-hop 為 0.4，確保重新排序的島嶼之間移動安全
    mat_profile = {"z_hop": 0.4, "speed_multiplier": 1.0, "max_travel_speed": 20000, "needs_petg_wipe": is_petg}
    
    if is_soft:
        mat_profile.update({"z_hop": 0.6, "speed_multiplier": 0.5, "max_travel_speed": 12000})
    elif is_cf_gf:
        mat_profile.update({"z_hop": 0.4, "speed_multiplier": 0.85, "max_travel_speed": 18000})
    elif is_petg or is_nylon:
        mat_profile.update({"z_hop": 0.4, "speed_multiplier": 0.9, "max_travel_speed": 24000})
    elif is_abs_asa:
        mat_profile.update({"z_hop": 0.2, "speed_multiplier": 0.95, "max_travel_speed": 25000})
        
    return mat_profile
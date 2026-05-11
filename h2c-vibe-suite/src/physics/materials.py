import re
import os
from typing import Dict, Any

def detect_filament_type(header_text: str, file_path: str = "") -> str:
    """
    從 G-Code 標頭或檔名精準偵測線材種類。
    支援 Bambu Studio / Orca Slicer 的 AMS 陣列解析與多樣化關鍵字。
    """
    # 1. 官方標籤檢查 (處理陣列格式: ["PLA";"PETG";"TPU"] 或 PLA;PETG)
    match = re.search(r';\s*(?:filament_type|filament_settings_id)\s*[:=]\s*([^\n\r]+)', header_text, re.IGNORECASE)
    if match:
        val = match.group(1).upper()
        # 移除陣列括號與引號，並按分號切割
        filaments = [f.strip(' "[]') for f in val.split(';') if any(c.isalpha() for c in f)]
        if filaments:
            # 優先回傳第一個有效項目
            return filaments[0]
        
    # 2. 標頭全文關鍵字掃描 (處理自定義線材標籤)
    header_upper = header_text.upper()
    if any(k in header_upper for k in ["PLA-CF", "PLA_CF", "CARBON", "PLA-MATTE"]):
        return "PLA-CF" if "CARBON" in header_upper or "CF" in header_upper else "PLA"
    if "PETG" in header_upper: return "PETG"
    if any(k in header_upper for k in ["TPU", "TPE", "FLEX"]): return "TPU"
    if "ABS" in header_upper: return "ABS"
    if "ASA" in header_upper: return "ASA"
    if any(k in header_upper for k in ["PA-CF", "PAHT", "NYLON"]): return "PA-CF"
    
    # 3. 備用方案：檔名檢查 (針對沒寫入標頭的快速切片文件)
    filename = os.path.basename(file_path).upper()
    keywords = {"PLA-CF": "PLA-CF", "PETG": "PETG", "TPU": "TPU", "ABS": "ABS", "PA": "PA-CF"}
    for kw, label in keywords.items():
        if kw in filename: return label

    return "UNKNOWN"

def get_material_profile(filament_type: str, header_text: str = "") -> Dict[str, Any]:
    """
    根據材料物理特性回傳品質優化規則。
    加入了最大體積流速 (Max Volumetric Speed) 的保護機制，防止擠出機卡料 (Extruder Skipping/Stalling)。
    
    優化參數:
    - z_hop: 防止噴嘴拖曳導致的表面刮痕。
    - speed_multiplier: 調整流量壓力穩定性。
    - max_volumetric_speed: mm³/s 流量上限保護。
    - needs_uniform_speed: 解決「殼線 (Hull Lines)」問題的關鍵標記。
    - needs_petg_wipe: 針對高黏性材料的特殊結尾清理。
    """
    
    # 從切片軟體標頭中動態提取最大體積流速上限 (支援 AMS 陣列)
    max_vol_speed = None
    if header_text:
        match = re.search(r';\s*filament_max_volumetric_speed\s*[:=]\s*([^\n\r]+)', header_text, re.IGNORECASE)
        if match:
            # 提取數字 (例如: [15, 10] -> [15.0, 10.0])
            vals = [float(v) for v in re.findall(r'\d+\.?\d*', match.group(1))]
            if vals:
                max_vol_speed = vals[0]

    # 初始化標準配置 (PLA 基準)
    profile = {
        "z_hop": 0.4,
        "speed_multiplier": 1.0,
        "max_travel_speed": 20000,
        "max_volumetric_speed": max_vol_speed or 15.0, # 預設 PLA 流量上限
        "needs_uniform_speed": False,
        "needs_petg_wipe": False,
        "glass_transition_temp": 60,  # [!] 新增: 聚合物玻璃轉化溫度 (Tg)
        "label": filament_type
    }

    # 1. 高黏性材料 (PETG, Nylon) - 易產生殼線與拉絲
    if "PETG" in filament_type or "PA" in filament_type:
        profile.update({
            "z_hop": 0.6,               
            "speed_multiplier": 0.9,     
            "max_travel_speed": 18000,
            "max_volumetric_speed": max_vol_speed or 10.0, # PETG 較黏稠，強制降低預設流量上限
            "needs_uniform_speed": True, 
            "needs_petg_wipe": "PETG" in filament_type,
            "glass_transition_temp": 70 if "PETG" in filament_type else 90
        })

    # 2. 彈性材料 (TPU, TPE, FLEX) - 極易導致擠出機齒輪打滑卡料
    elif any(kw in filament_type for kw in ["TPU", "TPE", "FLEX"]):
        profile.update({
            "z_hop": 0.8,               
            "speed_multiplier": 0.5,     
            "max_travel_speed": 12000,
            "max_volumetric_speed": max_vol_speed or 3.2,   # TPU 物理極限，嚴格限制流量
            "glass_transition_temp": 35  # TPU 不需要高溫床
        })

    # 3. 纖維強化材料 (CF, GF, CARBON) - 注意噴嘴磨損
    elif any(kw in filament_type for kw in ["CF", "GF", "CARBON", "GLASS"]):
        profile.update({
            "z_hop": 0.2,               
            "speed_multiplier": 0.8,     
            "max_travel_speed": 15000,
            "max_volumetric_speed": max_vol_speed or 8.0,   # 纖維導致熔體黏度飆升，降低流量上限
            "glass_transition_temp": 65 if "PLA" in filament_type else (75 if "PETG" in filament_type else 100)
        })
        print("\n" + "="*50)
        print("[!] QUALITY ALERT: Abrasive Filament (CF/GF) detected.")
        print("[!] ACTION: Ensure HARDENED STEEL nozzle is installed to maintain DA accuracy.")
        print("="*50 + "\n")

    # 4. 高溫工程材料 (ABS, ASA)
    elif "ABS" in filament_type or "ASA" in filament_type:
        profile.update({
            "z_hop": 0.3,
            "speed_multiplier": 0.95,
            "max_travel_speed": 25000,
            "max_volumetric_speed": max_vol_speed or 18.0,
            "glass_transition_temp": 105
        })

    return profile
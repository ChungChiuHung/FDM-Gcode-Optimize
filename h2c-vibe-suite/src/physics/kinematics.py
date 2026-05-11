import math
from typing import Optional

class KinematicProfile:
    """
    針對重型工具頭 (如 Bambu Lab H2C/X1C/P1S) 優化的硬體運動常數。
    """
    MASS_KG = 0.45                     # 工具頭質量 (約 450g)
    BASE_DWELL_MS = 15.0               # 基礎框架穩定時間
    MAX_DWELL_MS = 50.0                # 最大停頓上限，防止噴嘴溢料 (Oozing)
    
    # CRITICAL: 觸發動能煞車的最小移動距離。
    # 保持在 10.0mm 能保證在列印「小圓圈/螺絲孔」時的微距空跑絕對不會觸發煞車抬升，
    # 徹底防止原位點測 (Pecking) 造成的溢料斑點。
    MIN_TRIGGER_DIST_MM = 10.0         
    MIN_TRIGGER_SPEED_MM_MIN = 6000.0  # 觸發煞車的最小速度 (100mm/s)
    
    # --- 高品質細節設定 ---
    MICRO_LIFT_MM = 0.15               # 煞車穩定時的 Z 軸微抬升，防止轉角燒焦
    Z_LIFT_SPEED_MM_MIN = 1200.0       # 抬升速度
    ENERGY_SCALING_FACTOR = 600.0      # 能量轉化為停頓時間的權重因子

def calculate_inertia_dampening(feature_type: str, is_first_layer: bool, is_travel: bool) -> int:
    """
    根據列印特徵類型動態計算最佳加速度限制，以優化表面品質與防止過擠出。
    """
    # 1. 安全鎖定：首層與空跑
    if is_first_layer:
        return 500   # 極低加速度確保首層黏附
        
    if is_travel:
        # QUALITY FIX: 將空跑加速度拉升至 12000 mm/s²。
        # 在小圓圈內部進行空跑時，極高的加速度能將「滯空時間」縮短至幾毫秒，
        # 讓管內的殘餘壓力與重力來不及反應，從物理上根除牽絲 (Stringing) 與溢料。
        return 12000 

    feat = feature_type.lower() if feature_type else ""
    
    # 2. 精密度優先特徵 (小圓圈 / 螺絲孔 / 間隙填充)
    # QUALITY FIX: 小特徵過擠出保護。
    # 450g 的工具頭在畫小圓圈時頻繁轉向會導致擠出機背壓 (Backpressure) 失控。
    # 強制將加速度箝制在 1500，能讓物理位移與塑料吐出量完美同步。
    if any(kw in feat for kw in ['small', 'gap', 'hole', 'circle']): 
        return 1500  
        
    # 3. 表面品質優先 (外牆)
    elif any(kw in feat for kw in ['outer wall', 'external', 'wall-outer']): 
        return 2000  # 外牆低加速度可完全消除震動產生的鬼影 (Ringing)
        
    # 4. 結構性特徵 (內牆)
    elif any(kw in feat for kw in ['inner wall', 'internal']): 
        return 3500  # 兼顧速度與結構強度
        
    # 5. 高速填充
    elif any(kw in feat for kw in ['infill', 'solid', 'bridge']): 
        return 5000  
        
    return 3000

class AntiResonanceBrake:
    """
    動能反諧振演算法：
    計算工具頭的物理動能 (E = 0.5 * m * v^2)，
    在高速移動結束點注入「軟停機 (Soft-stop)」與「空降穩定 (Air-settle)」。
    """
    
    @staticmethod
    def calculate_settling_time(speed_mm_min: float, distance_mm: float) -> int:
        """計算框架抵消慣性震動所需的毫秒數。"""
        if distance_mm < KinematicProfile.MIN_TRIGGER_DIST_MM or speed_mm_min < KinematicProfile.MIN_TRIGGER_SPEED_MM_MIN:
            return 0
            
        # 轉換為國際標準單位 (m/s) 計算焦耳
        speed_m_sec = speed_mm_min / 60000.0
        
        # 動能計算: E = 0.5 * m * v^2
        kinetic_energy_joules = 0.5 * KinematicProfile.MASS_KG * (speed_m_sec ** 2)
        
        # 根據動能線性縮放停頓時間
        extra_dwell = kinetic_energy_joules * KinematicProfile.ENERGY_SCALING_FACTOR
        
        return int(min(
            KinematicProfile.BASE_DWELL_MS + extra_dwell, 
            KinematicProfile.MAX_DWELL_MS
        ))

    @staticmethod
    def inject_soft_stop(current_accel: int, speed_mm_min: float, distance_mm: float, current_z: float) -> str:
        """
        生成高品質煞車 G-Code：
        軟減速 -> Z軸微抬 -> 空中穩定 -> 返回平面 -> 恢復運動。
        """
        settling_time = AntiResonanceBrake.calculate_settling_time(speed_mm_min, distance_mm)
        if settling_time == 0:
            return ""
        
        brake_gcode = [
            f"\n; --- AI KINEMATIC BRAKING: ENERGY CUSHION ---",
            f"M204 S{max(500, int(current_accel * 0.25))} ; 軟減速墊片",
            f"M205 X2.0 Y2.0 ; 降低瞬間衝力 (Jerk)",
            f"G1 Z{current_z + KinematicProfile.MICRO_LIFT_MM:.3f} F{int(KinematicProfile.Z_LIFT_SPEED_MM_MIN)} ; 絕對坐標Z軸微抬防止融化",
            f"G4 P{settling_time} ; 框架殘餘震動吸收 ({settling_time}ms)",
            f"G1 Z{current_z:.3f} F{int(KinematicProfile.Z_LIFT_SPEED_MM_MIN)} ; 回歸列印平面",
            f"M204 S{current_accel} ; 恢復列印動能",
            f"M205 X9.0 Y9.0 ; 恢復標準衝力",
            f"; ---------------------------------------------\n"
        ]
        
        return "\n".join(brake_gcode)
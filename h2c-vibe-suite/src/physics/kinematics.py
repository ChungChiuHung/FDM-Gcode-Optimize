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
    def inject_soft_stop(speed_mm_min: float, distance_mm: float) -> str:
        """
        生成純物理停頓 G-Code：
        在高速移動結束點注入 G4 停頓以吸收框架殘餘震動。
        加速度與 Z 軸幾何完全交由切片軟體管理。
        """
        settling_time = AntiResonanceBrake.calculate_settling_time(speed_mm_min, distance_mm)
        if settling_time == 0:
            return ""

        brake_gcode = [
            f"\n; --- AI KINEMATIC BRAKING: PURE PHYSICS DWELL ---",
            f"G4 P{settling_time} ; 框架殘餘震動吸收 ({settling_time}ms)",
            f"; -------------------------------------------------\n"
        ]

        return "\n".join(brake_gcode)


def calculate_inertia_dampening(feature: str) -> int:
    """
    Return the M204 acceleration (mm/s²) appropriate for a G-code feature.

    Rules (per H2C spec):
      - Outer-wall / wall-outer / external → 2000  (low accel for surface quality)
      - Infill                             → 5000  (high accel for speed)
      - All other features                 → 5000  (permissive default)
    """
    f = feature.lower()
    if any(kw in f for kw in ('outer wall', 'wall-outer', 'external')):
        return 2000
    if 'infill' in f:
        return 5000
    return 5000
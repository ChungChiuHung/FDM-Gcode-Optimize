import math
from typing import Optional

class KinematicProfile:
    """
    Hardware-specific kinematic constants. 
    Currently tuned for heavy CoreXY toolheads (e.g., Bambu Lab X1C / P1S).
    """
    MASS_KG = 0.45                     # Toolhead mass in Kilograms (450g)
    BASE_DWELL_MS = 15.0               # Baseline frame stabilization time
    MAX_DWELL_MS = 50.0                # Hard cap to prevent filament oozing
    MIN_TRIGGER_DIST_MM = 10.0         # Minimum distance to accumulate dangerous momentum
    MIN_TRIGGER_SPEED_MM_MIN = 6000.0  # Minimum speed (100mm/s) to require braking
    
    # --- New: Lift-Braking Settings ---
    MICRO_LIFT_MM = 0.15               # Z-lift height during resonant braking (avoids corner melting)
    Z_LIFT_SPEED_MM_MIN = 1200.0       # Speed of the micro-lift (fast enough to prevent ooze)

def calculate_inertia_dampening(feature_type: str, is_first_layer: bool, is_travel: bool) -> int:
    """
    Dynamically calculates the optimal acceleration limit based on the structural 
    importance and physical requirements of the current print feature.
    """
    if is_first_layer:
        return 500 
        
    if is_travel:
        return 10000

    feat = feature_type.lower() if feature_type else ""
    
    # High precision/delicate features
    if 'small' in feat or 'gap' in feat: 
        return 1500
    # Exterior surface quality (Supports Bambu, Prusa, and Cura tags)
    elif 'outer wall' in feat or 'external perimeter' in feat or 'wall-outer' in feat: 
        return 2000
    # Internal structural integrity
    elif 'inner wall' in feat or 'internal' in feat: 
        return 3000
    # High-speed internal routing
    elif 'infill' in feat or 'solid' in feat: 
        return 5000
        
    return 3000

class AntiResonanceBrake:
    """
    Kinematic Braking Algorithm:
    Calculates the true kinetic energy (Joules) of the toolhead and applies 
    soft-stop deceleration combined with a Micro-Lift (Z-hop) to cancel 
    mechanical ringing without melting corners.
    """
    
    @staticmethod
    def calculate_settling_time(speed_mm_min: float, distance_mm: float) -> int:
        """
        Calculates how many milliseconds the machine needs to pause to let 
        momentum-induced vibrations cancel out and stabilize.
        """
        # If the move was short or slow, no vibration cancellation is needed
        if distance_mm < KinematicProfile.MIN_TRIGGER_DIST_MM or speed_mm_min < KinematicProfile.MIN_TRIGGER_SPEED_MM_MIN:
            return 0
            
        # Convert to standard SI units (m/s) for accurate Joules calculation
        speed_m_sec = speed_mm_min / 60000.0
        
        # Kinetic Energy (Joules): E = 0.5 * m * v^2
        kinetic_energy_joules = 0.5 * KinematicProfile.MASS_KG * (speed_m_sec ** 2)
        
        # Scale extra dwell based on Joules. 
        # Example: 0.056 Joules (500mm/s) * 600 = ~33ms of extra dynamic dwell
        energy_scaling_factor = 600.0 
        extra_dwell = kinetic_energy_joules * energy_scaling_factor
        
        total_dwell_ms = int(min(KinematicProfile.BASE_DWELL_MS + extra_dwell, KinematicProfile.MAX_DWELL_MS))
        
        return total_dwell_ms

    @staticmethod
    def inject_soft_stop(current_accel: int, speed_mm_min: float, distance_mm: float) -> str:
        """
        Generates the G-Code block to softly decelerate, lift off the print surface,
        wait for frame stabilization, return to the exact layer height, and safely resume.
        """
        settling_time = AntiResonanceBrake.calculate_settling_time(speed_mm_min, distance_mm)
        if settling_time == 0:
            return "" # No braking needed for this move
            
        brake_gcode = []
        
        # 1. Soft Brake: Drop Acceleration and Jerk dynamically to cushion the stop
        soft_accel = max(500, int(current_accel * 0.25))
        brake_gcode.append(f"; --- AI KINEMATIC BRAKING: LIFT & SETTLE ---")
        brake_gcode.append(f"M204 S{soft_accel} ; Cushion deceleration")
        brake_gcode.append(f"M205 X2.0 Y2.0 ; Drop Jerk to absorb corner shock")
        
        # 2. The Micro-Lift: Safely raise the nozzle to prevent corner melting/oozing
        brake_gcode.append(f"G91 ; Switch to relative positioning")
        brake_gcode.append(f"G1 Z{KinematicProfile.MICRO_LIFT_MM:.3f} F{int(KinematicProfile.Z_LIFT_SPEED_MM_MIN)} ; Kinematic Micro-Lift")
        
        # 3. Resonant Settling: Micro-pause to let frame vibrations cancel out IN THE AIR
        brake_gcode.append(f"G4 P{settling_time} ; Anti-Resonance Dwell ({settling_time}ms to stabilize)")
        
        # 4. Return to Plane: Drop back down exactly to the original layer height
        brake_gcode.append(f"G1 Z-{KinematicProfile.MICRO_LIFT_MM:.3f} F{int(KinematicProfile.Z_LIFT_SPEED_MM_MIN)} ; Return to plane")
        brake_gcode.append(f"G90 ; Restore absolute positioning")
        
        # 5. Clean Exit: Restore normal operating kinematics for the next path
        brake_gcode.append(f"M204 S{current_accel} ; Restore operating momentum")
        brake_gcode.append(f"M205 X9.0 Y9.0 ; Restore normal Jerk")
        brake_gcode.append(f"; ---------------------------------------------")
        
        return "\n".join(brake_gcode)
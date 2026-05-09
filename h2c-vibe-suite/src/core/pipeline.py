import sys
import os
import math
import shutil
import argparse

# --- Path Resolution ---
# Automatically find the project root (3 levels up from src/core/pipeline.py)
# This allows the script to be run from anywhere and still find the 'src' modules.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# --- Import Modular AI Plugins (Nested Directory Structure) ---
from src.core.parser import GCodeParser
from src.core.pathing import PathOptimizer
from src.physics.materials import detect_filament_type, get_material_profile
from src.physics.thermodynamics import get_base_temperature, calculate_thermal_state
from src.physics.kinematics import calculate_inertia_dampening
from src.safety.bounds_enforcer import get_hardware_bounds

# --- Check for Optional Agent Tools ---
try:
    from src.safety.collision_sandbox import GCodeCollisionChecker
    HAS_AUDITOR = True
except ImportError:
    HAS_AUDITOR = False
    print("Notice: src/safety/collision_sandbox.py not found. Automated post-audit disabled.")

try:
    from src.agent import mcp_server
    MCP_AVAILABLE = True
except (ImportError, SystemExit):
    MCP_AVAILABLE = False
    print("Notice: src/agent/mcp_server.py not found. Advanced MCP injection disabled.")


def auto_optimize_gcode(input_path: str, is_heavy_toolhead: bool = True):
    """
    Main entry point for the H2C Geometric AI Optimizer.
    """
    if not os.path.exists(input_path): 
        print(f"Error: File not found: {input_path}")
        return False
    
    parser = GCodeParser()
    print("[*] Initiating Generator Parsing (OOM-Safe Streaming)...")
    
    # Peek at header to load modular configs
    with open(input_path, 'r', encoding='utf-8', errors='ignore') as f:
        header_text = ""
        in_config = False
        for i, line in enumerate(f):
            header_text += line
            line_upper = line.upper()
            
            # Track the config block to prevent premature termination
            if "; CONFIG_BLOCK_START" in line_upper:
                in_config = True
            elif "; CONFIG_BLOCK_END" in line_upper:
                in_config = False
                
            # Only break out of the header loop if we are strictly outside the config block
            if not in_config:
                if ";===== PRINT BODY =====" in line_upper or line_upper.startswith("; LAYER_CHANGE") or line_upper.startswith(";LAYER_CHANGE") or i > 1500:
                    break
                
    # 1. Load Physics & Hardware Plugins
    filament_type = detect_filament_type(header_text, input_path)
    mat_profile = get_material_profile(filament_type)
    base_temp = get_base_temperature(header_text)
    HW_MIN_X, HW_MAX_X, HW_MIN_Y, HW_MAX_Y, HW_MAX_Z = get_hardware_bounds(header_text)
    
    print(f"[*] Material Profile [{filament_type}]: Flow Scale={mat_profile['speed_multiplier']}x")
    if base_temp: print(f"[*] Active Thermal Equalizer Ready: Baseline = {base_temp}°C")

    # Safety Backup
    backup_path = input_path + ".bak"
    if not os.path.exists(backup_path): shutil.copy2(input_path, backup_path)
    output_path = input_path 
    hw_violations = 0

    print("[*] Applying Hybrid Cluster-Sweep & Modular Physics...")
    
    stats = {"islands": 0}
    last_x, last_y, current_z, active_feat = 0.0, 0.0, None, None
    current_accel_state = None
    current_eq_state = "NORMAL"

    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        layer_generator = parser.parse_streaming(backup_path)
        first_layer = next(layer_generator, None)
        
        # Write extracted header
        f.write("".join(parser.header_lines))
        
        def process_layer(layer):
            nonlocal stats, last_x, last_y, current_z, active_feat, hw_violations, current_accel_state, current_eq_state
            if not layer: return
            
            # PLUGIN: Core Path Optimization (Sorting Islands)
            optimized_islands = PathOptimizer.hybrid_sort(layer)
            stats["islands"] += len(optimized_islands)
            
            for idx, island in enumerate(optimized_islands):
                start_x, start_y, start_z = island[0]['start']
                jump_dist = math.hypot(start_x - last_x, start_y - last_y)
                is_micro_glide = (idx > 0) and (jump_dist < 2.0) and (abs(start_z - (current_z or start_z)) < 0.001)

                if current_z is None or abs(start_z - current_z) > 0.001:
                    f.write(f"G1 Z{start_z:.3f} F1200 ; AI Layer Update\n")
                    current_z = start_z

                is_first_layer = (start_z <= 0.4)

                # PLUGIN: Material Adaptive Z-Hops (Collision Prevention)
                if not is_micro_glide and mat_profile["z_hop"] > 0:
                    f.write(f"G91\nG1 Z{mat_profile['z_hop']} F600\nG90\n")
                    f.write(f"G1 X{start_x:.3f} Y{start_y:.3f} F{mat_profile['max_travel_speed']}\n")
                    f.write(f"G91\nG1 Z-{mat_profile['z_hop']} F600\nG90\n")
                elif jump_dist > 0.001:
                    f.write(f"G1 X{start_x:.3f} Y{start_y:.3f} F{mat_profile['max_travel_speed']}\n")

                # PLUGIN: Active Thermal Equalizer
                island_extrude_dist = sum(math.hypot(m['end'][0]-m['start'][0], m['end'][1]-m['start'][1]) for m in island if m['type'] == 'extrude')
                if base_temp and island_extrude_dist > 0:
                    target_state, target_temp = calculate_thermal_state(base_temp, island_extrude_dist, is_first_layer)
                    if current_eq_state != target_state:
                        f.write(f"\n; --- AI Thermal Equalizer: {target_state} MODE ---\n")
                        f.write(f"M104 S{target_temp} ; Dynamic Temp Shift (Non-Blocking)\n")
                        current_eq_state = target_state

                # Route the actual coordinates
                for move in island:
                    for meta_line in move.get('metadata', []): f.write(f"{meta_line}\n")
                    feat = move.get('feature')
                    
                    if feat != active_feat:
                        f.write(f"{feat}\n")
                        active_feat = feat
                        
                        # PLUGIN: Inertia Dampening
                        if is_heavy_toolhead and feat:
                            is_travel = (move['type'] == 'travel')
                            new_accel = calculate_inertia_dampening(feat, is_first_layer, is_travel)
                            if new_accel != current_accel_state:
                                f.write(f"M204 S{new_accel} ; AI Inertia Dampening\n")
                                current_accel_state = new_accel

                    ex, ey, ez = move['end']
                    
                    # PLUGIN: Hardware Bounds Enforcement
                    if move.get('g_code', 1) not in (2, 3) and move.get('has_xy', True):
                        if not (HW_MIN_X <= ex <= HW_MAX_X and HW_MIN_Y <= ey <= HW_MAX_Y and ez <= HW_MAX_Z):
                            hw_violations += 1
                            ex, ey, ez = max(HW_MIN_X, min(HW_MAX_X, ex)), max(HW_MIN_Y, min(HW_MAX_Y, ey)), min(HW_MAX_Z, ez)
                        
                    f_val = move['feedrate']
                    if move['type'] == 'travel':
                        f_val = min(f_val, mat_profile['max_travel_speed'])
                    else:
                        f_val *= mat_profile['speed_multiplier']
                    
                    e_val, g_cmd = move['e_val'], move.get('g_code', 1)
                    xy_str = f" X{ex:.3f} Y{ey:.3f}" if move.get('has_xy', True) else ""
                    if xy_str: last_x, last_y = ex, ey
                        
                    z_str = ""
                    if abs(ez - current_z) > 0.001:
                        z_str = f" Z{ez:.3f}"
                        current_z = ez
                        
                    arc_str = ""
                    if g_cmd in (2, 3):
                        if move.get('i') is not None: arc_str += f" I{move['i']:.3f}"
                        if move.get('j') is not None: arc_str += f" J{move['j']:.3f}"
                        if move.get('r') is not None: arc_str += f" R{move['r']:.3f}"
                        if move.get('p') is not None: arc_str += f" P{int(move['p'])}"

                    if xy_str or z_str or arc_str or abs(e_val) > 0.0001:
                        e_str = f" E{e_val:.5f}" if abs(e_val) > 0.0001 else ""
                        f.write(f"G{g_cmd}{xy_str}{z_str}{arc_str}{e_str} F{int(f_val)}\n")

        # Process layers dynamically via generator
        if first_layer: process_layer(first_layer)
        for layer in layer_generator: process_layer(layer)

        if mat_profile.get("needs_petg_wipe", False):
            f.write("\n; --- AI: IDEX-SAFE WIPE ---\nM106 S255\nG91\nG1 E-2.0 F3600\nG1 Z2.0 F6000\nG90\nM400\nM106 S0\n")
        
        # Write extracted footer
        f.write("".join(parser.footer_lines))

    print(f"[*] Optimization Success: {stats['islands']} islands sorted dynamically.")
    if hw_violations > 0: print(f"[!] SAFETY WARNING: {hw_violations} moves forcefully clamped to prevent crashes.")
    
    # Trigger AI Hardware Extensions (e.g. Laser triggers for bridges)
    if MCP_AVAILABLE:
        print("\n=== INITIATING MCP HARDWARE TRIGGERS ===")
        try:
            print(f"MCP Action: {mcp_server.inject_m1004_at_feature(output_path, 'bridge', 4)}")
        except Exception as e: print(f"MCP Injection Error: {str(e)}")

    # Closed-Loop Verification
    if HAS_AUDITOR:
        print("\n=== INITIATING AUTOMATED CLOSED-LOOP AUDIT ===")
        GCodeCollisionChecker().run_check(output_path)
        
    return True

if __name__ == "__main__":
    parser_cli = argparse.ArgumentParser(description="H2C Geometric AI G-Code Optimizer (v4.0 - Enterprise)")
    parser_cli.add_argument("input", help="Path to the raw .gcode file")
    parser_cli.add_argument("--disable-heavy", action="store_true", help="Disable the H2C Heavy Toolhead Inertia Dampening")
    args = parser_cli.parse_args()

    auto_optimize_gcode(args.input, is_heavy_toolhead=not args.disable_heavy)
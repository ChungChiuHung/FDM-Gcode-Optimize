import sys
import os
import re
import math
import shutil
import argparse
from typing import List, Dict, Tuple, Generator

# Automatically add the script's directory to the path so it can find local modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# --- Module Availability Checks ---
try:
    from gcode_collision_checker import GCodeCollisionChecker
    HAS_AUDITOR = True
except ImportError:
    HAS_AUDITOR = False
    print("Notice: gcode_collision_checker.py not found. Automated post-audit disabled.")

try:
    import gcode_mcp_server
    MCP_AVAILABLE = True
except (ImportError, SystemExit):
    MCP_AVAILABLE = False
    print("Notice: gcode_mcp_server.py not found. Advanced MCP injection disabled.")

# ==========================================
# 1. GEOMETRIC ENGINE (Streaming Parser)
# ==========================================
class GCodeParser:
    def __init__(self):
        self.current_x, self.current_y, self.current_z = 0.0, 0.0, 0.0
        self.current_e, self.current_f = 0.0, 0.0
        self.is_absolute_extrusion = True  
        self.is_absolute_position = True   
        self.current_feature = "; FEATURE: Unknown"
        self.current_layer_moves: List[Dict] = []
        self.current_layer_z = None
        self.header_lines = []
        self.footer_lines = []
        self.in_body = False
        self.pending_metadata = [] 

    def parse_file_streaming(self, file_path: str) -> Generator[List[Dict], None, None]:
        """Yields one layer at a time to prevent RAM Out-Of-Memory (OOM) crashes on 100MB+ files."""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line_s = line.strip()
                if not self.in_body:
                    self.header_lines.append(line)
                    if ";===== print body =====" in line.lower() or "LAYER_CHANGE" in line:
                        self.in_body = True
                    continue
                
                if "; machine_end_gcode_start" in line.lower() or "; stop printing" in line.lower():
                    self.in_body = "FINISHED"
                
                if self.in_body == "FINISHED":
                    self.footer_lines.append(line)
                    continue

                layer_yielded = self._parse_line(line)
                if layer_yielded:
                    yield layer_yielded
                    
        if self.current_layer_moves:
            yield self.current_layer_moves

    def _parse_line(self, line: str):
        line_s = line.strip()
        if not line_s: return None

        if "; FEATURE:" in line_s.upper() or "; TYPE:" in line_s.upper() or "; WIPE_START" in line_s.upper():
            self.current_feature = line_s

        clean_line = line_s.split(';')[0].strip()
        if not clean_line:
            self.pending_metadata.append(line_s)
            return None

        tokens = re.findall(r'([A-Z])([-+]?\d*\.?\d+)', clean_line.upper())
        if not tokens:
            self.pending_metadata.append(line_s)
            return None

        cmd_let, cmd_num = tokens[0][0], int(float(tokens[0][1]))

        if cmd_let == 'G':
            if cmd_num == 90: self.is_absolute_position = True
            elif cmd_num == 91: self.is_absolute_position = False
            elif cmd_num in (0, 1, 2, 3): 
                return self._handle_movement(tokens, cmd_num)
        elif cmd_let == 'M':
            if cmd_num == 82: self.is_absolute_extrusion = True
            elif cmd_num == 83: self.is_absolute_extrusion = False
            
        self.pending_metadata.append(line_s)
        return None

    def _handle_movement(self, tokens: List[Tuple[str, str]], g_type: int):
        params = {l: float(v) for l, v in tokens[1:]}
        start_pt = (self.current_x, self.current_y, self.current_z)
        
        if self.is_absolute_position:
            new_x = params.get('X', self.current_x)
            new_y = params.get('Y', self.current_y)
            new_z = params.get('Z', self.current_z)
        else:
            new_x = self.current_x + params.get('X', 0.0)
            new_y = self.current_y + params.get('Y', 0.0)
            new_z = self.current_z + params.get('Z', 0.0)

        if 'F' in params: self.current_f = params['F']
        
        actual_e = (params['E'] - self.current_e) if (self.is_absolute_extrusion and 'E' in params) else params.get('E', 0.0)
        if 'E' in params: self.current_e = params['E'] if self.is_absolute_extrusion else 0.0

        layer_to_yield = None
        if new_z != self.current_z and new_z > 0:
            if self.current_layer_z is None: self.current_layer_z = new_z
            elif new_z > self.current_layer_z and len(self.current_layer_moves) > 0:
                layer_to_yield = list(self.current_layer_moves)
                self.current_layer_moves, self.current_layer_z = [], new_z

        dist = math.hypot(new_x - self.current_x, new_y - self.current_y)
        
        # CRITICAL FIX 1: Recognize G2/G3 Gear Arcs with I/J offsets as physical XY moves
        has_xy = ('X' in params or 'Y' in params or (g_type in (2, 3) and ('I' in params or 'J' in params)))
        is_extrusion_activity = abs(actual_e) > 0.00001
        
        if dist > 0.001 or abs(new_z - self.current_z) > 0.001 or is_extrusion_activity:
            self.current_layer_moves.append({
                'type': "extrude" if is_extrusion_activity and g_type in (0, 1, 2, 3) else "travel",
                'g_code': g_type,
                'start': start_pt, 'end': (new_x, new_y, new_z),
                'feedrate': self.current_f, 'e_val': actual_e,
                'has_xy': has_xy,
                'feature': self.current_feature,
                'i': params.get('I'), 'j': params.get('J'), 'r': params.get('R'), 'p': params.get('P'),
                'metadata': self.pending_metadata
            })
            self.pending_metadata = []
            
        self.current_x, self.current_y, self.current_z = new_x, new_y, new_z
        return layer_to_yield

# ==========================================
# 2. PATH OPTIMIZATION (Hybrid Cluster-Sweep)
# ==========================================
class PathOptimizer:
    @staticmethod
    def calculate_distance(pt1: Tuple[float, float, float], pt2: Tuple[float, float, float]) -> float:
        return math.hypot(pt2[0] - pt1[0], pt2[1] - pt1[1])

    @staticmethod
    def reverse_island(island: List[Dict]) -> List[Dict]:
        """Safely reverses printing direction while shielding Retractions/Wipes."""
        front_e_moves, back_e_moves, print_moves = [], [], []
        
        for move in island:
            if not move.get('has_xy', True):
                if not print_moves: front_e_moves.append(move)
                else: back_e_moves.append(move)
            else:
                print_moves.append(move)
                
        if not print_moves: return island
        
        reversed_print_moves = []
        for move in reversed(print_moves):
            new_move = move.copy()
            new_move['start'], new_move['end'] = move['end'], move['start']
            if 'metadata' in new_move: del new_move['metadata']
            if new_move.get('g_code') in (2, 3): return island 
            reversed_print_moves.append(new_move)
            
        new_island = front_e_moves + reversed_print_moves + back_e_moves
        if island[0].get('metadata'):
            new_island[0]['metadata'] = island[0]['metadata']
        return new_island

    @staticmethod
    def cluster_islands(islands: List[List[Dict]], threshold: float = 15.0) -> List[List[List[Dict]]]:
        clusters = []
        unassigned = list(islands)
        while unassigned:
            current_cluster = [unassigned.pop(0)]
            clusters.append(current_cluster)
            while True:
                added = False
                for i in range(len(unassigned) - 1, -1, -1):
                    for island in current_cluster:
                        if PathOptimizer.calculate_distance(unassigned[i][0]['start'], island[0]['start']) < threshold:
                            current_cluster.append(unassigned.pop(i))
                            added = True
                            break
                if not added: break
        return clusters

    @staticmethod
    def serpentine_sort(islands: List[List[Dict]], lane_width: float = 3.0) -> List[List[Dict]]:
        if not islands: return []
        lanes = {}
        for island in islands:
            lane_id = int(island[0]['start'][1] / lane_width)
            if lane_id not in lanes: lanes[lane_id] = []
            lanes[lane_id].append(island)
        
        optimized = []
        sweep_ltr = True
        for lid in sorted(lanes.keys()):
            lane_islands = sorted(lanes[lid], key=lambda i: i[0]['start'][0], reverse=not sweep_ltr)
            for item in lane_islands:
                feat = item[0].get('feature', '').lower()
                can_reverse = not ('wall' in feat or 'bridge' in feat)
                
                if can_reverse:
                    start_x, end_x = item[0]['start'][0], item[-1]['end'][0]
                    if (sweep_ltr and start_x > end_x) or (not sweep_ltr and start_x < end_x): 
                        item = PathOptimizer.reverse_island(item)
                optimized.append(item)
            sweep_ltr = not sweep_ltr
        return optimized

    @staticmethod
    def hybrid_sort(layer_moves: List[Dict], lane_width: float = 3.0, cluster_threshold: float = 15.0) -> List[List[Dict]]:
        islands, current, accumulated_meta = [], [], []
        for m in layer_moves:
            accumulated_meta.extend(m.get('metadata', []))
            if m['type'] == 'extrude':
                m['metadata'] = accumulated_meta
                accumulated_meta = []
                current.append(m)
            elif m['type'] == 'travel' and current:
                islands.append(current)
                current = []
        if current: islands.append(current)
        if not islands: return []

        feature_blocks, current_block, current_feat = [], [], None
        for island in islands:
            feat = island[0].get('feature', 'Unknown')
            if feat != current_feat:
                if current_block: feature_blocks.append(current_block)
                current_block, current_feat = [], feat
            current_block.append(island)
        if current_block: feature_blocks.append(current_block)

        optimized_sequence = []
        last_pos = (0.0, 0.0, 0.0)

        for block in feature_blocks:
            clusters = PathOptimizer.cluster_islands(block, cluster_threshold)
            sorted_clusters = []
            while clusters:
                best_idx, min_dist = 0, float('inf')
                for idx, cluster in enumerate(clusters):
                    dist = PathOptimizer.calculate_distance(last_pos, cluster[0][0]['start'])
                    if dist < min_dist:
                        min_dist, best_idx = dist, idx
                closest_cluster = clusters.pop(best_idx)
                sorted_clusters.append(closest_cluster)
                last_pos = closest_cluster[-1][-1]['end']

            for cluster in sorted_clusters:
                sorted_islands = PathOptimizer.serpentine_sort(cluster, lane_width)
                optimized_sequence.extend(sorted_islands)
                last_pos = sorted_islands[-1][-1]['end']

        return optimized_sequence

# ==========================================
# 3. PRODUCTION INTEGRATION
# ==========================================
def _detect_filament_type(header_text: str) -> str:
    match = re.search(r';\s*filament_type\s*[:=]\s*([A-Za-z0-9\-\,\_ ]+)', header_text, re.IGNORECASE)
    return match.group(1).upper() if match else "UNKNOWN"

def auto_optimize_gcode(input_path: str, is_heavy_toolhead: bool = True):
    if not os.path.exists(input_path): 
        print(f"Error: File not found: {input_path}")
        return False
    
    parser = GCodeParser()
    print("[*] Initiating Generator Parsing (OOM-Safe Streaming)...")
    
    # Peek at header for configs without parsing entire file
    with open(input_path, 'r', encoding='utf-8', errors='ignore') as f:
        header_text = ""
        for line in f:
            header_text += line
            if ";===== print body =====" in line.lower() or "LAYER_CHANGE" in line:
                break
                
    filament_type = _detect_filament_type(header_text)
    
    # --- ACTIVE THERMAL EQUALIZER CALIBRATION ---
    base_temp = None
    temp_match = re.search(r';\s*nozzle_temperature\s*=\s*(\d+)', header_text)
    if temp_match:
        base_temp = int(temp_match.group(1))
    else:
        # Fallback: scan for first M104 or M109 command in the header
        m104_match = re.search(r'M10[49]\s+S(\d+)', header_text)
        if m104_match:
            base_temp = int(m104_match.group(1))

    if base_temp:
        print(f"[*] Active Thermal Equalizer Ready: Baseline = {base_temp}°C")

    # --- DYNAMIC MATERIAL MATRIX ---
    is_soft = any(kw in filament_type for kw in ["TPU", "TPE", "FLEX"])
    is_petg = "PETG" in filament_type
    is_cf_gf = any(kw in filament_type for kw in ["CF", "GF", "CARBON", "GLASS"])
    is_abs_asa = any(kw in filament_type for kw in ["ABS", "ASA"])
    is_nylon = "PA" in filament_type
    
    mat_profile = {"z_hop": 0.0, "speed_multiplier": 1.0, "max_travel_speed": 30000, "needs_petg_wipe": is_petg}
    if is_soft:
        mat_profile.update({"z_hop": 0.4, "speed_multiplier": 0.5, "max_travel_speed": 12000})
    elif is_cf_gf:
        mat_profile.update({"z_hop": 0.2, "speed_multiplier": 0.85, "max_travel_speed": 18000})
    elif is_petg or is_nylon:
        mat_profile.update({"z_hop": 0.0, "speed_multiplier": 0.9, "max_travel_speed": 24000})
    elif is_abs_asa:
        mat_profile.update({"z_hop": 0.2, "speed_multiplier": 0.95, "max_travel_speed": 25000})
        
    print(f"[*] Material Profile [{filament_type}]: Flow Scale={mat_profile['speed_multiplier']}x")

    # --- DYNAMIC HARDWARE SAFETY BOUNDS ---
    bed_max_x, bed_max_y, bed_max_z = 256.0, 256.0, 256.0
    area_match = re.search(r';\s*printable_area\s*=\s*(.*)', header_text, re.IGNORECASE)
    if area_match:
        coords = [float(c) for c in re.findall(r'[-+]?\d*\.\d+|\d+', area_match.group(1))]
        if len(coords) >= 2:
            bed_max_x, bed_max_y = max(coords[::2]), max(coords[1::2])
            
    height_match = re.search(r';\s*printable_height\s*=\s*([-+]?\d*\.\d+|\d+)', header_text, re.IGNORECASE)
    if height_match: bed_max_z = float(height_match.group(1))

    HW_MIN_X, HW_MAX_X = -5.0, bed_max_x + 5.0
    HW_MIN_Y, HW_MAX_Y = -5.0, bed_max_y + 10.0
    HW_MAX_Z = bed_max_z + 2.0
    hw_violations = 0

    backup_path = input_path + ".bak"
    if not os.path.exists(backup_path): shutil.copy2(input_path, backup_path)
    output_path = input_path 

    print("[*] Applying Hybrid Cluster-Sweep & Thermodynamics...")
    
    stats = {"islands": 0}
    last_x, last_y, current_z, active_feat = 0.0, 0.0, None, None
    current_accel_state = None
    current_eq_state = "NORMAL"

    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        layer_generator = parser.parse_file_streaming(backup_path)
        first_layer = next(layer_generator, None)
        f.write("".join(parser.header_lines))
        
        def process_layer(layer):
            nonlocal stats, last_x, last_y, current_z, active_feat, hw_violations, current_accel_state, current_eq_state
            if not layer: return
            optimized_islands = PathOptimizer.hybrid_sort(layer)
            stats["islands"] += len(optimized_islands)
            
            for idx, island in enumerate(optimized_islands):
                start_x, start_y, start_z = island[0]['start']
                jump_dist = math.hypot(start_x - last_x, start_y - last_y)
                is_micro_glide = (idx > 0) and (jump_dist < 2.0) and (abs(start_z - (current_z or start_z)) < 0.001)

                if current_z is None or abs(start_z - current_z) > 0.001:
                    f.write(f"G1 Z{start_z:.3f} F1200 ; AI Layer Update\n")
                    current_z = start_z

                # --- NEW: FIRST LAYER ADHESION LOCK ---
                # Detect if we are on the delicate first layer (typically Z <= 0.4mm)
                is_first_layer = start_z <= 0.4

                # Material Z-Hops
                if not is_micro_glide and mat_profile["z_hop"] > 0:
                    f.write(f"G91\nG1 Z{mat_profile['z_hop']} F600\nG90\n")
                    f.write(f"G1 X{start_x:.3f} Y{start_y:.3f} F{mat_profile['max_travel_speed']}\n")
                    f.write(f"G91\nG1 Z-{mat_profile['z_hop']} F600\nG90\n")
                elif jump_dist > 0.001:
                    f.write(f"G1 X{start_x:.3f} Y{start_y:.3f} F{mat_profile['max_travel_speed']}\n")

                # ========================================================
                # NEW FEATURE: ACTIVE THERMAL EQUALIZER
                # ========================================================
                island_extrude_dist = sum(math.hypot(m['end'][0]-m['start'][0], m['end'][1]-m['start'][1]) for m in island if m['type'] == 'extrude')
                
                if base_temp and island_extrude_dist > 0:
                    if is_first_layer:
                        target_state = "BED_ADHESION"
                        target_temp = base_temp  # Lock to base temp to ensure bed squish!
                    elif island_extrude_dist < 15.0:
                        target_state = "COOLING"
                        target_temp = base_temp - 5  # Prevent details from melting
                    elif island_extrude_dist > 150.0:
                        target_state = "BONDING"
                        target_temp = base_temp + 5  # Super-heat long lines for structural shear strength
                    else:
                        target_state = "NORMAL"
                        target_temp = base_temp
                        
                    if current_eq_state != target_state:
                        f.write(f"\n; --- AI Thermal Equalizer: {target_state} MODE ---\n")
                        f.write(f"M104 S{target_temp} ; Dynamic Temp Shift (Non-Blocking)\n")
                        current_eq_state = target_state
                # ========================================================

                for move in island:
                    for meta_line in move.get('metadata', []): f.write(f"{meta_line}\n")
                    feat = move.get('feature')
                    if feat != active_feat:
                        f.write(f"{feat}\n")
                        active_feat = feat
                        
                        # --- HEAVY TOOLHEAD INERTIA DAMPENING ---
                        if is_heavy_toolhead and feat:
                            feat_lower = feat.lower()
                            
                            if is_first_layer:
                                new_accel = 1000 # Ultra-smooth, locked acceleration for first layer
                            else:
                                new_accel = 3000
                                if 'gap' in feat_lower or 'small' in feat_lower or 'internal' in feat_lower:
                                    new_accel = 1500 # Hard brake for tight geometries
                                elif 'outer' in feat_lower or 'travel' in move['type']:
                                    new_accel = 5000 # Accelerate on smooth straights
                            
                            if new_accel != current_accel_state:
                                f.write(f"M204 S{new_accel} ; AI Inertia Dampening\n")
                                current_accel_state = new_accel

                    ex, ey, ez = move['end']
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

        # Process layers dynamically as they stream in
        if first_layer: process_layer(first_layer)
        for layer in layer_generator: process_layer(layer)

        if mat_profile["needs_petg_wipe"]:
            f.write("\n; --- AI: IDEX-SAFE WIPE ---\nM106 S255\nG91\nG1 E-2.0 F3600\nG1 Z2.0 F6000\nG90\nM400\nM106 S0\n")
        
        f.write("".join(parser.footer_lines))

    print(f"[*] Optimization Success: {stats['islands']} islands sorted dynamically.")
    if hw_violations > 0: print(f"[!] SAFETY WARNING: {hw_violations} moves forcefully clamped to prevent crashes.")
    
    if MCP_AVAILABLE:
        print("\n=== INITIATING MCP HARDWARE TRIGGERS ===")
        try:
            print(f"MCP Action: {gcode_mcp_server.inject_m1004_at_feature(output_path, 'bridge', 4)}")
        except Exception as e: print(f"MCP Injection Error: {str(e)}")

    if HAS_AUDITOR:
        print("\n=== INITIATING AUTOMATED CLOSED-LOOP AUDIT ===")
        GCodeCollisionChecker().run_check(output_path)
        
    return True

if __name__ == "__main__":
    parser_cli = argparse.ArgumentParser(description="H2C Geometric AI G-Code Optimizer (v3.1)")
    parser_cli.add_argument("input", help="Path to the raw .gcode file")
    parser_cli.add_argument("--disable-heavy", action="store_true", help="Disable the H2C Heavy Toolhead Inertia Dampening")
    args = parser_cli.parse_args()

    auto_optimize_gcode(args.input, is_heavy_toolhead=not args.disable_heavy)
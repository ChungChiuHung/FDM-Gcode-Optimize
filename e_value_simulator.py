import math
from typing import List, Dict

class EValueSimulator:
    def __init__(self):
        self.retract_length = 0.8
        self.retract_speed = 1800
        self.deretract_speed = 1800
        self.travel_speed = 12000
        self.z_hop_height = 0.4 
        self.z_hop_speed = 600
        self.micro_glide_threshold = 2.0 
        
        self.is_retracted = False
        self.current_z = None
        self.last_x, self.last_y = 0.0, 0.0
        self.active_feature = None

    def simulate_and_rebuild(self, optimized_sequence: List[List[Dict]]) -> str:
        rebuilt_gcode = ""
        islands_processed = 0
        
        for island in optimized_sequence:
            start_x, start_y, start_z = island[0]['start']
            jump_dist = math.sqrt((start_x - self.last_x)**2 + (start_y - self.last_y)**2)
            
            is_micro_glide = (islands_processed > 0) and (jump_dist < self.micro_glide_threshold) and (abs(start_z - (self.current_z or start_z)) < 0.001)
            islands_processed += 1

            if not is_micro_glide:
                rebuilt_gcode += f"\n; --- AI Jump (Dist: {jump_dist:.1f}mm) ---\n"
                if not self.is_retracted:
                    rebuilt_gcode += f"G1 E-{self.retract_length} F{self.retract_speed}\n"
                    self.is_retracted = True
            
            if self.current_z is None or abs(start_z - self.current_z) > 0.001:
                rebuilt_gcode += f"G1 Z{start_z:.3f} F1200\n"
                self.current_z = start_z
                
            if self.is_retracted and not is_micro_glide:
                rebuilt_gcode += f"G91\nG1 Z{self.z_hop_height} F600\nG90\n"
                rebuilt_gcode += f"G1 X{start_x:.3f} Y{start_y:.3f} F{self.travel_speed}\n"
                rebuilt_gcode += f"G91\nG1 Z-{self.z_hop_height} F600\nG90\n"
                rebuilt_gcode += f"G1 E{self.retract_length} F{self.deretract_speed}\n"
                self.is_retracted = False
            elif not is_micro_glide:
                rebuilt_gcode += f"G1 X{start_x:.3f} Y{start_y:.3f} F{self.travel_speed}\n"

            for move in island:
                # 1. Restore the rescued metadata (Fans, Accel limits)
                for meta_line in move.get('metadata', []):
                    rebuilt_gcode += f"{meta_line}\n"

                # 2. Restore Slicer Feature Colors
                feat = move.get('feature')
                if feat != self.active_feature:
                    rebuilt_gcode += f"{feat}\n"
                    self.active_feature = feat

                ex, ey, ez = move['end']
                f_val = move.get('feedrate', 3000)
                e_val = move.get('e_val', 0)
                g_cmd = move.get('g_code', 1) 
                
                z_str = ""
                if abs(ez - self.current_z) > 0.001 and abs(ez - self.current_z) < 1.0:
                    z_str = f" Z{ez:.3f}"
                    self.current_z = ez
                    
                arc_str = ""
                if g_cmd in (2, 3):
                    if move.get('i') is not None: arc_str += f" I{move['i']:.3f}"
                    if move.get('j') is not None: arc_str += f" J{move['j']:.3f}"
                    if move.get('r') is not None: arc_str += f" R{move['r']:.3f}"

                if e_val > 0:
                    rebuilt_gcode += f"G{g_cmd} X{ex:.3f} Y{ey:.3f}{z_str}{arc_str} E{e_val:.5f} F{int(f_val)}\n"
                else:
                    rebuilt_gcode += f"G{g_cmd} X{ex:.3f} Y{ey:.3f}{z_str}{arc_str} F{int(f_val)}\n"
                    
                self.last_x, self.last_y = ex, ey
                
        return rebuilt_gcode
import os
import re
import math
from typing import List, Tuple, Dict

class GCodeCollisionChecker:
    """
    A mathematical sandbox that tracks extruded plastic and 
    checks if travel moves intersect (collide) with printed walls.
    """
    def __init__(self):
        self.current_x = 0.0
        self.current_y = 0.0
        self.current_z = 0.0
        self.current_e = 0.0
        self.is_absolute_e = True
        
        self.layer_z = 0.0
        self.current_feature = "Unknown"
        self.extruded_lines: List[Dict] = []
        self.travel_moves: List[Dict] = []
        
        self.layer_count = 0
        self.total_collisions = 0
        self.micro_glide_tolerance = 2.0 # mm (Safe distance to drag nozzle)

    @staticmethod
    def ccw(A, B, C):
        """Cross product to determine orientation of 3 points"""
        return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])

    @staticmethod
    def segments_intersect(A, B, C, D):
        """Returns True if line segment AB mathematically intersects line CD"""
        
        # --- CRITICAL FIX: The Endpoint Touching Filter ---
        # If the travel move (A->B) connects directly to the wall's start or end point (C or D), 
        # it is "landing" or "wiping", not crashing. We grant a 0.05mm floating-point leniency.
        if (math.hypot(A[0]-C[0], A[1]-C[1]) < 0.05 or 
            math.hypot(A[0]-D[0], A[1]-D[1]) < 0.05 or 
            math.hypot(B[0]-C[0], B[1]-C[1]) < 0.05 or 
            math.hypot(B[0]-D[0], B[1]-D[1]) < 0.05):
            return False

        # Quick Bounding Box Test (Performance)
        if not (min(A[0], B[0]) <= max(C[0], D[0]) and max(A[0], B[0]) >= min(C[0], D[0]) and
                min(A[1], B[1]) <= max(C[1], D[1]) and max(A[1], B[1]) >= min(C[1], D[1])):
            return False
            
        # Exact Mathematical Intersection
        return GCodeCollisionChecker.ccw(A,C,D) != GCodeCollisionChecker.ccw(B,C,D) and \
               GCodeCollisionChecker.ccw(A,B,C) != GCodeCollisionChecker.ccw(A,B,D)

    def analyze_layer(self):
        """Analyzes the current layer for severe surface collisions."""
        if not self.travel_moves or not self.extruded_lines:
            return
            
        # Ignore startup sequences and purge lines below 0.1mm
        if self.layer_z < 0.1:
            return

        layer_collisions = 0
        for travel in self.travel_moves:
            travel_start = travel['start']
            travel_end = travel['end']
            travel_z = travel['z']
            
            # Skip if successfully Z-Hopped
            if travel_z > self.layer_z + 0.01:
                continue

            # Skip safe short micro-glides
            dist = math.hypot(travel_end[0] - travel_start[0], travel_end[1] - travel_start[1])
            if dist <= self.micro_glide_tolerance:
                continue

            for wall in self.extruded_lines:
                if self.segments_intersect(travel_start, travel_end, wall['segment'][0], wall['segment'][1]):
                    feat = wall['feature'].lower()
                    
                    # We only throw a Fatal Collision if it cuts through the Outside Wall or Top Surface!
                    if "outer wall" in feat or "top surface" in feat:
                        layer_collisions += 1
                        self.total_collisions += 1
                        print(f"  [!] FATAL COLLISION on Layer {self.layer_count} (Z={self.layer_z}):")
                        print(f"      Travel move cut directly through: {wall['feature']}!")
                        break

        if layer_collisions > 0:
            print(f"  -> Layer {self.layer_count} failed with {layer_collisions} fatal exterior collisions.\n")

    def run_check(self, file_path: str):
        if not os.path.exists(file_path):
            return

        print(f"\n==================================================")
        print(f"   STARTING COLLISION PHYSICS CHECK")
        print(f"   Target: {os.path.basename(file_path)}")
        print(f"==================================================")

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line_num, line in enumerate(f, 1):
                line_s = line.strip()
                
                # Capture Slicer Feature Identity
                feat_match = re.search(r';\s*(?:FEATURE|TYPE)\s*:\s*(.*)', line_s, re.IGNORECASE)
                if feat_match:
                    self.current_feature = feat_match.group(1).strip()
                    continue

                clean_line = line_s.split(';')[0].strip().upper()
                if not clean_line: continue

                if clean_line.startswith("M82"): self.is_absolute_e = True
                if clean_line.startswith("M83"): self.is_absolute_e = False

                tokens = re.findall(r'([A-Z])([-+]?\d*\.?\d+)', clean_line)
                if not tokens: continue

                cmd_let = tokens[0][0]
                cmd_num = int(float(tokens[0][1]))

                if cmd_let == 'G' and cmd_num == 92:
                    params = {k: float(v) for k, v in tokens[1:]}
                    if 'E' in params:
                        self.current_e = params['E']
                        
                elif cmd_let == 'G' and cmd_num in (0, 1, 2, 3):
                    params = {k: float(v) for k, v in tokens[1:]}
                    
                    new_x = params.get('X', self.current_x)
                    new_y = params.get('Y', self.current_y)
                    new_z = params.get('Z', self.current_z)
                    
                    # Calculate true extrusion amount
                    actual_e = 0.0
                    if 'E' in params:
                        if self.is_absolute_e:
                            actual_e = params['E'] - self.current_e
                        else:
                            actual_e = params['E']
                        self.current_e = params['E'] if self.is_absolute_e else 0.0

                    # Layer Change Logic
                    if new_z != self.current_z and new_z > self.layer_z + 0.05 and actual_e > 0:
                        self.analyze_layer() 
                        self.extruded_lines = []
                        self.travel_moves = []
                        self.layer_z = new_z
                        self.layer_count += 1

                    dist = math.hypot(new_x - self.current_x, new_y - self.current_y)
                    
                    if dist > 0.001:
                        if actual_e > 0:
                            self.extruded_lines.append({
                                'segment': ((self.current_x, self.current_y), (new_x, new_y)),
                                'feature': self.current_feature
                            })
                        else:
                            self.travel_moves.append({
                                'start': (self.current_x, self.current_y),
                                'end': (new_x, new_y),
                                'z': new_z
                            })

                    self.current_x, self.current_y, self.current_z = new_x, new_y, new_z

        self.analyze_layer()

        print(f"==================================================")
        if self.total_collisions == 0:
            print(f"   [PASSED] PERFECT PATHING! No exterior collisions.")
        else:
            print(f"   [FAILED] {self.total_collisions} fatal collisions found!")
        print(f"==================================================\n")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        checker = GCodeCollisionChecker()
        checker.run_check(sys.argv[1])
    else:
        print("Usage: python gcode_collision_checker.py <path_to_gcode>")
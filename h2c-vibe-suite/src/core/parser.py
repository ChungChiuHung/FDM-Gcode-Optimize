import os
import re
import math
from typing import Generator, List, Dict, Tuple

class GCodeParser:
    # Pre-compiled once at class level — avoids repeated regex compilation
    # on every parsed line, which dominates CPU time for >50 MB G-code files.
    TOKEN_PATTERN = re.compile(r'([A-Z])([-+]?\d*\.?\d+)')

    # OOM protection: cap header accumulation on malformed files that never
    # emit a body-start marker (EXECUTABLE_BLOCK_START / LAYER_CHANGE).
    MAX_HEADER_LINES = 5000
    # OOM protection: cap footer accumulation for files with missing/late
    # EXECUTABLE_BLOCK_END markers that would otherwise buffer the whole body.
    MAX_FOOTER_LINES = 2000

    def __init__(self):
        self.current_x, self.current_y, self.current_z = 0.0, 0.0, 0.0
        self.current_e, self.current_f = 0.0, 0.0
        self.is_absolute_extrusion = False  
        self.is_absolute_position = True   
        self.current_feature = "; FEATURE: Unknown"
        
        self.current_layer_moves: List[Dict] = []
        self.current_layer_z: float | None = None
        self._pending_z: float | None = None  # deferred layer-change candidate (Z-hop shielding)

        self.header_lines = []
        self.footer_lines = []
        self.in_body = False
        self.in_config = False 
        self.pending_metadata = []

    def parse_streaming(self, file_path: str) -> Generator[List[Dict], None, None]:
        if not os.path.exists(file_path):
            return

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line_s = line.strip()
                if not self.in_body:
                    if len(self.header_lines) < self.MAX_HEADER_LINES:
                        self.header_lines.append(line)
                    line_upper = line.upper()
                    
                    if "M82" in line_upper:
                        self.is_absolute_extrusion = True
                    elif "M83" in line_upper:
                        self.is_absolute_extrusion = False
                    
                    if "; CONFIG_BLOCK_START" in line_upper:
                        self.in_config = True
                    elif "; CONFIG_BLOCK_END" in line_upper:
                        self.in_config = False
                        
                    if not self.in_config:
                        if ("EXECUTABLE_BLOCK_START" in line_upper or
                            ";===== PRINT BODY =====" in line_upper or
                            line_upper.startswith("; LAYER_CHANGE") or
                            line_upper.startswith(";LAYER_CHANGE")):
                            self.in_body = True
                    continue
                
                if ("machine_end_gcode_start" in line.lower() or
                    "; stop printing" in line.lower() or
                    "EXECUTABLE_BLOCK_END" in line.upper()):
                    self.in_body = "FINISHED"
                
                if self.in_body == "FINISHED":
                    # [!] CRITICAL FIX: 在結尾區塊前，強制輸出所有被困在記憶體中的 Metadata (如 M104 S0)
                    if self.pending_metadata:
                        self.footer_lines.extend([m + '\n' for m in self.pending_metadata])
                        self.pending_metadata = []
                    if len(self.footer_lines) < self.MAX_FOOTER_LINES:
                        self.footer_lines.append(line)
                    continue

                if self._parse_line(line):
                    yield self.current_layer_moves
                    self.current_layer_moves = []
        
        if self.current_layer_moves:
            yield self.current_layer_moves

    def _parse_line(self, line: str) -> bool:
        line_s = line.strip()
        if not line_s: return False

        # QUALITY FIX: Do NOT let ; WIPE_START overwrite the physical feature tag.
        # This preserves "Outer wall" metadata during seam wipes for kinematics.py.
        if "; FEATURE:" in line_s.upper() or "; TYPE:" in line_s.upper():
            self.current_feature = line_s

        clean_line = line_s.split(';')[0].strip()
        if not clean_line:
            self.pending_metadata.append(line_s)
            return False

        tokens = self.TOKEN_PATTERN.findall(clean_line.upper())
        if not tokens:
            self.pending_metadata.append(line_s)
            return False

        cmd_let, cmd_num = tokens[0][0], int(float(tokens[0][1]))

        if cmd_let == 'G':
            if cmd_num == 90: self.is_absolute_position = True
            elif cmd_num == 91: self.is_absolute_position = False
            elif cmd_num == 92:
                g92_params = {let: float(val) for let, val in tokens[1:]}
                if 'X' in g92_params: self.current_x = g92_params['X']
                if 'Y' in g92_params: self.current_y = g92_params['Y']
                if 'Z' in g92_params: self.current_z = g92_params['Z']
                if 'E' in g92_params: self.current_e = g92_params['E']
            elif cmd_num in (0, 1, 2, 3): 
                # [!] Pass line_s down so _handle_movement can save the raw string
                return self._handle_movement(tokens, cmd_num, line_s)
        elif cmd_let == 'M':
            if cmd_num == 82: self.is_absolute_extrusion = True
            elif cmd_num == 83: self.is_absolute_extrusion = False
            
        self.pending_metadata.append(line_s)
        return False

    def _handle_movement(self, tokens: List[Tuple[str, str]], g_type: int, line_s: str) -> bool:
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
        
        if 'E' in params:
            self.current_e = params['E'] if self.is_absolute_extrusion else self.current_e + params['E']

        layer_yielded = False
        if new_z != self.current_z and new_z > 0:
            if self.current_layer_z is None:
                self.current_layer_z = new_z
            elif new_z >= self.current_layer_z + 0.05:
                if len(self.current_layer_moves) > 0:
                    self._pending_z = new_z
            elif new_z < self.current_layer_z - 2.0 and len(self.current_layer_moves) > 0:
                layer_yielded = True
                self._pending_z = None
                self.current_layer_z = new_z
            elif new_z <= self.current_layer_z + 0.001:
                self._pending_z = None

        has_xy = ('X' in params or 'Y' in params or (g_type in (2, 3) and ('I' in params or 'J' in params)))
        
        # QUALITY FIX: Separate Positive Extrusions and Retractions
        is_extrude = actual_e > 0.00001
        is_retract = actual_e < -0.00001

        if is_extrude and self._pending_z is not None and len(self.current_layer_moves) > 0:
            layer_yielded = True
            self.current_layer_z = self._pending_z
            self._pending_z = None
            
        if is_extrude and g_type in (0, 1, 2, 3):
            move_type = "extrude"
        elif is_retract and g_type in (0, 1, 2, 3):
            move_type = "retract"
        else:
            move_type = "travel"
        
        # [!] CRITICAL FIX: 移除距離過濾器，確保所有指令(包含 G1 F30000) 皆完整保留
        self.current_layer_moves.append({
            'type': move_type,
            'g_code': g_type,
            'start': start_pt, 'end': (new_x, new_y, new_z),
            'feedrate': self.current_f, 'e_val': actual_e,
            'raw_e': params.get('E'),
            'has_xy': has_xy,
            'feature': self.current_feature,
            'i': params.get('I'), 'j': params.get('J'), 'r': params.get('R'), 'p': params.get('P'),
            'metadata': self.pending_metadata,
            'raw_line': line_s
        })
        self.pending_metadata = []
            
        self.current_x, self.current_y, self.current_z = new_x, new_y, new_z
        return layer_yielded
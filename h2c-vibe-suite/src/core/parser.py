import os
import re
import math
from typing import Generator, List, Dict, Tuple

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
        self.in_config = False # 新增狀態追蹤
        self.pending_metadata = []

    def parse_streaming(self, file_path: str) -> Generator[List[Dict], None, None]:
        if not os.path.exists(file_path):
            return

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line_s = line.strip()
                if not self.in_body:
                    self.header_lines.append(line)
                    line_upper = line.upper()
                    
                    # 追蹤是否進入了設定區塊，防止在區塊內提早中斷
                    if "; CONFIG_BLOCK_START" in line_upper:
                        self.in_config = True
                    elif "; CONFIG_BLOCK_END" in line_upper:
                        self.in_config = False
                        
                    # 只有在設定區塊外部，且確實是列印主體開頭時，才切換狀態
                    if not self.in_config:
                        if ";===== PRINT BODY =====" in line_upper or line_upper.startswith("; LAYER_CHANGE") or line_upper.startswith(";LAYER_CHANGE"):
                            self.in_body = True
                    continue
                
                if "; machine_end_gcode_start" in line.lower() or "; stop printing" in line.lower():
                    self.in_body = "FINISHED"
                
                if self.in_body == "FINISHED":
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

        if "; FEATURE:" in line_s.upper() or "; TYPE:" in line_s.upper() or "; WIPE_START" in line_s.upper():
            self.current_feature = line_s

        clean_line = line_s.split(';')[0].strip()
        if not clean_line:
            self.pending_metadata.append(line_s)
            return False

        tokens = re.findall(r'([A-Z])([-+]?\d*\.?\d+)', clean_line.upper())
        if not tokens:
            self.pending_metadata.append(line_s)
            return False

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
        return False

    def _handle_movement(self, tokens: List[Tuple[str, str]], g_type: int) -> bool:
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

        layer_yielded = False
        if new_z != self.current_z and new_z > 0:
            if self.current_layer_z is None: self.current_layer_z = new_z
            elif new_z > self.current_layer_z and len(self.current_layer_moves) > 0:
                layer_yielded = True
                self.current_layer_z = new_z

        dist = math.hypot(new_x - self.current_x, new_y - self.current_y)
        
        # --- CRITICAL ARC GEAR FIX ---
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
        return layer_yielded
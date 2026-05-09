import os
import re
import math
from typing import List, Tuple, Dict

# 企業級型別別名 (Type Aliases) 提升可讀性
Point2D = Tuple[float, float]

class GCodeCollisionChecker:
    """
    碰撞沙盒 (Collision Sandbox): 
    透過數學運算追蹤已擠出的塑料牆面，並檢查空跑 (Travel Moves) 是否與之發生致命的物理碰撞。
    支援 Bambu Studio, Orca Slicer, PrusaSlicer 等主流切片軟體的特徵解析。
    """
    
    # --- 沙盒物理容差設定 (類別常數) ---
    ENDPOINT_TOLERANCE = 0.05   # mm: 端點接觸容差 (區分「降落/擦拭」與「撞擊」)
    ZHOP_TOLERANCE = 0.01       # mm: 安全抬刀的判定高度差
    MIN_LAYER_HEIGHT = 0.1      # mm: 低於此高度的初始吐料與定位線將不進行碰撞檢查
    MICRO_GLIDE_TOLERANCE = 2.0 # mm: 允許噴嘴直接滑過的微距空跑安全值
    
    def __init__(self):
        self.current_x = 0.0
        self.current_y = 0.0
        self.current_z = 0.0
        self.current_e = 0.0
        
        self.is_absolute_e = True
        self.is_absolute_pos = True  
        
        self.layer_z = 0.0
        self.current_feature = "Unknown"
        
        self.extruded_lines: List[Dict] = []
        self.travel_moves: List[Dict] = []
        
        self.layer_count = 0
        self.total_collisions = 0

    @staticmethod
    def ccw(A: Point2D, B: Point2D, C: Point2D) -> bool:
        """利用向量外積 (Cross Product) 判斷三點的旋轉方向"""
        return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])

    @staticmethod
    def segments_intersect(A: Point2D, B: Point2D, C: Point2D, D: Point2D) -> bool:
        """數學判定：傳回 True 表示線段 AB 與線段 CD 發生實體交會 (Intersect)"""
        
        # --- 端點接觸過濾器 (Endpoint Touching Filter) ---
        # 如果空跑的起點或終點直接連接在牆面的端點上，在物理上這是「降落準備列印」或是「接縫擦拭」，而非撞擊。
        if (math.hypot(A[0]-C[0], A[1]-C[1]) < GCodeCollisionChecker.ENDPOINT_TOLERANCE or 
            math.hypot(A[0]-D[0], A[1]-D[1]) < GCodeCollisionChecker.ENDPOINT_TOLERANCE or 
            math.hypot(B[0]-C[0], B[1]-C[1]) < GCodeCollisionChecker.ENDPOINT_TOLERANCE or 
            math.hypot(B[0]-D[0], B[1]-D[1]) < GCodeCollisionChecker.ENDPOINT_TOLERANCE):
            return False

        # 效能優化：快速邊界盒測試 (Bounding Box Test)，若區域不重疊則直接排除
        if not (min(A[0], B[0]) <= max(C[0], D[0]) and max(A[0], B[0]) >= min(C[0], D[0]) and
                min(A[1], B[1]) <= max(C[1], D[1]) and max(A[1], B[1]) >= min(C[1], D[1])):
            return False
            
        # 精確數學交會判定
        return GCodeCollisionChecker.ccw(A, C, D) != GCodeCollisionChecker.ccw(B, C, D) and \
               GCodeCollisionChecker.ccw(A, B, C) != GCodeCollisionChecker.ccw(A, B, D)

    def analyze_layer(self):
        """分析當前層級，尋找可能破壞表面的致命碰撞"""
        if not self.travel_moves or not self.extruded_lines:
            return
            
        # 忽略機台啟動序列與極低層的廢料線
        if self.layer_z < self.MIN_LAYER_HEIGHT:
            return

        layer_collisions = 0
        # CRITICAL FIX 1: 定義容易被撞毀的關鍵外觀特徵 (跨所有主流 Slicer 兼容)
        critical_features = ["outer wall", "top surface", "external perimeter", "wall-outer"]

        for travel in self.travel_moves:
            travel_start = travel['start']
            travel_end = travel['end']
            travel_z = travel['z']
            
            # 安全防護：若 Z 軸已成功抬升超過安全容差，則該次空跑絕對安全
            if travel_z > self.layer_z + self.ZHOP_TOLERANCE:
                continue

            # 安全防護：略過短距離微距滑行
            dist = math.hypot(travel_end[0] - travel_start[0], travel_end[1] - travel_start[1])
            if dist <= self.MICRO_GLIDE_TOLERANCE:
                continue

            for wall in self.extruded_lines:
                if self.segments_intersect(travel_start, travel_end, wall['segment'][0], wall['segment'][1]):
                    feat = wall['feature'].lower()
                    
                    # 只有當空跑路徑直接切穿「外牆」或「頂部表面」時，才判定為致命碰撞！
                    if any(crit in feat for crit in critical_features):
                        layer_collisions += 1
                        self.total_collisions += 1
                        print(f"  [!] FATAL COLLISION on Layer {self.layer_count} (Z={self.layer_z:.3f}):")
                        print(f"      Travel move cut directly through: {wall['feature']}!")
                        break

        if layer_collisions > 0:
            print(f"  -> Layer {self.layer_count} failed with {layer_collisions} fatal exterior collisions.\n")

    def run_check(self, file_path: str):
        """執行沙盒模擬分析"""
        if not os.path.exists(file_path):
            print(f"  [!] Sandbox Error: File {file_path} not found.")
            return

        print(f"\n==================================================")
        print(f"   STARTING COLLISION PHYSICS CHECK")
        print(f"   Target: {os.path.basename(file_path)}")
        print(f"==================================================")

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line_num, line in enumerate(f, 1):
                    line_s = line.strip()
                    
                    # CRITICAL FIX 2: 擷取切片軟體特徵標記 (更寬鬆正則，兼容帶冒號與無冒號的格式)
                    feat_match = re.search(r';\s*(?:FEATURE|TYPE)\s*[:=]?\s*(.*)', line_s, re.IGNORECASE)
                    if feat_match:
                        self.current_feature = feat_match.group(1).strip()
                        continue

                    clean_line = line_s.split(';')[0].strip().upper()
                    if not clean_line: continue

                    # 讓沙盒聽得懂 G90(絕對) 與 G91(相對) 座標切換指令
                    if clean_line.startswith("G90"): self.is_absolute_pos = True
                    if clean_line.startswith("G91"): self.is_absolute_pos = False
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
                        
                        # 根據座標模式正確計算 Z-Hop 抬刀後的真實高度
                        if self.is_absolute_pos:
                            new_x = params.get('X', self.current_x)
                            new_y = params.get('Y', self.current_y)
                            new_z = params.get('Z', self.current_z)
                        else:
                            new_x = self.current_x + params.get('X', 0.0)
                            new_y = self.current_y + params.get('Y', 0.0)
                            new_z = self.current_z + params.get('Z', 0.0)
                        
                        # 計算真實擠出量
                        actual_e = 0.0
                        if 'E' in params:
                            if self.is_absolute_e:
                                actual_e = params['E'] - self.current_e
                            else:
                                actual_e = params['E']
                            self.current_e = params['E'] if self.is_absolute_e else 0.0

                        # 層級變更邏輯 (Layer Change)
                        if new_z != self.current_z and new_z > self.layer_z + 0.05 and actual_e > 0:
                            self.analyze_layer() 
                            # CRITICAL FIX 3: 使用 .clear() 提升記憶體重用效率
                            self.extruded_lines.clear()
                            self.travel_moves.clear()
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

            # 檔案結尾：確保最後一層被分析到
            self.analyze_layer()
            
        except Exception as e:
            print(f"  [!] Sandbox Crash: Error parsing G-code during collision check: {e}")
            return

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
        print("Usage: python src/safety/collision_sandbox.py <path_to_gcode>")
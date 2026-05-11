import os
import re
import math
from typing import List, Tuple, Dict

# 型別別名，提升維護性
Point2D = Tuple[float, float]

class GCodeCollisionChecker:
    """
    碰撞沙盒 (Collision Sandbox) v4.5: 
    透過幾何向量運算追蹤塑料牆面，偵測空跑 (Travel) 是否會切穿「外牆」或「頂面」。
    這是 H2C 管線最後一道物理安全防線。
    """
    
    # --- 物理模擬常數 ---
    ENDPOINT_TOLERANCE = 0.05    # mm: 排除接縫擦拭 (Seam Wipe) 的誤判
    ZHOP_TOLERANCE = 0.02       # mm: 判定安全抬刀的高度閾值
    MIN_LAYER_HEIGHT = 0.15     # mm: 忽略機台初始清理線
    MICRO_GLIDE_LIMIT = 2.0     # mm: 忽略超短距離的滑行
    
    def __init__(self):
        self.reset_state()

    def reset_state(self):
        """重置狀態機"""
        self.current_x = 0.0
        self.current_y = 0.0
        self.current_z = 0.0
        self.current_e = 0.0
        self.is_absolute_e = True
        self.is_absolute_pos = True  
        self.current_layer_z = 0.0
        self.current_feature = "Unknown"
        self.extruded_lines: List[Dict] = []
        self.travel_moves: List[Dict] = []
        self.total_collisions = 0
        self.layer_num = 0

    @staticmethod
    def ccw(A: Point2D, B: Point2D, C: Point2D) -> bool:
        """向量外積：判斷三點走向"""
        return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])

    @staticmethod
    def segments_intersect(A: Point2D, B: Point2D, C: Point2D, D: Point2D) -> bool:
        """線段交點判定 (用於物理碰撞計算)"""
        # 1. 快速過濾：端點重合判定 (物理上屬於銜接而非碰撞)
        if (math.hypot(A[0]-C[0], A[1]-C[1]) < GCodeCollisionChecker.ENDPOINT_TOLERANCE or 
            math.hypot(B[0]-D[0], B[1]-D[1]) < GCodeCollisionChecker.ENDPOINT_TOLERANCE):
            return False

        # 2. 邊界盒 (AABB) 預測試：大幅提升處理速度
        if not (min(A[0], B[0]) <= max(C[0], D[0]) and max(A[0], B[0]) >= min(C[0], D[0]) and
                min(A[1], B[1]) <= max(C[1], D[1]) and max(A[1], B[1]) >= min(C[1], D[1])):
            return False
            
        # 3. 精確幾何交會判定
        return GCodeCollisionChecker.ccw(A, C, D) != GCodeCollisionChecker.ccw(B, C, D) and \
               GCodeCollisionChecker.ccw(A, B, C) != GCodeCollisionChecker.ccw(A, B, D)

    def analyze_layer(self):
        """核心分析：掃描當前層級的所有潛在致命碰撞"""
        if not self.travel_moves or not self.extruded_lines:
            return
            
        # 跳過非列印層 (如清理噴嘴的高度)
        if self.current_layer_z < self.MIN_LAYER_HEIGHT:
            return

        # 品質敏感特徵清單 (對齊 pipeline.py 與 kinematics.py)
        critical_tags = ["outer wall", "top surface", "external", "wall-outer"]
        layer_crashes = 0

        for travel in self.travel_moves:
            # 安全檢查：若空跑高度高於當前層 (Z-Hop)，則視為絕對安全
            if travel['z'] > self.current_layer_z + self.ZHOP_TOLERANCE:
                continue
                
            ts, te = travel['start'], travel['end']
            
            # 安全檢查：忽略微距滑行
            if math.hypot(te[0]-ts[0], te[1]-ts[1]) < self.MICRO_GLIDE_LIMIT:
                continue

            for wall in self.extruded_lines:
                # 僅針對會破壞表面品質的特徵進行警報
                feat = wall['feature'].lower()
                if any(tag in feat for tag in critical_tags):
                    ws, we = wall['segment']
                    if self.segments_intersect(ts, te, ws, we):
                        layer_crashes += 1
                        self.total_collisions += 1
                        print(f"  [!] 致命碰撞偵測：第 {self.layer_num} 層 (Z={self.current_layer_z:.3f})")
                        print(f"      空跑路徑橫切外牆：{wall['feature']}")
                        break # 一個空跑只需報警一次

    def run_check(self, file_path: str):
        """啟動沙盒模擬"""
        if not os.path.exists(file_path):
            print(f"  [!] 錯誤：找不到檔案 {file_path}")
            return

        print(f"\n" + "="*50)
        print(f"   H2C 高精密碰撞物理稽核 (品質防線)")
        print(f"   目標：{os.path.basename(file_path)}")
        print("="*50)

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line_s = line.strip()
                    if not line_s: continue

                    # 擷取特徵標記
                    feat_match = re.search(r';\s*(?:FEATURE|TYPE)\s*[:=]?\s*(.*)', line_s, re.IGNORECASE)
                    if feat_match:
                        self.current_feature = feat_match.group(1).strip()
                        continue

                    # 指令預處理
                    clean_line = line_s.split(';')[0].strip().upper()
                    if not clean_line: continue

                    # 模式切換
                    if "G90" in clean_line: self.is_absolute_pos = True
                    elif "G91" in clean_line: self.is_absolute_pos = False
                    elif "M82" in clean_line: self.is_absolute_e = True
                    elif "M83" in clean_line: self.is_absolute_e = False

                    # 參數提取
                    tokens = re.findall(r'([A-ZE])([-+]?\d*\.?\d+)', clean_line)
                    if not tokens: continue
                    params = {t[0]: float(t[1]) for t in tokens}
                    cmd = int(params.get('G', -1)) if 'G' in params else -1

                    # 處理坐標重置 (G92)
                    if cmd == 92:
                        if 'E' in params: self.current_e = params['E']
                        continue

                    # 處理運動指令 (G0/G1/G2/G3)
                    if cmd in (0, 1, 2, 3):
                        # 計算新坐標
                        if self.is_absolute_pos:
                            nx = params.get('X', self.current_x)
                            ny = params.get('Y', self.current_y)
                            nz = params.get('Z', self.current_z)
                        else:
                            nx = self.current_x + params.get('X', 0.0)
                            ny = self.current_y + params.get('Y', 0.0)
                            nz = self.current_z + params.get('Z', 0.0)

                        # 計算真實擠出增量
                        ae = 0.0
                        if 'E' in params:
                            ae = params['E'] - self.current_e if self.is_absolute_e else params['E']
                            # BUG FIX: M83 (relative) mode must accumulate, not reset to 0.
                            # Resetting caused false-negative collision detection because ae > 0
                            # comparisons evaluated wrong deltas after every retraction.
                            self.current_e = params['E'] if self.is_absolute_e else self.current_e + params['E']

                        # 層級切換偵測 (僅在絕對定位模式下，避免 G91 微抬誤判)
                        # BUG FIX: Removed `ae > 0` — pipeline output separates Z and E
                        # moves onto different lines, so nz > threshold AND ae > 0 is
                        # NEVER simultaneously true. Use absolute-mode Z-rise only.
                        if self.is_absolute_pos and nz > self.current_layer_z + 0.05:
                            self.analyze_layer()
                            self.extruded_lines.clear()
                            self.travel_moves.clear()
                            self.current_layer_z = nz
                            self.layer_num += 1
                        elif self.is_absolute_pos and nz < self.current_layer_z - 0.1:
                            # Z drop in absolute mode = returning from Z-hop or toolchange lift.
                            # Threshold 0.1mm catches small Z-hop restores (typ. 0.2-0.8mm)
                            # and large toolchange lifts (3mm+). G91 micro-lifts are excluded
                            # by the is_absolute_pos guard above.
                            self.analyze_layer()
                            self.extruded_lines.clear()
                            self.travel_moves.clear()
                            self.current_layer_z = nz

                        # 記錄路徑
                        dist = math.hypot(nx - self.current_x, ny - self.current_y)
                        if dist > 0.001:
                            if ae > 0.0001:
                                self.extruded_lines.append({
                                    'segment': ((self.current_x, self.current_y), (nx, ny)),
                                    'feature': self.current_feature
                                })
                            elif abs(ae) < 0.0001:
                                # Pure travel (no E): check for collision.
                                # ae < 0 → retract/seam-wipe: deliberate slicer behaviour,
                                # not a cross-wall collision concern.
                                self.travel_moves.append({
                                    'start': (self.current_x, self.current_y),
                                    'end': (nx, ny),
                                    'z': nz
                                })

                        self.current_x, self.current_y, self.current_z = nx, ny, nz

            # 檔案結尾分析最後一層
            self.analyze_layer()
            
        except Exception as e:
            print(f"  [!] 沙盒模擬崩潰：{e}")
            return

        print("="*50)
        if self.total_collisions == 0:
            print(f"   [通過] 路徑物理稽核完美！未發現外部特徵碰撞。")
        else:
            print(f"   [失敗] 偵測到 {self.total_collisions} 處可能破壞表面品質的致命碰撞。")
            print(f"   建議：檢查 Z-Hop 設定或開啟 AI 自動避障。")
        print("="*50 + "\n")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        GCodeCollisionChecker().run_check(sys.argv[1])
    else:
        print("用法: python src/safety/collision_sandbox.py <gcode_path>")
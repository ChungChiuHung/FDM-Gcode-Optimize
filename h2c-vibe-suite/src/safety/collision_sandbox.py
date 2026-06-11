import os
import re
import math
from typing import List, Tuple, Dict, Set

# 型別別名，提升維護性
Point2D = Tuple[float, float]
CellKey = Tuple[int, int]

class GCodeCollisionChecker:
    """
    碰撞沙盒 (Collision Sandbox) v4.8: 
    完美修復端點重合與回抽擦拭 (Wipe) 造成的幾何誤判。
    """
    
    # --- 物理模擬常數 ---
    ENDPOINT_TOLERANCE = 0.05    # mm: 排除接縫擦拭與端點重合的誤判
    ZHOP_TOLERANCE = 0.02       # mm: 判定安全抬刀的高度閾值
    MIN_LAYER_HEIGHT = 0.15     # mm: 忽略機台初始清理線
    MICRO_GLIDE_LIMIT = 3.0     # mm: 與 ZHopInjector 保持一致的 3.0mm 滑行容忍度
    CELL_SIZE = 10.0            # mm: spatial-hash grid cell side length

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
        # Spatial hash grid: maps (cell_x, cell_y) → list of wall-segment dicts.
        # Replaces the flat extruded_lines list for O(1) average bucket lookup.
        self._grid: Dict[CellKey, List[Dict]] = {}
        self.total_collisions = 0
        self.layer_num = 0
        self.is_new_layer = False

    # ------------------------------------------------------------------
    # Spatial-hash helpers
    # ------------------------------------------------------------------

    def _cells_for_bbox(self, x1: float, y1: float, x2: float, y2: float) -> List[CellKey]:
        """Return every grid cell whose bounding box overlaps the given AABB."""
        cs = self.CELL_SIZE
        cx_lo = int(math.floor(min(x1, x2) / cs))
        cx_hi = int(math.floor(max(x1, x2) / cs))
        cy_lo = int(math.floor(min(y1, y2) / cs))
        cy_hi = int(math.floor(max(y1, y2) / cs))
        return [
            (cx, cy)
            for cx in range(cx_lo, cx_hi + 1)
            for cy in range(cy_lo, cy_hi + 1)
        ]

    def _add_wall(self, seg_dict: Dict) -> None:
        """Bucket a wall segment into every cell its bounding box covers."""
        (x1, y1), (x2, y2) = seg_dict['segment']
        for cell in self._cells_for_bbox(x1, y1, x2, y2):
            self._grid.setdefault(cell, []).append(seg_dict)

    def _candidates(self, ts: Point2D, te: Point2D) -> List[Dict]:
        """Collect deduplicated wall segments from cells overlapping the travel bbox."""
        seen: Set[int] = set()
        result: List[Dict] = []
        for cell in self._cells_for_bbox(ts[0], ts[1], te[0], te[1]):
            for seg in self._grid.get(cell, []):
                sid = id(seg)
                if sid not in seen:
                    seen.add(sid)
                    result.append(seg)
        return result

    @staticmethod
    def ccw(A: Point2D, B: Point2D, C: Point2D) -> bool:
        """向量外積：判斷三點走向"""
        return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])

    @staticmethod
    def segments_intersect(A: Point2D, B: Point2D, C: Point2D, D: Point2D) -> bool:
        """線段交點判定 (用於物理碰撞計算)"""
        # 1. 快速過濾：端點重合判定 (完美修復 A=D, B=C 的幾何盲區)
        tol = GCodeCollisionChecker.ENDPOINT_TOLERANCE
        if (math.hypot(A[0]-C[0], A[1]-C[1]) < tol or 
            math.hypot(B[0]-D[0], B[1]-D[1]) < tol or
            math.hypot(A[0]-D[0], A[1]-D[1]) < tol or
            math.hypot(B[0]-C[0], B[1]-C[1]) < tol):
            return False

        # 2. 邊界盒 (AABB) 預測試
        if not (min(A[0], B[0]) <= max(C[0], D[0]) and max(A[0], B[0]) >= min(C[0], D[0]) and
                min(A[1], B[1]) <= max(C[1], D[1]) and max(A[1], B[1]) >= min(C[1], D[1])):
            return False
            
        # 3. 精確幾何交會判定
        return GCodeCollisionChecker.ccw(A, C, D) != GCodeCollisionChecker.ccw(B, C, D) and \
               GCodeCollisionChecker.ccw(A, B, C) != GCodeCollisionChecker.ccw(A, B, D)

    def check_travel_collision(self, ts: Point2D, te: Point2D, travel_z: float):
        """時序碰撞檢測：只與『當前時刻之前』已印好的牆面進行比對"""
        # 跳過非列印層
        if self.current_layer_z < self.MIN_LAYER_HEIGHT:
            return
            
        # 安全檢查：若空跑的「起點」或「終點」高於當前層 (Z-Hop 飛越或斜坡下降)，皆視為安全
        if travel_z > self.current_layer_z + self.ZHOP_TOLERANCE or \
           self.current_z > self.current_layer_z + self.ZHOP_TOLERANCE:
            return
            
        # 安全檢查：忽略微距滑行
        if math.hypot(te[0]-ts[0], te[1]-ts[1]) < self.MICRO_GLIDE_LIMIT:
            return

        critical_tags = ["outer wall", "top surface", "external", "wall-outer"]
        
        for wall in self._candidates(ts, te):
            feat = wall['feature'].lower()
            if any(tag in feat for tag in critical_tags):
                ws, we = wall['segment']
                if self.segments_intersect(ts, te, ws, we):
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

                    line_upper = line_s.upper()

                    # 捕捉切片軟體的換層標記，強制清空記憶體
                    if line_upper.startswith("; LAYER_CHANGE") or line_upper.startswith(";LAYER_CHANGE"):
                        self._grid.clear()
                        self.layer_num += 1
                        self.is_new_layer = True
                        continue

                    feat_match = re.search(r';\s*(?:FEATURE|TYPE)\s*[:=]?\s*(.*)', line_s, re.IGNORECASE)
                    if feat_match:
                        self.current_feature = feat_match.group(1).strip()
                        continue

                    clean_line = line_s.split(';')[0].strip().upper()
                    if not clean_line: continue

                    if "G90" in clean_line: self.is_absolute_pos = True
                    elif "G91" in clean_line: self.is_absolute_pos = False
                    elif "M82" in clean_line: self.is_absolute_e = True
                    elif "M83" in clean_line: self.is_absolute_e = False

                    tokens = re.findall(r'([A-ZE])([-+]?\d*\.?\d+)', clean_line)
                    if not tokens: continue
                    params = {t[0]: float(t[1]) for t in tokens}
                    cmd = int(params.get('G', -1)) if 'G' in params else -1

                    if cmd == 92:
                        if 'E' in params: self.current_e = params['E']
                        continue

                    if cmd in (0, 1, 2, 3):
                        if self.is_absolute_pos:
                            nx = params.get('X', self.current_x)
                            ny = params.get('Y', self.current_y)
                            nz = params.get('Z', self.current_z)
                        else:
                            nx = self.current_x + params.get('X', 0.0)
                            ny = self.current_y + params.get('Y', 0.0)
                            nz = self.current_z + params.get('Z', 0.0)

                        ae = 0.0
                        if 'E' in params:
                            ae = params['E'] - self.current_e if self.is_absolute_e else params['E']
                            self.current_e = params['E'] if self.is_absolute_e else 0.0

                        dist = math.hypot(nx - self.current_x, ny - self.current_y)
                        
                        if dist > 0.001:
                            if ae > 0.0001:
                                # 支援 Z 軸噴嘴清理後的下降
                                if abs(nz - self.current_layer_z) > 0.05:
                                    if nz > self.current_layer_z + 0.05 or nz < self.current_layer_z - 2.0:
                                        self._grid.clear()
                                        self.current_layer_z = nz
                                        if not self.is_new_layer:
                                            self.layer_num += 1
                                        self.is_new_layer = False

                                if self.is_new_layer:
                                    self.current_layer_z = nz
                                    self.is_new_layer = False

                                self._add_wall({
                                    'segment': ((self.current_x, self.current_y), (nx, ny)),
                                    'feature': self.current_feature
                                })
                            # [!] 完美修復：只有純空跑 (abs(ae) < 0.0001) 才檢查碰撞，完全排除合法的 Wipe 與回抽！
                            elif abs(ae) < 0.0001:
                                ts = (self.current_x, self.current_y)
                                te = (nx, ny)
                                self.check_travel_collision(ts, te, nz)

                        self.current_x, self.current_y, self.current_z = nx, ny, nz

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
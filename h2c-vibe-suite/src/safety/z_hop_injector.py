import math
from typing import List, Tuple

class ZHopInjector:
    """
    動態幾何避障引擎 (Dynamic Obstacle Avoidance)
    攔截切片軟體危險的空跑路徑，強制 Z-Hop 飛越已列印的實體牆面與頂層表面，防止噴嘴刮花表面或撞飛模型。
    """
    def __init__(self, z_hop: float = 0.4):
        self.z_hop_dist = max(0.2, z_hop)  # 最小抬升高度為 0.2mm
        self.current_z = 0.0
        # 儲存當前圖層的實體特徵線段 (x1, y1, x2, y2)
        self.wall_segments: List[Tuple[float, float, float, float]] = []

    def reset_layer(self, current_z: float):
        """每層開始時清空快取，重新記錄該層障礙物"""
        self.current_z = current_z
        self.wall_segments.clear()

    def add_wall_seg(self, x1: float, y1: float, x2: float, y2: float):
        """記錄一道不可跨越的實體牆面"""
        self.wall_segments.append((x1, y1, x2, y2))

    def _bounding_box_intersect(self, ax1, ay1, ax2, ay2, bx1, by1, bx2, by2) -> bool:
        """AABB 快速包圍盒碰撞過濾 (效能優化)"""
        return not (max(ax1, ax2) < min(bx1, bx2) or
                    min(ax1, ax2) > max(bx1, bx2) or
                    max(ay1, ay2) < min(by1, by2) or
                    min(ay1, ay2) > max(by1, by2))

    def _ccw(self, A, B, C) -> bool:
        """計算三個點的旋轉方向 (Counter-Clockwise)"""
        return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])

    def _intersect(self, A, B, C, D) -> bool:
        """判斷線段 AB (空跑路徑) 與線段 CD (實體牆面) 是否發生幾何交叉"""
        return self._ccw(A, C, D) != self._ccw(B, C, D) and self._ccw(A, B, C) != self._ccw(A, B, D)

    def get_hop_gcode(self, frm: Tuple[float, float, float], to: Tuple[float, float, float], cur_z: float, travel_f: int) -> str:
        """
        診斷空跑路徑，若會撞擊障礙物，則回傳抬升的 G-code。
        若安全無虞，回傳空字串。
        """
        if not self.wall_segments or self.z_hop_dist <= 0:
            return ""

        # 如果切片軟體本身就已經在執行 Z 軸抬升或換層，就不重複干預
        if abs(frm[2] - to[2]) > 0.001:
            return ""

        A = (frm[0], frm[1])
        B = (to[0], to[1])
        
        # 距離過短的微小移動不需要抬升 (防止頻繁點頭浪費時間)
        dist = math.hypot(B[0] - A[0], B[1] - A[1])
        if dist < 3.0:
            return ""

        needs_hop = False

        # 遍歷當前圖層所有的實體牆面進行射線碰撞測試
        for (wx1, wy1, wx2, wy2) in self.wall_segments:
            # 1. 第一階段：快速包圍盒過濾 (節省 90% 的運算時間)
            if self._bounding_box_intersect(A[0], A[1], B[0], B[1], wx1, wy1, wx2, wy2):
                C = (wx1, wy1)
                D = (wx2, wy2)
                # 2. 第二階段：精確的向量交叉測試
                if self._intersect(A, B, C, D):
                    needs_hop = True
                    break

        if needs_hop:
            hop_z = cur_z + self.z_hop_dist
            # 產生完美的 Z-Hop 飛行軌跡
            gcode = (
                f"; --- AI Auto Z-Hop: Obstacle Avoidance ---\n"
                f"G1 Z{hop_z:.3f} F1200 ; 安全抬升\n"
                f"G1 X{B[0]:.3f} Y{B[1]:.3f} F{travel_f} ; 飛越障礙區\n"
                f"G1 Z{cur_z:.3f} F1200 ; 精準降落\n"
            )
            return gcode
            
        return ""
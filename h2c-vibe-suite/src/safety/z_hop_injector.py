import math
from typing import Dict, List, Optional, Tuple

# Type aliases
Point2D = Tuple[float, float]
CellKey = Tuple[int, int]
WallSeg = Tuple[float, float, float, float]  # (x1, y1, x2, y2)


class ZHopInjector:
    """
    動態幾何避障引擎 (Dynamic Obstacle Avoidance)
    攔截切片軟體危險的空跑路徑，強制 Z-Hop 飛越已列印的實體牆面與頂層表面，防止噴嘴刮花表面或撞飛模型。

    Also maintains a spatial hash of wall segments (wall_grid) for use by
    TravelRouter when computing XY combing detours.
    """

    # Spatial hash cell size — matches GCodeCollisionChecker.CELL_SIZE so the
    # two safety modules share a consistent grid resolution.
    CELL_SIZE = 10.0  # mm

    def __init__(self, z_hop: float = 0.4):
        self.z_hop_dist = max(0.2, z_hop)  # 最小抬升高度為 0.2mm
        self.current_z = 0.0
        # Flat list — used by get_hop_gcode() (O(n) scan with AABB prefilter).
        self.wall_segments: List[WallSeg] = []
        # Spatial hash — used by TravelRouter for O(1)-avg bucket lookup.
        self._wall_grid: Dict[CellKey, List[WallSeg]] = {}

    def reset_layer(self, current_z: float):
        """每層開始時清空快取，重新記錄該層障礙物"""
        self.current_z = current_z
        self.wall_segments.clear()
        self._wall_grid.clear()

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

    def add_wall_seg(self, x1: float, y1: float, x2: float, y2: float):
        """記錄一道不可跨越的實體牆面"""
        seg: WallSeg = (x1, y1, x2, y2)
        self.wall_segments.append(seg)
        # Bucket the same object into the spatial hash so TravelRouter can
        # use id(seg) for O(1) deduplication across multi-cell segments.
        for cell in self._cells_for_bbox(x1, y1, x2, y2):
            self._wall_grid.setdefault(cell, []).append(seg)

    @property
    def wall_grid(self) -> Dict[CellKey, List[WallSeg]]:
        """Read-only view of the current layer's spatial hash for TravelRouter."""
        return self._wall_grid

    # ------------------------------------------------------------------
    # Public collision query API (used by TravelRouter)
    # ------------------------------------------------------------------

    # Endpoint-overlap tolerance — suppresses false positives at seam joins
    # where the travel start or end is physically on a wall endpoint.
    _ENDPOINT_TOL: float = 0.05  # mm

    def find_blocking_bbox(
        self,
        start_pt: Tuple[float, float],
        end_pt: Tuple[float, float],
    ) -> Optional[Tuple[float, float, float, float]]:
        """
        Return the union AABB (min_x, min_y, max_x, max_y) of every wall
        segment that the direct line start_pt→end_pt crosses, or None if
        the path is clear.

        Uses the spatial hash for O(1)-avg bucket lookup and the same
        geometry primitives as get_hop_gcode, plus an endpoint-tolerance
        guard to suppress false positives at seam/wipe joins.
        """
        sx, sy = start_pt
        ex, ey = end_pt
        tol = self._ENDPOINT_TOL
        seen: set = set()
        blocking: List[WallSeg] = []

        for cell in self._cells_for_bbox(sx, sy, ex, ey):
            for seg in self._wall_grid.get(cell, []):
                sid = id(seg)
                if sid in seen:
                    continue
                seen.add(sid)
                x1, y1, x2, y2 = seg
                # Endpoint-overlap guard
                if (math.hypot(sx - x1, sy - y1) < tol or
                        math.hypot(ex - x2, ey - y2) < tol or
                        math.hypot(sx - x2, sy - y2) < tol or
                        math.hypot(ex - x1, ey - y1) < tol):
                    continue
                if not self._bounding_box_intersect(sx, sy, ex, ey, x1, y1, x2, y2):
                    continue
                A, B, C, D = (sx, sy), (ex, ey), (x1, y1), (x2, y2)
                if self._intersect(A, B, C, D):
                    blocking.append(seg)

        if not blocking:
            return None

        return (
            min(min(s[0], s[2]) for s in blocking),
            min(min(s[1], s[3]) for s in blocking),
            max(max(s[0], s[2]) for s in blocking),
            max(max(s[1], s[3]) for s in blocking),
        )

    def check_intersection(
        self,
        start_pt: Tuple[float, float],
        end_pt: Tuple[float, float],
    ) -> bool:
        """Return True if the direct path start_pt→end_pt crosses any wall."""
        return self.find_blocking_bbox(start_pt, end_pt) is not None

    # ------------------------------------------------------------------
    # Geometry helpers (unchanged from original)
    # ------------------------------------------------------------------

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

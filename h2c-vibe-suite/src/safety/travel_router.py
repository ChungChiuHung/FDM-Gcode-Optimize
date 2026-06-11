import math
from typing import List, Tuple

Point2D = Tuple[float, float]


class TravelRouter:
    """
    Avoid-Crossing-Perimeters / Combing Router.

    Computes XY dog-leg detour paths for travel moves that would otherwise
    cross registered wall segments. This is the 2D complement to ZHopInjector:
    Z-hop lifts the nozzle over walls; TravelRouter steers around them in-plane.

    All intersection geometry is delegated to the ZHopInjector instance (single
    source of truth) via its check_intersection() and find_blocking_bbox() API.

    Algorithm — two phases, tried in order
    --------------------------------------
    PHASE 1 — Single-waypoint dog-leg (works for convex polygon walls):
      1. Ask hop_injector.find_blocking_bbox(start, end) to detect all crossing
         wall segments and return their union AABB.
      2. Expand the AABB by BBOX_MARGIN mm for routing clearance.
      3. Try six candidate single-waypoint dog-legs in priority order:
           - Two L-shape corners  (sx, ey) and (ex, sy)
           - Four expanded-AABB corners
      4. For each candidate, verify both legs are clear via
         hop_injector.check_intersection().
      5. Return [waypoint, end_pt] on the first valid candidate.

    PHASE 2 — Perpendicular bridge (for circular / hollow geometries):
      When all Phase-1 candidates fail — typically when start and end are on
      opposite sides of a circular hole and every single-waypoint leg still
      bisects the hole — use a two-waypoint U-shaped bypass:
        WP1 = start pushed perpendicular to the travel line by perp_push
        WP2 = end  pushed perpendicular to the travel line by perp_push (same dir)
        path: start → WP1 → WP2 → end  (3 segments, all tested for clearance)
      push distance = half the max crossing-AABB span (≈ obstacle radius for circles)
      plus BRIDGE_EXTRA_MARGIN for guaranteed clearance.
      Both perpendicular signs (left / right of travel) are tried.

    Hardware bounds clamping is the caller's responsibility (pipeline.py).
    """

    MIN_DETOUR_DIST: float = 3.0     # mm — skip routing for micro-glides
    BBOX_MARGIN: float = 2.0          # mm — expand obstacle AABB for clearance
    ENDPOINT_TOLERANCE: float = 0.05  # mm — degenerate-waypoint suppression
    BRIDGE_EXTRA_MARGIN: float = 4.0  # mm — extra push on top of half-span for bridge

    @staticmethod
    def route_safe_travel(
        start_pt: Point2D,
        end_pt: Point2D,
        hop_injector,
    ) -> List[Point2D]:
        """
        Return a list of (X, Y) waypoints from start_pt to end_pt that avoids
        registered wall segments tracked by hop_injector.

        Returns
        -------
        [end_pt]                   — direct path is clear, or travel < MIN_DETOUR_DIST
        [waypoint, end_pt]         — single dog-leg detour found (Phase 1)
        [wp1, wp2, end_pt]         — perpendicular bridge detour found (Phase 2)
        [end_pt]                   — no clean detour found (safe passthrough)

        Caller must clamp every returned waypoint to hardware bounds.
        """
        sx, sy = start_pt
        ex, ey = end_pt

        # Skip expensive routing for micro-glides.
        if math.hypot(ex - sx, ey - sy) < TravelRouter.MIN_DETOUR_DIST:
            return [end_pt]

        # No walls tracked yet on this layer → nothing to route around.
        if not hop_injector.wall_segments:
            return [end_pt]

        # Detect obstacle and get its union AABB in one call.
        obs = hop_injector.find_blocking_bbox(start_pt, end_pt)
        if obs is None:
            return [end_pt]

        # Expand AABB for routing clearance.
        m = TravelRouter.BBOX_MARGIN
        ox_min = obs[0] - m
        oy_min = obs[1] - m
        ox_max = obs[2] + m
        oy_max = obs[3] + m

        tol = TravelRouter.ENDPOINT_TOLERANCE

        # ------------------------------------------------------------------
        # Phase 1: Standard six-candidate single-waypoint dog-leg.
        # Works well for straight walls and convex polygon obstacles.
        # ------------------------------------------------------------------
        candidates: List[Point2D] = [
            (sx, ey),           # L-shape: travel along start-X then end-Y
            (ex, sy),           # L-shape: travel along start-Y then end-X
            (ox_min, oy_min),   # expanded AABB bottom-left
            (ox_min, oy_max),   # expanded AABB top-left
            (ox_max, oy_min),   # expanded AABB bottom-right
            (ox_max, oy_max),   # expanded AABB top-right
        ]

        for wp in candidates:
            # Reject waypoints that collapse to start or end.
            if (math.hypot(wp[0] - sx, wp[1] - sy) < tol or
                    math.hypot(wp[0] - ex, wp[1] - ey) < tol):
                continue

            # Both legs must be clear of all registered walls.
            if (not hop_injector.check_intersection(start_pt, wp) and
                    not hop_injector.check_intersection(wp, end_pt)):
                return [wp, end_pt]

        # ------------------------------------------------------------------
        # Phase 2: Perpendicular bridge — for circular / hollow geometries.
        #
        # When start and end lie on opposite sides of a hole, every Phase-1
        # single-waypoint candidate has at least one leg that still cuts through
        # the obstacle interior.  The fix is a 3-segment U-shape:
        #
        #   start ──► WP1 ──► WP2 ──► end
        #              ↑         ↑
        #     perpendicularly pushed from start / end by perp_push
        #
        # The perpendicular push distance is derived from the crossing AABB:
        # for a horizontal travel across a circular hole of radius R, the
        # crossing AABB has width ≈ 2R, so half_span ≈ R — precisely the push
        # we need to clear the top / bottom of the circle.
        # ------------------------------------------------------------------
        dx = ex - sx
        dy = ey - sy
        travel_len = math.hypot(dx, dy)

        if travel_len > 0.0:
            # Perpendicular unit vector (rotated 90° CCW from travel direction).
            perp_x = -dy / travel_len
            perp_y = dx / travel_len

            # Half the max crossing-AABB span approximates the obstacle's
            # effective radius in the travel direction.
            obs_max_span = max(obs[2] - obs[0], obs[3] - obs[1])
            perp_push = obs_max_span / 2.0 + TravelRouter.BRIDGE_EXTRA_MARGIN

            for sign in (1, -1):
                wp1: Point2D = (sx + perp_x * sign * perp_push,
                                sy + perp_y * sign * perp_push)
                wp2: Point2D = (ex + perp_x * sign * perp_push,
                                ey + perp_y * sign * perp_push)

                # Reject degenerate waypoints (shouldn't happen with perp_push > 0,
                # but guard defensively).
                if (math.hypot(wp1[0] - sx, wp1[1] - sy) < tol or
                        math.hypot(wp2[0] - ex, wp2[1] - ey) < tol):
                    continue

                # All three legs must be clear.
                if (not hop_injector.check_intersection(start_pt, wp1) and
                        not hop_injector.check_intersection(wp1, wp2) and
                        not hop_injector.check_intersection(wp2, end_pt)):
                    return [wp1, wp2, end_pt]

        # No valid detour found — passthrough rather than inject a bad path.
        return [end_pt]

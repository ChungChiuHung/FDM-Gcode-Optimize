AI Handoff & Architecture Report — H2C Vibe Suite

Generated: 2026-06-08 | Status: GOLD MASTER BASELINE (16/16 Tests Passing)
Scope: src/core/, src/physics/, src/safety/, src/agent/, tests/
Pipeline version: H2C Geometric AI Pipeline v4.9 (Non-Destructive Routing)

1. Project Architecture & State

Decoupling & Stability Statement

The system is explicitly decoupled from the legacy monolith. The project has transitioned from "Destructive Path Optimization" (TSP/Serpentine sorting) to a "Non-Destructive Physics Injector." The pipeline perfectly preserves the slicer's original travel intents while layering advanced kinematics, thermal dynamics, and collision avoidance on top.

Directory Structure (Prefix-Based Naming)

h2c-vibe-suite/
├── src/
│   ├── main.py                     # CLI Entry, Logger Interceptor (Recursion-Safe)
│   ├── core/
│   │   ├── pipeline.py             # Main Orchestrator (OOM-Safe Generator Loop)
│   │   ├── parser.py               # Streaming G-Code Parser (Features, Z-Tracking)
│   │   └── io_manager.py           # AtomicGCodeWriter (Crash-safe file replacement)
│   ├── physics/
│   │   ├── kinematics.py           # AntiResonanceBrake, Inertia Dampening
│   │   ├── materials.py            # Material Profile Matrix (CF/GF, PETG, TPU)
│   │   └── thermodynamics.py       # Active Thermal Equalizer (BONDING/COOLING)
│   ├── safety/
│   │   ├── bounds_enforcer.py      # Hardware limits via header parsing
│   │   ├── collision_sandbox.py    # Math sandbox (10mm Grid, CCW intersection)
│   │   ├── travel_router.py        # Combing/Detour Pathfinding (AABB/Dog-leg/U-Bridge)
│   │   └── z_hop_injector.py       # Clearance enforcement
│   └── agent/
│       └── mcp_server.py           # FastMCP Server for LLM trigger injection


2. High-Performance Engine Specs

2.1 — Combing & Travel Router (travel_router.py)

Automatically detects if a travel move (longer than 3.0mm) intersects a printed wall.

Perpendicular Bridge (Bug #9 Fix): For circular hole crossings where AABB candidates fail, it computes a two-waypoint rectangular U-shape bypass pushed perpendicularly by (max_crossing_AABB_span / 2) + 4mm to safely clear hollow geometries.

Physically steers the nozzle around open spaces to eliminate stringing without relying on excessive retractions.

2.2 — 10 mm Spatial Hash Grid (collision_sandbox.py & z_hop_injector.py)

Replaces O(n) scan with a dict-based 2D grid (CELL_SIZE = 10.0).

Endpoint Tolerance (0.05mm): Filters out false-positive collisions where travel moves safely touch wall endpoints (nozzle landings/wipes).

Comprehensive Registration: Tracks both outer wall AND inner wall (using the broad 'wall' tag) to ensure holes and perimeters are fully registered as obstacles.

2.3 — Anti-Resonant Braking (kinematics.py)

Uses kinetic energy (E = 0.5 * m * v^2) mapped to a 450g toolhead to calculate physical settling time.

Emits a dynamic sequence: M204/M205 Drop -> G91 G1 Z0.15 (Micro-lift) -> G4 P{ms} (Dwell) -> G1 Z-0.15 -> M204/M205 Restore.

2.4 — OOM Protection & Atomic I/O

MAX_HEADER_LINES = 5000 & MAX_FOOTER_LINES = 2000 cap RAM usage on malformed files.

AtomicGCodeWriter writes entirely to a .tmp file, using os.replace to guarantee the original G-Code is never corrupted.

3. Vibe Coding Sync & Strict Rules

🚨 Critical Directives for Future Modifications:

Preserve Ironing Integrity: The threshold abs(e_val) > 0.00001 and the is_ironing feedrate bypass in pipeline.py MUST NEVER be altered or rounded.

Non-Destructive Routing: Do not attempt to introduce TSP or serpentine sorting into the core pipeline. The engine assumes the slicer's pathing is correct and only injects detours (TravelRouter) or Z-Hops.

Nozzle Temperature Guard: The thermal equalizer must always retain the base_temp.get("normal", 0) > 50 sanity check.

Universal Wall Tracking: hop_injector.add_wall_seg() MUST trigger on the substring 'wall' (not strictly 'outer wall') so that inner holes are correctly loaded into the spatial grid for Combing and Z-Hop detection.

Active Threshold Constants

| Constant | Value | Purpose |
| MAX_HEADER_LINES | 5000 | OOM cap on header accumulation |
| MAX_FOOTER_LINES | 2000 | OOM cap on footer accumulation |
| CELL_SIZE | 10.0 mm | Spatial hash grid cell side |
| ENDPOINT_TOLERANCE | 0.05 mm | Seam/wipe false-positive filter |
| MICRO_GLIDE_TOLERANCE | 2.0 mm | Safe travel distance to drag nozzle |
| MASS_KG | 0.45 kg | H2C toolhead mass |
| MICRO_LIFT_MM | 0.15 mm | Z-lift height during resonant braking |
| BASE_DWELL_MS | 15.0 ms | Minimum G4 dwell for kinetic braking |
| MAX_DWELL_MS | 50.0 ms | Maximum G4 dwell (prevents ooze) |
| MIN_MEANINGFUL_SPEED | 1.0 mm/s | Prevents zero-division in vol_flow |

4. Current Test Status & Bug Report

Status: ALL CLEAR. No known physical or logical defects.
Tests: 16 / 16 Unit and Integration Tests Passing (100%).

End of Handoff Report.
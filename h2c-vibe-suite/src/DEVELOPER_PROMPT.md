H2C Geometric AI Suite: Quality-Centric System Architecture

1. Project Mission: Quality-First FDM

The h2c-vibe-suite is an enterprise-grade geometric AI engine designed to maximize FDM print quality through physics-aware post-processing. It transforms standard slicer output (Bambu Studio/Orca) into high-fidelity, resonance-compensated G-code by modeling toolhead momentum, volumetric flow dynamics, and thermal equilibrium.

2. Layered Architecture

I. Data Ingestion Layer (OOM-Safe)

Module: src/core/parser.py

Mechanism: Implements a Python Generator-based streaming parser.

Logic: Processes G-code layer-by-layer to avoid RAM exhaustion on high-detail files (>500MB). It extracts feature tags (e.g., ; FEATURE: Outer wall) to drive downstream physics interventions.

II. Geometric Optimization Layer

Module: src/core/pathing.py

Macro-Logic (TSP): A localized Greedy algorithm that minimizes travel distance between extrusion islands to maintain gantry rhythm.

Micro-Logic (Sweep): Serpentine sorting and island reversal to maintain toolhead momentum and minimize surface artifacts like "Hull Lines" (box lines).

III. Physics & Material Layer (The "Vibe" Plugins)

Thermodynamics (thermodynamics.py):

Active Thermal Equalizer: Dynamically adjusts M104 based on Volumetric Flow Rate ($mm^3/s$) and extrusion distance to stabilize nozzle pressure.

Bonding Mode: Increases temperature by $+7^\circ C$ for long paths ($>150mm$) to maximize polymer chain entanglement.

Cooling Mode: Decreases temperature by $-5^\circ C$ for micro-features ($<15mm$) to prevent heat accumulation and sagging.

Kinematics (kinematics.py):

Anti-Resonance Brake: Calculates toolhead kinetic energy ($E = 0.5 \cdot m \cdot v^2$) for the $450g$ H2C gantry.

Lift-Settle Routine: Injects a $0.15mm$ Micro-Lift and calculated $G4$ dwell during high-speed braking to eliminate ghosting and corner artifacts.

Materials (materials.py):

Profile Matrix: Automatically detects filament types (PLA-CF, PETG, TPU) and applies specialized speed/Z-hop multipliers.

Uniform Speed Logic: Forces constant velocity on outer walls for PETG to eliminate visible shrinkage lines at floor heights.

IV. Safety & Audit Layer (Closed-Loop Guard)

Module: src/safety/collision_sandbox.py

Mechanism: Mathematical reconstruction of printed walls using Vector Cross Products.

Policy: Triggers a fatal warning if a travel move cuts through a "Critical Feature" (Outer Wall/Top Surface) without a sufficient Z-hop.

Module: src/safety/bounds_enforcer.py

Mechanism: Clamps coordinates to the printer's printable_area detected in the G-code header.

3. Data Flow Workflow

Ingest: Bambu Studio exports raw G-code to main.py.

Coordinate: pipeline.py initializes the streaming layer generator.

Intervene: Physics plugins calculate dynamic M104 (Thermal), M204 (Accel), and G4 (Dwell) overrides.

Audit: The Collision Sandbox performs a final geometric verification of all modified paths.

Output: Quality-hardened G-code is saved, preserving all retraction/wipe pairs ("Bookending").

4. Technical Constraints

Language: Python 3.10+ (uv managed).

Reliable Engineering: All source files must remain under 200 lines to ensure strict Single Responsibility.

Integrity: Zero destructive modifications to E (extrusion) values; arc integrity ($G2/G3$) must be locked.

Reference: H2C Geometric AI Pipeline v4.6 (Quality Focused)
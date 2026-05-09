Agentic G-Code Optimization: Lab Manual

1. Project Overview

This log tracks the development of a localized AI Agent designed to intercept, analyze, and optimize 3D printing G-code through Geometric AI, kinematics smoothing, and hardware-specific triggers.

2. The Unified "God Script" Architecture (Automated Production Mode)

The agent now operates as a fully autonomous, direct post-processing hook via gcode_optimizer.py. The previously fragmented pipeline (parser, optimizer, simulator) has been consolidated into a single monolithic engine to ensure perfect state-tracking and Bambu Studio compatibility.

Slicer Export: Bambu Studio natively hands the raw .gcode to gcode_optimizer.py upon export.

Dynamic Material Matrix: The AI dynamically parses the header for filament type (PLA-CF, PETG, TPU) and automatically scales volumetric flow ratios and caps abrasive travel speeds.

Geometric Sweep & Audit: Toolpaths are clustered, bi-directionally aligned for momentum, and mathematically audited inside a closed-loop physics sandbox to prevent fatal collisions.

Auto-Injection: The script securely imports the FastMCP server tools (gcode_mcp_server.py) to automatically inject M1004 cooling lifecycle commands into the final file.

Centralized Execution Logging: The orchestrator script (gcode_master.py) automatically intercepts standard output and errors, writing full execution traces, hardware clamp warnings, and crash reports to a dedicated .log file for historical optimization checking and debugging.

3. Lab Results: Core Engine Breakthroughs

Date: 2026-05-08

Status: Unified script deployed and verified against maximum-complexity edge-cases (Gears, IDEX, Wipes).

Critical Mathematical Fixes:

The Arc Parameter (P) Rule: The parser now correctly captures full-circle G2/G3 gear teeth geometry, preventing arc-radius collapse.

The Retraction Wipeout Fix: Pure Extruder (E) movements (retractions, de-retractions, and slicer wipes) are now safely bundled into extrusion islands without corrupting XY coordinates or causing pressure-advance failure.

Dynamic Hardware Enforcer: The AI mathematically scans the header's printable_area to build a virtual bounding box, making the pipeline universally compatible with any printer size (A1 Mini, X1C, etc.) without risk of frame crashes.

4. FDM G-Code Modification Best Practices Applied

To prevent catastrophic print failures and hardware damage, this agentic pipeline strictly adheres to the following industry best practices for FDM post-processing:

State Machine Integrity: The parser meticulously tracks Absolute vs. Relative positioning modes (G90/G91 for XYZ, M82/M83 for E). G-code math relies on historical context; failing to track state changes leads to compounding coordinate drift.

Metadata & Macro Shielding: Manufacturer start/end sequences (e.g., bed tramming G380, timelapse G1 Z112) and slicer feature tags (; FEATURE: Outer wall) are safely bypassed and prepended/appended untouched. The AI only modifies the core print body.

Pressure Advance & Retraction Isolation: Zero-XY extrusion moves (like retractions, de-retractions, and seam wipes) are never stripped or arbitrarily reversed. They are carefully "bookended" around extrusion paths to preserve the slicer's highly calibrated nozzle pressure dynamics.

Curvilinear (Arc) Preservation: The engine explicitly tracks circular interpolation commands (G2/G3) along with their offset parameters (I, J, R, P). Clamping endpoints or reversing arc logic mathematically destroys the radius, so they are safely bypassed to preserve high-resolution curves.

Kinematic Throttling (Inertia Dampening): Instead of using static feedrates, the optimizer injects dynamic Acceleration limits (M204) tailored to the mass of the custom H2C toolhead—dropping acceleration for dense gap-fills and raising it for long outer walls to eliminate mechanical ringing.

Proportional Flow Scaling: When modifying speeds for abrasive filaments (CF/GF) or high-viscosity plastics (PETG), the engine uses proportional multipliers against the original F values to perfectly maintain the slicer's volumetric flow ratios across different feature types.

Execution Traceability & Logging: All pipeline executions must be recorded. By maintaining a centralized .log file structure, the agent ensures that previous optimization stats (like 'islands sorted') and critical safety warnings (like hardware bounding clamps or OOM errors) are preserved for retrospective analysis.

5. Agent-Driven Expansion Framework

The codebase is now structured to be directly modified by VS Code Copilot. Using the @gcode_optimizer.py prompt tag, the AI can seamlessly expand the Material Matrix, refactor memory usage via Python generators, or implement multi-threading for the collision sandbox.

6. Next Research Prompt (NotebookLM)

"We have upgraded our post-processing pipeline into a unified 'God Script' that automatically scales extrusion speeds based on material composition (like CF/GF) while simultaneously restructuring toolpaths using a Hybrid Serpentine TSP algorithm. How does decoupling the slicer's speed hardcodes and replacing them with proportional flow-scaling affect the mechanical shear strength of carbon-fiber parts printed using momentum-aligned toolpaths?"
H2C Geometric AI Pipeline: System Architecture & Technical Specification

1. Project Overview

H2C Vibe Suite is a "plug-in geometric AI optimization engine" designed for FDM 3D printers.
This project adopts Reliable Engineering and SRP (Single Responsibility Principle) for its modular design. It acts as a post-processing script that intercepts the G-Code output from slicers (e.g., Bambu Studio / Orca Slicer) to perform dynamic path clustering, physics-based parameter interventions, and mathematical collision prevention.

2. Directory Structure

The project uses a modern Python isolated environment (powered by uv). The directory layout is as follows:

h2c-vibe-suite/
├── pyproject.toml          # Project Brain: Dependencies and pytest configuration
├── logs/                   # Centralized Logging (auto-intercepts stdout/stderr)
├── spec/                   # System Architecture & Technical Specs (This file)
├── tests/                  # TDD Safety Net (pytest)
│   └── test_physics.py     # Unit tests for the physics engine
└── src/
    ├── main.py             # Entry point and logger interceptor
    ├── agent/              # AI Agent Interface
    │   └── mcp_server.py   # FastMCP Server (allows LLMs to dynamically inject M1004)
    ├── core/               # Geometric Compute Core
    │   ├── parser.py       # OOM-Safe streaming parser
    │   ├── pathing.py      # Hybrid Cluster-Sweep pathing algorithm
    │   └── pipeline.py     # Core pipeline coordinator
    ├── physics/            # Physics & Material Plugins
    │   ├── kinematics.py   # Inertia Dampener (Dynamic M204 for heavy toolheads)
    │   ├── materials.py    # Material Profile Matrix (Dynamic flow & Z-Hop)
    │   └── thermodynamics.py# Active Thermal Equalizer (Dynamic M104 temp control)
    └── safety/             # Safety Nets
        ├── bounds_enforcer.py  # Physical hardware boundary locks
        └── collision_sandbox.py# Mathematical intersection & collision sandbox


3. Core Modules

3.1 Streaming Parser (src/core/parser.py)

Mechanism: Abandons loading the entire file into RAM at once. Instead, it utilizes Python Generators (yield) to achieve an OOM-Safe (Out-Of-Memory Safe) architecture. It instantiates and processes only a "Single Layer" of extrusion paths at a time.

3.2 Path Optimizer (src/core/pathing.py)

Mechanism:

Macro-TSP: Uses a Greedy algorithm to sort scattered extrusion islands by the shortest path.

Micro-Sweep & Serpentine Reversal: Divides islands into flight lanes using lane_width and dynamically reverses the printing direction (Reverse Island) to minimize travel jumps.

4. Physics & Safety Plugins

4.1 Active Thermal Equalizer (src/physics/thermodynamics.py)

Dynamically adjusts the nozzle temperature (M104) based on the "continuous extrusion distance" of the current feature:

Bed Adhesion (First Layer): Strictly locks the base temperature.

Cooling Mode: Distance < 15mm. Drops temp by 5°C for micro-features to prevent heat creep/melting.

Bonding Mode: Distance > 150mm. Raises temp by 5°C for long straight lines to maximize polymer chain shear strength.

4.2 Kinematics / Inertia Dampener (src/physics/kinematics.py)

Addresses resonance issues caused by the heavy H2C toolhead by dynamically injecting M204 acceleration commands:

Travel: 5000 mm/s² (Zero extrusion resistance, full speed).

Gap Fill / Small: 1500 mm/s² (Heavy braking to eliminate ringing/ghosting).

Outer Wall: 5000 mm/s² (Smooth long lines, tolerates higher acceleration).

4.3 Collision Sandbox (src/safety/collision_sandbox.py)

Utilizes Vector Cross Products to mathematically reconstruct printed walls in virtual space. If a Travel Move cuts directly through an "Outer Wall" or "Top Surface" without a safe Z-Hop, it triggers a Fatal Collision warning. Includes a 0.05mm endpoint tolerance to prevent false positives from Seam Wipes or nozzle landings.

5. Development Workflow (Commands)

Run the Optimization Pipeline:

uv run python src/main.py "path/to/your/file.gcode"


Run the TDD Safety Net (Tests):

uv run pytest


Start the MCP AI Server:

uv run python src/agent/mcp_server.py
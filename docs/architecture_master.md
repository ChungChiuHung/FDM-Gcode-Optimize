FDM-Optimize: Slicer G-Code Architecture Reference

Date: 2026-06-11
Target Hardware: H2C (450g Toolhead) & X2D (Dual-Nozzle, 256^3 Volume)
Slicer Engine: Bambu Studio / OrcaSlicer

1. Executive Summary

The stock manufacturer G-code profiles for the H2C and X2D contain several aggressive kinematic and thermal parameters that present severe risks to the hardware (bed scratching, heat creep, gantry resonance, and Z-axis crashes).

This document outlines the Certified AI-Safe Baseline. We have deliberately stripped "smart" tracking logic out of the slicer (to be handled by the Python backend) and locked the slicer down to serve exclusively as a safe, predictable, state-changing engine.

2. The 5 Core Hardware Safety Directives

Directive 1: The Zero-Baseline Anti-Scratch Matrix

Vulnerability: Stock profiles use negative Z-offsets (Z-0.03) to squish filament, which mathematically overrides the auto-leveling sensors and physically grinds the nozzle into PEI plates.
Solution: All Z-offsets in the Start G-code are strictly clamped to a 0.00 or positive baseline. Squish issues must be resolved physically (cleaning the nozzle/bed), not via G-code.

{if curr_bed_type=="Textured PEI Plate"}
  G29.1 Z{0.00}   ; Safe neutral baseline
{else}
  G29.1 Z{0.00}   ; Safe neutral baseline
{endif}



Directive 2: Z-Limit Bounding

Vulnerability: Unbounded relative lifts (e.g., G1 Z{max_layer_z + 10}) will cause the motors to crash into the physical frame if a print reaches the absolute top of the 256mm build volume.
Solution: All clearance lifts in End and Filament Change G-code are mathematically bounded using min().

G1 Z{min(max_layer_z + 3.0, 256)} F3000



Directive 3: PLA Heat Creep Lockout

Vulnerability: Stock macros blindly activate the active chamber heater based on slicer temperatures, causing PLA/PLA-CF to soften inside the extruder gears and jam.
Solution: Hardcoded string-matching override in the Start G-code.

{if (filament_type[initial_no_support_filament_id] == "PLA" || filament_type[...] == "PLA-CF")}
    M141 S0 ; HARD OVERRIDE: Force Chamber Heater OFF
    M145 P0 ; Set airduct to cooling mode
{else}
    ; ... normal heating logic
{endif}



Directive 4: Kinematic Dampening

Vulnerability: Stock profiles enforce M204 S10000 (10k acceleration) which causes violent gantry resonance, especially on the heavy 450g H2C toolhead.
Solution: Accelerations are clamped to 5000 at startup and heavily dampened (M400) before directional changes.

Directive 5: Firmware-Native Pause/Resume

Vulnerability: Manually injecting absolute coordinates (X250 Y250) during a pause breaks the firmware's coordinate tracking, causing resume failures.
Solution: Use the manufacturer's native hardware macro.

M400 U1 ; Trigger the machine's native pause/park/wipe sequence



3. Slicer Configuration Map (Text Box Roles)

To prevent compiler crashes and blind-travel collisions, the slicer's text boxes are strictly configured as follows:

| Slicer Text Box | Status | Role / Contents |
| Machine start G-code | FILLED | Bed leveling, thermal lockouts, 5k accel limit, zero-baseline Z-offsets, safe load-line escape. |
| Machine end G-code | FILLED | Final -2.0 retract, min(z, 256) bounded clearance lifts, safe XY parking. |
| Layer change G-code | FILLED | OEM Fan cooling matrices and M991 S0 P{layer_num} timelapse sync. (NO mechanical wipe logic). |
| Change filament G-code | FILLED | Pre-travel -2.0 retract, OEM flush logic, bounded Z-lifts, post-travel accel clamping. |
| Pause G-code | FILLED | Strictly M400 U1 (Firmware native sequence). |
| Template Custom G-code | EMPTY | Slicer compiler does not support cross-box custom variables. |
| Time lapse G-code | EMPTY | Managed natively via M991 in the Layer Change script. |
| By object G-code | EMPTY | Managed natively by the slicer's internal collision-avoidance engine. |

4. The Python Handoff: Why We Emptied the Slicer

The most critical architectural decision was removing the Periodic Nozzle Wipe from the slicer's "Layer Change" box.

The Reason: The slicer is mathematically blind to the cumulative volume of extruded plastic, and it loses track of XY coordinates during custom Layer Change macros, creating a massive risk of the toolhead driving through the printed part upon return.

The Backend Solution: The intelligent tracking is handed off to the Python application (volumetric_wipe_injector.py). The Python script will:

Parse the completed .gcode file line-by-line.

Sum the exact volumetric extrusion (G1 E...).

Safely inject a custom wipe sequence during a valid travel move once the sticky-filament threshold (e.g., 15 meters) is reached.

Calculate and enforce exact, collision-free return coordinates.
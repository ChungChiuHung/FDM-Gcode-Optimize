import os
import re
import sys
import json
from typing import Any, Dict, List

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    # Print errors to STDERR so we don't corrupt the MCP stdio stream
    print("'mcp' package not found. Ensure your venv is active.", file=sys.stderr)
    sys.exit(1)

# Initialize FastMCP server with a distinct name
mcp = FastMCP("G-Code-Optimizer-Agent")

@mcp.tool()
def analyze_gcode_features(file_path: str) -> str:
    """
    Analyzes a G-code file to identify layers, walls, bridges, and current settings.
    Skips the configuration header to avoid false positives.
    """
    if not os.path.exists(file_path):
        return f"Error: File {file_path} not found."

    stats: Dict[str, Any] = {
        "total_lines": 0,
        "layers": 0,
        "outer_walls": 0,
        "bridges": 0,
        "detected_nozzle_temps": set(),
    }

    try:
        is_in_data_section = False
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                stats["total_lines"] += 1
                line_lower = line.lower()
                
                # Detect the start of actual print data to skip config headers
                if not is_in_data_section:
                    if ";layer_change" in line_lower or "g28" in line_lower or ";layer:1" in line_lower:
                        is_in_data_section = True
                    continue

                # Broad pattern matching for various slicer versions (Bambu/Orca)
                if re.search(r';\s*layer\s*[:_]?\s*(\d+)', line_lower):
                    stats["layers"] += 1
                
                if 'outer wall' in line_lower:
                    stats["outer_walls"] += 1
                if 'bridge' in line_lower or ';type:bridge' in line_lower:
                    stats["bridges"] += 1
                
                temp_match = re.search(r'M104 S(\d+)', line)
                if temp_match:
                    stats["detected_nozzle_temps"].add(temp_match.group(1))

        stats["detected_nozzle_temps"] = sorted(list(stats["detected_nozzle_temps"]))
        return json.dumps(stats, indent=2)
    except Exception as e:
        print(f"Analysis failed: {str(e)}", file=sys.stderr)
        return f"Error analyzing file: {str(e)}"

@mcp.tool()
def inject_m1004_at_feature(file_path: str, feature_type: str, command_s_value: int) -> str:
    """
    Injects M1004 commands before specific features, strictly within the print data section.
    Automatically injects M1004 S0 at the end of the feature section.
    """
    if not os.path.exists(file_path):
        return "Error: File not found."

    modified_lines: List[str] = []
    changes_made = 0
    target_feature = feature_type.lower()
    in_target_feature = False
    is_in_data_section = False

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        for line in lines:
            line_lower = line.lower()
            
            # Identify the start of the actual print movements
            if not is_in_data_section:
                if ";layer_change" in line_lower or "g28" in line_lower or ";layer:1" in line_lower:
                    is_in_data_section = True
                modified_lines.append(line)
                continue
            
            # Detect start of target feature (within data section)
            if target_feature in line_lower and not in_target_feature:
                modified_lines.append(f"M1004 S{command_s_value} ; AI MCP: Triggered for {feature_type}\n")
                modified_lines.append(line)
                in_target_feature = True
                changes_made += 1
            # Detect end of feature
            elif in_target_feature and (';type:' in line_lower and target_feature not in line_lower):
                modified_lines.append(f"M1004 S0 ; AI MCP: Feature end - turning off\n")
                modified_lines.append(line)
                in_target_feature = False
                changes_made += 1
            else:
                modified_lines.append(line)
        
        if in_target_feature:
            modified_lines.append(f"M1004 S0 ; AI MCP: Feature end at EOF - turning off\n")
            changes_made += 1

        # CRITICAL FIX: Enforce Linux line endings here as well
        with open(file_path, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(modified_lines)

        return f"Success: Injected {changes_made} M1004 commands (Start/End) within the print data section."
    except Exception as e:
        print(f"Modification failed: {str(e)}", file=sys.stderr)
        return f"Error modifying file: {str(e)}"

if __name__ == "__main__":
    # Running FastMCP (this automatically handles standard input/output for JSON-RPC)
    mcp.run()
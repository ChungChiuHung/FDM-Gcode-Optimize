import os
import sys
from typing import List

# The inject_m1004_at_feature function implementation
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

        # Enforce Linux line endings
        with open(file_path, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(modified_lines)

        return f"Success: Injected {changes_made} M1004 commands (Start/End) within the print data section."
    except Exception as e:
        return f"Error modifying file: {str(e)}"

# Call with your parameters
file_path = r'e:\CodeHere\FDM-Optimize\gear-upper-cap-v02_PLA_2h51m.gcode'
feature_type = 'outer wall'
command_s_value = 1

result = inject_m1004_at_feature(file_path, feature_type, command_s_value)
print(result)

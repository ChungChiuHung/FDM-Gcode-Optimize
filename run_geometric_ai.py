import sys
import os
from gcode_parser import GCodeParser
from path_optimizer import PathOptimizer
from e_value_simulator import EValueSimulator
from gcode_collision_checker import GCodeCollisionChecker  # <-- 1. Import the new sandbox

def run_hybrid_pipeline(input_file: str):
    if not os.path.exists(input_file):
        print(f"Error: Could not find {input_file}")
        return

    output_file = input_file.replace(".gcode", "_test_optimized.gcode")
    if output_file == input_file:
        output_file += "_test_optimized.gcode"
        
    print("\n=== STARTING HYBRID CLUSTER-SWEEP AI TEST ===")

    # 1. Parse using your updated parser
    parser = GCodeParser()
    if not parser.parse_file(input_file):
        print("Error: Parsing failed.")
        return

    path_opt = PathOptimizer()
    e_sim = EValueSimulator()

    # 2. Rebuild and Optimize
    with open(output_file, 'w', encoding='utf-8', newline='\n') as out_f:
        out_f.write("".join(parser.header_lines))
        
        for i, layer_moves in enumerate(parser.layers):
            print(f"-> Processing Layer {i+1}/{len(parser.layers)}...")
            
            # Execute the new Advanced Hybrid Algorithm
            optimized_islands = path_opt.hybrid_sort(layer_moves)
            rebuilt_gcode = e_sim.simulate_and_rebuild(optimized_islands)
            
            out_f.write(f"\n; === AI HYBRID OPTIMIZED LAYER {i+1} ===\n")
            out_f.write(rebuilt_gcode)

        out_f.write("".join(parser.footer_lines))

    print(f"\n=== SUCCESS! Saved to: {output_file} ===")

    # 3. AUTOMATED CLOSED-LOOP AUDIT
    # Instantly verify the safety of the file we just created!
    print("\n=== INITIATING AUTOMATED COLLISION AUDIT ===")
    checker = GCodeCollisionChecker()
    checker.run_check(output_file)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_hybrid_pipeline(sys.argv[1])
    else:
        print("Usage: python run_geometric_ai.py <path_to_gcode>")
from gcode_parser import GCodeParser

def analyze_print_travels(gcode_path):
    # 1. Initialize the parser
    parser = GCodeParser()
    
    # 2. Parse the file into a 3D mathematical array
    success = parser.parse_file(gcode_path)
    if not success:
        return

    # 3. Now you have access to `parser.layers`
    # Let's find the longest, most dangerous travel move that might cause stringing
    longest_travel = 0.0
    dangerous_layer = 0
    
    for layer_idx, layer_moves in enumerate(parser.layers):
        for move in layer_moves:
            if move['type'] == 'travel':
                if move['distance'] > longest_travel:
                    longest_travel = move['distance']
                    dangerous_layer = layer_idx

    print(f"Warning: Found a massive travel move of {longest_travel:.2f}mm on Layer {dangerous_layer}!")
    print("This is where you would apply a Convex Hull algorithm to bend the path.")

if __name__ == "__main__":
    analyze_print_travels("test.gcode")
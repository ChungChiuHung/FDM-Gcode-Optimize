import os
import shutil
import tempfile
import sys

# --- CRITICAL FIX: Add Project Root to sys.path ---
# This ensures Python can always find the 'src' module regardless of how uvicorn is launched
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse
from src.core.pipeline import auto_optimize_gcode

app = FastAPI(title="Geometric AI WebUI")

# 1. Serve the 3D Viewer Frontend
@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    """Serves the 3D Viewer frontend from the parent directory."""
    # FIX: Go up TWO directories (from src/ -> h2c-vibe-suite/ -> FDM-Optimize/)
    # and look for the updated gcode_viewer_3d.html file.
    viewer_path = os.path.join(PROJECT_ROOT, "..", "gcode_viewer_3d.html")
    
    # Fallback just in case it's still named gcode_viewer.html
    if not os.path.exists(viewer_path):
        viewer_path = os.path.join(PROJECT_ROOT, "..", "gcode_viewer.html")
        
    with open(viewer_path, "r", encoding="utf-8") as f:
        return f.read()

# 2. Expose the Optimization Pipeline Endpoint
@app.post("/api/optimize")
async def optimize_endpoint(file: UploadFile = File(...)):
    """Receives G-code from the browser, runs the physics engine, and returns it."""
    
    # Create a secure temporary directory for the upload
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, file.filename)
        
        # Save the uploaded file to disk
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Run your enterprise pipeline (Atomic I/O modifies the file in place)
        auto_optimize_gcode(input_path)
        
        # Read the newly optimized G-code back into memory
        with open(input_path, "r", encoding="utf-8") as f:
            optimized_content = f.read()
            
        # Send it back to the browser for 3D rendering
        return {
            "filename": file.filename, 
            "optimized_gcode": optimized_content
        }

# --- CRITICAL FIX: Programmatic Startup ---
if __name__ == "__main__":
    import uvicorn
    print("\n[*] Starting Geometric AI Web Server...")
    print("[*] Open your browser to: http://localhost:8000\n")
    # Run the server directly from Python
    uvicorn.run("src.web_server:app", host="0.0.0.0", port=8000)
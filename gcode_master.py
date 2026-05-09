import sys
import os
import logging
import traceback

# 1. Grab the file path from Bambu Studio BEFORE doing anything else
if len(sys.argv) > 1:
    gcode_path = sys.argv[1]
else:
    print("Error: No file path provided by the slicer.")
    sys.exit(1)

# 2. Force Python to look in the script's actual directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

# 3. Set up Centralized Logging
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True) 

base_name = os.path.basename(gcode_path)
log_file = os.path.join(LOG_DIR, f"{base_name}.log")

logger = logging.getLogger("H2C_Pipeline")
logger.setLevel(logging.DEBUG)

# CRITICAL FIX 1: Stop propagation to the hidden Slicer Root Logger!
# This prevents our messages from bubbling up and echoing back through stderr.
logger.propagate = False

# Save the original console outputs before hijacking them
original_stdout = sys.stdout
original_stderr = sys.stderr

if not logger.handlers:
    # File Handler (Always On - Safely writes to disk)
    fh = logging.FileHandler(log_file, mode='w', encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s | %(levelname)8s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    
    # CRITICAL BUG FIX: Only attach the Console StreamHandler if running manually in a real terminal!
    # Bambu Studio runs in the background and closes the console pipe. Trying to write to it causes 
    # a BrokenPipeError, which triggers a logging recursion loop.
    if sys.stdout.isatty():
        ch = logging.StreamHandler(original_stdout) 
        ch.setLevel(logging.INFO)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

# =====================================================================
# IMPROVED Stream Interceptor
# Safely catches print() without infinite recursion
# =====================================================================
class StreamToLogger(object):
    def __init__(self, logger_obj, log_level=logging.INFO):
        self.logger = logger_obj
        self.log_level = log_level
        self._is_logging = False

    def write(self, buf):
        # CRITICAL FIX 2: The Recursion Guard Lock
        if self._is_logging:
            return
            
        self._is_logging = True
        try:
            # Process incoming string buffer safely
            if not isinstance(buf, str):
                try:
                    buf = str(buf, 'utf-8')
                except Exception:
                    buf = str(buf)
            
            for line in buf.splitlines():
                clean_line = line.strip()
                if clean_line:  # Avoid logging completely empty lines
                    self.logger.log(self.log_level, clean_line)
        finally:
            self._is_logging = False

    def flush(self):
        pass

# Hijack the standard outputs
sys.stdout = StreamToLogger(logger, logging.INFO)
sys.stderr = StreamToLogger(logger, logging.ERROR)
# =====================================================================


# 4. Import the Optimizer AFTER setting up logging path
try:
    import gcode_optimizer
    logger.info("Successfully imported gcode_optimizer.py (Geometric AI Engine)")
except Exception as e:
    logger.error(f"CRITICAL: Failed to import gcode_optimizer.py")
    logger.error(traceback.format_exc())
    sys.exit(1)

# 5. Main Execution Pipeline
def main(file_path):
    if not os.path.exists(file_path):
        logger.error(f"Critical Error: G-code file not found at {file_path}")
        sys.exit(1)

    try:
        logger.info("==================================================")
        logger.info("   STARTING H2C GEOMETRIC AI PIPELINE             ")
        logger.info("==================================================")
        logger.info(f"Target File: {file_path}")
        logger.info("--> Executing gcode_optimizer.auto_optimize_gcode()...")
        
        opt_success = gcode_optimizer.auto_optimize_gcode(file_path)
        
        if not opt_success:
            logger.error("gcode_optimizer.auto_optimize_gcode() failed. Aborting pipeline.")
            sys.exit(1)

        logger.info("==================================================")
        logger.info("   PIPELINE FINISHED SUCCESSFULLY                 ")
        logger.info("==================================================")
        sys.exit(0)

    except Exception as e:
        logger.error("!!! CRITICAL PIPELINE CRASH !!!")
        logger.error(f"Exception Type: {type(e).__name__}")
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main(gcode_path)
import sys
import os
import logging
import traceback


class StreamToLogger:
    """Redirect print() to the logger without recursion."""
    def __init__(self, logger_obj, log_level=logging.INFO):
        self.logger = logger_obj
        self.log_level = log_level
        self._is_logging = False

    def write(self, buf):
        if self._is_logging:
            return
        self._is_logging = True
        try:
            if not isinstance(buf, str):
                try:
                    buf = str(buf, 'utf-8')
                except Exception:
                    buf = str(buf)
            for line in buf.splitlines():
                clean_line = line.strip()
                if clean_line:
                    self.logger.log(self.log_level, clean_line)
        finally:
            self._is_logging = False

    def flush(self):
        pass


def setup_logging(gcode_path: str) -> logging.Logger:
    """Configure file + optional console logging; mutate sys.path for imports."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    log_dir = os.path.join(project_root, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{os.path.basename(gcode_path)}.log")

    logger = logging.getLogger("H2C_Pipeline")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # isolate from any slicer root logger

    if not logger.handlers:
        fmt = logging.Formatter(
            '%(asctime)s | %(levelname)8s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        fh = logging.FileHandler(log_file, mode='w', encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        # sys.__stdout__ is the true OS console even if Bambu Studio has already
        # replaced sys.stdout — avoids recursion through StreamToLogger.
        if sys.__stdout__ and sys.__stdout__.isatty():
            ch = logging.StreamHandler(sys.__stdout__)
            ch.setLevel(logging.INFO)
            ch.setFormatter(fmt)
            logger.addHandler(ch)

    return logger


def main():
    if len(sys.argv) < 2:
        print("Error: No file path provided by the slicer.")
        sys.exit(1)

    gcode_path = sys.argv[1]
    logger = setup_logging(gcode_path)

    # Redirect print() from pipeline and plugins into the logger.
    sys.stdout = StreamToLogger(logger, logging.INFO)
    sys.stderr = StreamToLogger(logger, logging.ERROR)

    # Import pipeline AFTER logger is live so any SyntaxError / ImportError
    # in pipeline.py or its dependencies lands in the log file.
    try:
        from src.core import pipeline
        logger.info("Successfully imported pipeline.py (Geometric AI Engine)")
    except Exception:
        logger.error("CRITICAL: Failed to import pipeline.py")
        logger.error(traceback.format_exc())
        sys.exit(1)

    if not os.path.exists(gcode_path):
        logger.error(f"Critical Error: G-code file not found at {gcode_path}")
        sys.exit(1)

    try:
        logger.info("==================================================")
        logger.info("   STARTING H2C GEOMETRIC AI PIPELINE             ")
        logger.info("==================================================")
        logger.info(f"Target File: {gcode_path}")
        logger.info("--> Executing pipeline.auto_optimize_gcode()...")

        # is_heavy_toolhead=True explicitly activates the Joules-based
        # AntiResonanceBrake and M204 inertia dampening for the 450 g toolhead.
        ok = pipeline.auto_optimize_gcode(gcode_path, is_heavy_toolhead=True)

        if not ok:
            logger.error("pipeline.auto_optimize_gcode() failed. Aborting pipeline.")
            sys.exit(1)

        logger.info("==================================================")
        logger.info("   PIPELINE FINISHED SUCCESSFULLY                 ")
        logger.info("==================================================")
        sys.exit(0)

    except Exception:
        logger.error("!!! CRITICAL PIPELINE CRASH !!!")
        logger.error(f"Exception Type: {type(sys.exc_info()[1]).__name__}")
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
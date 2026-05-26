import os
import tempfile
from contextlib import contextmanager


class AtomicGCodeWriter:
    """Provides atomic file writes to prevent G-code corruption on crash."""

    @staticmethod
    @contextmanager
    def atomic_write(final_file_path: str, encoding: str = 'utf-8'):
        """
        Context manager that writes to a temp file and atomically replaces the
        target path via os.replace() only on clean exit.

        The temp file is created in the same directory as the target so that
        os.replace() is guaranteed to be an atomic same-filesystem rename.

        Usage:
            with AtomicGCodeWriter.atomic_write(path) as fh:
                fh.write(content)
        """
        dir_name = os.path.dirname(os.path.abspath(final_file_path))
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.atomic.tmp')
        try:
            with os.fdopen(fd, 'w', encoding=encoding, newline='\n') as fh:
                yield fh
            # Reached only if the body exited cleanly — atomically promote.
            os.replace(tmp_path, final_file_path)
        except BaseException:
            # On any error (including KeyboardInterrupt), discard the partial file.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

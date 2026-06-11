import re
import json
import os
from typing import Dict, Generator, Iterable, List, Optional

# Path to machine_profiles/ relative to this module (3 levels up from src/core/ → project root).
_PROFILES_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'machine_profiles')
)

_RE_G01_CMD = re.compile(r'^\s*G[01]\b', re.IGNORECASE)
_RE_E_VAL   = re.compile(r'\bE([+\-]?[\d.]+)', re.IGNORECASE)
_RE_XY_AXIS = re.compile(r'\b[XY][+\-]?[\d.]',  re.IGNORECASE)


class VolumetricWipeInjector:
    """
    Streaming generator filter that tracks cumulative relative filament extrusion
    (M83 mode) and injects a machine-specific nozzle-wipe macro before the first
    qualifying pure-XY travel move once the volumetric threshold is crossed.

    Non-Destructive Routing guarantee: the original slicer travel line is always
    emitted AFTER the wipe macro so the toolhead returns to the exact coordinate
    the slicer intended — zero coordinate math required on our side.
    """

    VOLUMETRIC_WIPE_MM: float = 15_000.0  # 15 metres of filament

    def __init__(self, profile: Dict, wipe_mm: Optional[float] = None) -> None:
        self._macro:   List[str] = profile.get('wipe_macro', [])
        self._wipe_mm: float     = wipe_mm if wipe_mm is not None else self.VOLUMETRIC_WIPE_MM
        self.cumulative_extrusion: float = 0.0
        self.needs_wipe: bool            = False

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_profile_path(cls, path: str, wipe_mm: Optional[float] = None) -> 'VolumetricWipeInjector':
        with open(path, 'r', encoding='utf-8') as fh:
            return cls(json.load(fh), wipe_mm=wipe_mm)

    # ------------------------------------------------------------------
    # Core streaming generator
    # ------------------------------------------------------------------

    def process_lines(self, lines: Iterable[str]) -> Generator[str, None, None]:
        """
        Yield every input line, inserting the wipe macro before the first
        qualifying travel move after the volumetric threshold is crossed.

        Qualifying travel: G0/G1 line that contains X or Y but NO E value.
        Threshold: cumulative positive E-delta >= wipe_mm.
        """
        for line in lines:
            bare = line.rstrip('\r\n')

            if _RE_G01_CMD.match(bare):
                e_match = _RE_E_VAL.search(bare)
                if e_match:
                    # Extrusion move: track positive E delta.
                    e_delta = float(e_match.group(1))
                    if e_delta > 0:
                        self.cumulative_extrusion += e_delta
                        if (not self.needs_wipe
                                and self.cumulative_extrusion >= self._wipe_mm
                                and self._macro):
                            self.needs_wipe = True
                    yield bare + '\n'
                else:
                    # G0/G1 with no E — potential travel move.
                    if (self.needs_wipe and _RE_XY_AXIS.search(bare)):
                        yield '; --- AI Volumetric Wipe: threshold reached ---\n'
                        yield 'M400\n'
                        for macro_line in self._macro:
                            yield macro_line.rstrip('\r\n') + '\n'
                        self.cumulative_extrusion = 0.0
                        self.needs_wipe = False
                    yield bare + '\n'
            else:
                yield bare + '\n'

    # ------------------------------------------------------------------
    # Pipeline integration helper
    # ------------------------------------------------------------------

    def make_filtered_writer(self, file_handle: object) -> '_FilteredWriter':
        """Return a file-proxy that transparently pipes all writes through this injector."""
        return _FilteredWriter(file_handle, self)


class _FilteredWriter:
    """
    Drop-in file-handle proxy: buffers partial writes, splits on newlines, and
    pipes each complete line through VolumetricWipeInjector.process_lines().
    """

    def __init__(self, fh: object, injector: VolumetricWipeInjector) -> None:
        self._fh  = fh
        self._inj = injector
        self._buf = ''

    def write(self, text: str) -> None:
        combined = self._buf + text
        lines    = combined.splitlines(keepends=True)
        if lines and not lines[-1].endswith(('\n', '\r')):
            self._buf = lines.pop()
        else:
            self._buf = ''
        for out_line in self._inj.process_lines(lines):
            self._fh.write(out_line)

    def flush(self) -> None:
        if self._buf:
            for out_line in self._inj.process_lines([self._buf]):
                self._fh.write(out_line)
            self._buf = ''
        self._fh.flush()

    def __getattr__(self, name: str):
        return getattr(self._fh, name)


# ------------------------------------------------------------------
# Utility: auto-detect machine profile from slicer header string
# ------------------------------------------------------------------

def load_machine_profile(machine_type: str, profiles_dir: str = _PROFILES_DIR) -> Optional[Dict]:
    """
    Scan profiles_dir for JSON files whose stem (stripped of '_profile' suffix)
    is a substring of machine_type (case-insensitive).  Returns the first match
    or None if the directory doesn't exist or no profile matches.
    """
    if not os.path.isdir(profiles_dir):
        return None
    machine_lower = machine_type.lower()
    for fname in sorted(os.listdir(profiles_dir)):
        if not fname.lower().endswith('.json'):
            continue
        stem = fname.lower()[:-5]               # strip .json
        if stem.endswith('_profile'):
            stem = stem[:-8]                    # strip _profile
        if len(stem) >= 2 and stem in machine_lower:
            path = os.path.join(profiles_dir, fname)
            try:
                with open(path, 'r', encoding='utf-8') as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass
    return None

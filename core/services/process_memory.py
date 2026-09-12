"""Resident memory of the current process, for per-stage evidence in the
processing journey.

On Linux the peak is per attempt: ``reset_peak`` clears the kernel's
high-water mark when an attempt begins and ``peak_rss_mb`` reads it back
from ``/proc``. Elsewhere the peak falls back to ``getrusage``, which is a
process-lifetime high-water mark that a long-lived worker carries from
earlier jobs. Current RSS comes from ``/proc`` on Linux and from psutil
where it is installed."""

import resource
import sys
from typing import Optional

_MB = 1024 * 1024


def reset_peak() -> None:
    """Start a fresh high-water mark for the current attempt (Linux only)."""
    try:
        with open("/proc/self/clear_refs", "w") as clear_refs:
            clear_refs.write("5")
    except OSError:
        pass


def _proc_status_kb(field: str) -> Optional[int]:
    try:
        with open("/proc/self/status") as status:
            for line in status:
                if line.startswith(field + ":"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def peak_rss_mb() -> float:
    high_water = _proc_status_kb("VmHWM")
    if high_water is not None:
        return round(high_water * 1024 / _MB, 1)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes, macOS bytes.
    return round((peak * 1024 if sys.platform != "darwin" else peak) / _MB, 1)


def current_rss_mb() -> Optional[float]:
    resident = _proc_status_kb("VmRSS")
    if resident is not None:
        return round(resident * 1024 / _MB, 1)
    try:
        import psutil
    except ImportError:
        return None
    return round(psutil.Process().memory_info().rss / _MB, 1)

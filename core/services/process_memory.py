"""Resident memory of the current process, for per-stage evidence in the
processing journey. Peak comes from ``getrusage`` and is a process-lifetime
high-water mark; current RSS is read from ``/proc`` on Linux and from psutil
elsewhere when it is installed."""

import resource
import sys
from typing import Optional

_MB = 1024 * 1024


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes, macOS bytes.
    return round((peak * 1024 if sys.platform != "darwin" else peak) / _MB, 1)


def current_rss_mb() -> Optional[float]:
    try:
        with open("/proc/self/statm") as statm:
            resident_pages = int(statm.read().split()[1])
        return round(resident_pages * resource.getpagesize() / _MB, 1)
    except (OSError, ValueError, IndexError):
        pass
    try:
        import psutil
    except ImportError:
        return None
    return round(psutil.Process().memory_info().rss / _MB, 1)

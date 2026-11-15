"""Read resource counters exposed to the API on Linux."""
from datetime import datetime, timezone
from pathlib import Path
from shutil import disk_usage


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii")
    except OSError:
        return ""


def resource_pressure(proc: Path = Path("/proc"), cgroup: Path = Path("/sys/fs/cgroup"),
                      data: Path = Path("/data")) -> dict:
    logical_cpus = sum(line.partition(" ")[0][3:].isdigit()
                       for line in _read(proc / "stat").splitlines() if line.startswith("cpu"))
    load = _read(proc / "loadavg").split()
    load_5m = float(load[1]) if len(load) >= 2 else None
    memory = {}
    for line in _read(proc / "meminfo").splitlines():
        key, _, value = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            memory[key] = int(value.split()[0]) * 1024
    try:
        disk = disk_usage(data)
    except OSError:
        disk = None
    used = _read(cgroup / "memory.current").strip()
    limit = _read(cgroup / "memory.max").strip()
    quota = _read(cgroup / "cpu.max").split()
    cpu_limit = int(quota[0]) / int(quota[1]) if len(quota) == 2 and quota[0].isdigit() else None
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "vm": {"logical_cpus": logical_cpus or None, "load_5m": load_5m,
               "memory_total": memory.get("MemTotal"), "memory_available": memory.get("MemAvailable")},
        "app_disk": {"total": disk.total, "available": disk.free} if disk else None,
        "api_container": {"memory_current": int(used) if used.isdigit() else None,
                          "memory_limit": int(limit) if limit.isdigit() else None,
                          "cpu_limit": cpu_limit},
    }

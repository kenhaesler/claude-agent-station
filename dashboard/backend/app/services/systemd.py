"""Systemd service control via subprocess with action whitelist."""

import asyncio
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_UNITS = {"claude-agent.service", "claude-agent.timer"}
ALLOWED_ACTIONS = {"start", "stop", "restart", "status", "enable", "disable"}


class SystemdError(Exception):
    pass


def _run_systemctl(action: str, unit: str) -> subprocess.CompletedProcess:
    """Run a systemctl command synchronously."""
    if unit not in ALLOWED_UNITS:
        raise SystemdError(f"Unit not allowed: {unit}")
    if action not in ALLOWED_ACTIONS:
        raise SystemdError(f"Action not allowed: {action}")

    cmd = ["sudo", "systemctl", action, unit]
    # For oneshot services, 'start' blocks until the service exits.
    # Use --no-block so the API returns immediately.
    if action in ("start", "restart"):
        cmd.insert(3, "--no-block")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=10,
    )


async def systemctl(action: str, unit: str) -> dict[str, Any]:
    """Run systemctl command asynchronously."""
    try:
        result = await asyncio.to_thread(_run_systemctl, action, unit)
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except SystemdError as e:
        return {"success": False, "error": str(e)}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def get_service_status() -> dict[str, Any]:
    """Get status of both service and timer."""
    service_result = await systemctl("status", "claude-agent.service")
    timer_result = await systemctl("status", "claude-agent.timer")

    service_stdout = service_result.get("stdout", "")
    service_active = "active (running)" in service_stdout or \
                     "active (exited)" in service_stdout or \
                     "activating" in service_stdout
    timer_active = "active (waiting)" in timer_result.get("stdout", "") or \
                   "active (running)" in timer_result.get("stdout", "")

    # Extract next trigger time from timer status
    timer_next = None
    for line in timer_result.get("stdout", "").split("\n"):
        if "Trigger:" in line:
            timer_next = line.split("Trigger:", 1)[1].strip()
            break

    return {
        "service_active": service_active,
        "timer_active": timer_active,
        "timer_next": timer_next,
        "service_stdout": service_result.get("stdout", ""),
        "timer_stdout": timer_result.get("stdout", ""),
    }


async def get_system_resources() -> dict[str, Any]:
    """Get system resource info without psutil."""
    info: dict[str, Any] = {}

    # Memory from /proc/meminfo
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]  # value in kB
                    meminfo[key] = int(val)
            total = meminfo.get("MemTotal", 0)
            available = meminfo.get("MemAvailable", 0)
            info["memory_total_mb"] = round(total / 1024, 1)
            info["memory_available_mb"] = round(available / 1024, 1)
            info["memory_used_mb"] = round((total - available) / 1024, 1)
    except Exception:
        pass

    # Load average from /proc/loadavg
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().strip().split()
            info["load_avg"] = [float(parts[0]), float(parts[1]), float(parts[2])]
    except Exception:
        pass

    # Disk usage
    try:
        import shutil
        usage = shutil.disk_usage("/")
        info["disk_total_gb"] = round(usage.total / (1024 ** 3), 1)
        info["disk_free_gb"] = round(usage.free / (1024 ** 3), 1)
        info["disk_used_gb"] = round(usage.used / (1024 ** 3), 1)
    except Exception:
        pass

    # Uptime
    try:
        with open("/proc/uptime") as f:
            info["uptime_seconds"] = float(f.read().split()[0])
    except Exception:
        pass

    return info

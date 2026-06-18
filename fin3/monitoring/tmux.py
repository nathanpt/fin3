"""tmux detection and pane management helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import structlog

logger = structlog.get_logger(__name__)


def is_in_tmux() -> bool:
    """Return True if the current process is running inside a tmux session."""
    return bool(os.environ.get("TMUX"))


def _tmux_available() -> bool:
    """Return True if the tmux binary is on PATH."""
    return shutil.which("tmux") is not None


def create_monitor_pane(metrics_file: str, width: int = 35) -> str | None:
    """Create a tmux split pane for the live monitor.

    Parameters
    ----------
    metrics_file : str
        Path to the JSON metrics file the display script will tail.
    width : int
        Pane width as a percentage of the current window.

    Returns
    -------
    str or None
        The pane ID (``%N``) on success, ``None`` if tmux is unavailable
        or pane creation fails.
    """
    if not is_in_tmux() or not _tmux_available():
        return None

    # Use sys.executable so the monitor pane runs under the same interpreter
    # (and venv) as the parent process — critical when fin3 is not installed
    # system-wide and depends on a project venv.
    py = sys.executable
    cmd = [
        "tmux", "split-window", "-h",
        "-p", str(width),
        f"{py} -m fin3.monitoring.display {metrics_file}",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            logger.warning("tmux.create_pane_failed", stderr=result.stderr.strip())
            return None
        # The pane ID is printed to stdout by tmux when using -P flag,
        # but split-window doesn't always print it. Query for the active pane.
        pane_id = _get_active_pane()
        logger.info("tmux.pane_created", pane_id=pane_id)
        return pane_id
    except Exception as exc:
        logger.warning("tmux.create_pane_error", error=str(exc))
        return None


def _get_active_pane() -> str | None:
    """Return the ID of the currently active pane (the newly created one)."""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{pane_id}"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def kill_pane(pane_id: str | None) -> None:
    """Kill a tmux pane by ID. Silently succeeds if pane_id is None."""
    if pane_id is None:
        return
    if not _tmux_available():
        return
    try:
        subprocess.run(
            ["tmux", "kill-pane", "-t", pane_id],
            capture_output=True, timeout=3,
        )
        logger.info("tmux.pane_killed", pane_id=pane_id)
    except Exception as exc:
        logger.warning("tmux.kill_pane_error", pane_id=pane_id, error=str(exc))

"""Guard the verification control script itself.

``scripts/verify.py`` is the artifact a reviewer re-runs to check fin3's
end-to-end claims; if it rots (broken import, bad argparse, unparseable JSON),
every downstream "verified" claim loses its evidence. These tests keep it
healthy: the offline `all` set must pass on a clean tree, and its default
output must be exactly one parseable JSON value.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify.py"


def _run_verify(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_verify_all_passes_on_healthy_tree_and_emits_parseable_json():
    """verify.py all: green on the committed tree, output is one JSON value."""
    proc = _run_verify("all")
    assert proc.returncode == 0, (
        f"verify.py all failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )
    report = json.loads(proc.stdout)
    assert report["failed"] == 0
    assert {c["name"] for c in report["checks"]} == {"cli", "plan", "idempotent"}
    assert all(c["outcome"] == "pass" for c in report["checks"])
    assert all(
        {"assert", "ok", "actual"} <= set(a)
        for c in report["checks"]
        for a in c["assertions"]
    )


def test_verify_store_without_minio_reports_failed_check_not_crash():
    """A broken environment yields a failed check with a fix, never a traceback."""
    proc = _run_verify("store")
    # store may legitimately pass if MinIO is reachable; either way the script
    # must exit cleanly and emit parseable JSON (0 = pass, 1 = failed check).
    assert proc.returncode in (0, 1)
    report = json.loads(proc.stdout)
    assert report["checks"][0]["name"] == "store"
    for assertion in report["checks"][0]["assertions"]:
        if not assertion["ok"]:
            assert "fix" in assertion, f"failure without fix hint: {assertion}"

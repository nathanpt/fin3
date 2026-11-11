#!/usr/bin/env python3
"""fin3 verification control script.

Drives fin3's real user surfaces -- the installed ``fin3`` CLI process and the
``MarketDataFetcher`` library API -- and asserts observable behavior:

- ``cli``        real ``uv run fin3`` process: help, JSON error envelopes,
                 exit codes 0/1/2 (offline; the failure paths are forced with
                 env overrides and a malformed manifest).
- ``plan``       declarative-manifest planning over an ephemeral LMDB store:
                 covered range -> skip, gap -> update with fetch ranges,
                 ``sync --dry-run`` writes nothing.
- ``idempotent`` ``get_data`` over a fully covered range is a read-only
                 no-op: canonical frame back, no new storage version, no
                 provider fetch. LMDB; the metadata cache is pre-seeded so no
                 provider API is ever touched.
- ``store``      read-only ``fin3 status`` against the configured MinIO
                 (credentials come from ``.env``/environment). Never fetches
                 market data; asserts the read-only command creates no bucket.
- ``all``        cli + plan + idempotent (the offline set; ``store`` is
                 separate because it needs a reachable MinIO).

Checks run against ephemeral fixtures (temp LMDB directories) or read-only
paths; nothing persistent is written, so there is no ``--dry-run``.

Output is one JSON value by default; pass ``--human`` for text.

Exit codes: 0 = all passed, 1 = at least one check failed, 2 = setup error
(check the message on stderr).

Usage:
    uv run python scripts/verify.py all
    uv run python scripts/verify.py <check> [--human]
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

VERIFY_SYMBOL = "VERIFY"


def _seed_sessions() -> pd.DatetimeIndex:
    """XNYS session dates (UTC midnight) in the seeded window.

    Exchange assets chunk gap detection per session, so one bar per session
    is exactly "fully covered" for a 1d library. Crypto is NOT usable here:
    its gap chunks are hourly, so daily bars never cover a range.
    """
    import exchange_calendars as ec

    cal = ec.get_calendar("XNYS")
    sessions = cal.sessions_in_range("2026-01-01", "2026-01-10")
    return pd.DatetimeIndex([pd.Timestamp(s).tz_localize("UTC") for s in sessions])


SEED_DAYS = pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC")


# ---------------------------------------------------------------------------
# Check plumbing
# ---------------------------------------------------------------------------


def _jsonsafe(value: Any) -> Any:
    """Coerce assertion evidence to JSON-serializable types."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (set, frozenset, tuple)):
        return [_jsonsafe(v) for v in value]
    if isinstance(value, list):
        return [_jsonsafe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonsafe(v) for k, v in value.items()}
    return str(value)


@dataclass
class CheckResult:
    """Outcome of one check: named assertions with expected/actual evidence."""

    name: str
    assertions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(a["ok"] for a in self.assertions)

    def expect(
        self,
        what: str,
        actual: Any,
        *,
        ok: bool,
        fix: str = "",
        expected: Any = None,
    ) -> None:
        entry: dict[str, Any] = {
            "assert": what,
            "ok": ok,
            "actual": _jsonsafe(actual),
        }
        if expected is not None:
            entry["expected"] = _jsonsafe(expected)
        if not ok and fix:
            entry["fix"] = fix
        self.assertions.append(entry)


def _seed_ohlcv(index: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """Canonical per-symbol OHLCV frame: float64 columns, UTC index."""
    idx = SEED_DAYS if index is None else index
    n = len(idx)
    base = [100.0 + i for i in range(n)]
    return pd.DataFrame(
        {
            "open": base,
            "high": [b + 1.0 for b in base],
            "low": [b - 1.0 for b in base],
            "close": [b + 0.5 for b in base],
            "volume": [1000.0 + i for i in range(n)],
        },
        index=idx,
    )


def _write_yaml(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


def _manifest_yaml(
    manifest_id: str,
    start: str,
    end: str,
    *,
    symbol: str = "VERIFYUSDT",
    resolution: str = "1d",
) -> str:
    return (
        "version: 1\n"
        f"id: {manifest_id}\n"
        "libraries:\n"
        "  verify:\n"
        "    asset_type: crypto\n"
        "    provider: binance\n"
        f"    resolution: {resolution}\n"
        "    symbols:\n"
        f"      include: [{symbol}]\n"
        f"    start: {start}\n"
        f"    end: {end}\n"
        "    update_policy: incremental\n"
    )


def _run_fin3(
    args: list[str], env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the real installed fin3 CLI process from the repo root."""
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["uv", "run", "fin3", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _parse_json_envelope(proc: subprocess.CompletedProcess[str]) -> Any:
    return json.loads(proc.stdout)


def _version_count(storage: Any, library: str, symbol: str) -> int:
    """Number of stored versions for a symbol (proves writes did/didn't happen)."""
    lib = storage.arctic_for(library).get_library(library)
    return len(lib.list_versions(symbol))


def _lmdb_setup(tmp: Path) -> tuple[Any, Any]:
    """Ephemeral LMDB store seeded with VERIFY equity bars + metadata cache entry.

    equity_us/1d: gap detection chunks per XNYS session, so a bar per session
    is a genuinely covered range. The metadata cache is pre-seeded so
    bootstrap_metadata() takes the cache path and never calls the provider
    (which would hit the network).
    """
    from fin3.config.settings import (
        BinanceConfig,
        ClientConfig,
        DatabentoConfig,
        LockConfig,
        MinioConfig,
    )
    from fin3.metadata.asset_profile import MetadataStore
    from fin3.storage.arctic import ArcticStorage

    storage = ArcticStorage.from_lmdb(str(tmp / "lmdb"))
    # Daily bars for the manifest planner (crypto plans per UTC day); session
    # bars for get_data (exchange gap detection chunks per session).
    storage.write("crypto-1d-binance", "VERIFYUSDT", _seed_ohlcv())
    sessions = _seed_sessions()
    storage.write("equities-1d-databento", VERIFY_SYMBOL, _seed_ohlcv(sessions))
    MetadataStore(storage).set_lifecycle_bounds(VERIFY_SYMBOL, None, None)
    config = ClientConfig(
        minio=MinioConfig(endpoint="lmdb.invalid:0", access_key="x", secret_key="y"),
        providers={
            "databento": DatabentoConfig(api_key="verify-not-a-real-key"),
            "binance": BinanceConfig(provider_type="binance"),
        },
        lock=LockConfig(lock_dir=str(tmp / "locks")),
    )
    return storage, config


def _check_cli(tmp: Path) -> CheckResult:
    """Real-process contract: help, exit codes, JSON error envelopes."""
    res = CheckResult("cli")

    proc = _run_fin3(["--help"])
    res.expect(
        "fin3 --help exits 0",
        proc.returncode,
        ok=proc.returncode == 0,
        fix="run `uv sync --all-extras` in the repo root, then retry",
    )
    res.expect(
        "--help lists all subcommands",
        {c for c in ("ensure", "sync", "status") if c in proc.stdout},
        ok=all(c in proc.stdout for c in ("ensure", "sync", "status")),
    )

    bad = _write_yaml(tmp / "bad_manifest.yaml", "version: 99\nid: x\nlibraries: {}\n")
    proc = _run_fin3(["status", bad, "--json"])
    res.expect(
        "invalid manifest exits 2",
        proc.returncode,
        ok=proc.returncode == 2,
        fix="cli.main must return 2 for configuration/validation errors",
    )
    try:
        envelope = _parse_json_envelope(proc)
        kind = envelope.get("error", {}).get("kind")
    except (json.JSONDecodeError, AttributeError):
        kind = f"<unparseable stdout: {proc.stdout[:120]!r}>"
    res.expect(
        "invalid manifest emits configuration envelope",
        kind,
        ok=kind == "configuration",
        expected="configuration",
    )

    unknown = _run_fin3(["frobnicate"])
    res.expect(
        "unknown subcommand exits 2",
        unknown.returncode,
        ok=unknown.returncode == 2,
        fix="argparse must reject unknown subcommands with exit 2",
    )

    # Valid manifest, storage unreachable: operational failure -> exit 1 envelope.
    good = _write_yaml(
        tmp / "good_manifest.yaml",
        _manifest_yaml("verify-cli-op", "2026-01-01", "2026-01-10"),
    )
    proc = _run_fin3(
        ["status", good, "--json"],
        env_overrides={
            "FIN3_MINIO__ENDPOINT": "127.0.0.1:1",
            "FIN3_PROVIDERS__BINANCE__PROVIDER_TYPE": "binance",
            "FIN3_LOCK__LOCK_DIR": str(tmp / "locks"),
        },
    )
    res.expect(
        "unreachable storage exits 1",
        proc.returncode,
        ok=proc.returncode == 1,
        fix="cli.main must return 1 for operational (Fin3Error) failures",
    )
    try:
        envelope = _parse_json_envelope(proc)
        kind = envelope.get("error", {}).get("kind")
    except (json.JSONDecodeError, AttributeError):
        kind = f"<unparseable stdout: {proc.stdout[:120]!r}>"
    res.expect(
        "unreachable storage emits operational envelope",
        kind,
        ok=kind == "operational",
        expected="operational",
    )
    return res


def _drive_cli(args: list[str], config: Any, storage: Any) -> tuple[int, str]:
    """Run fin3.cli.main() in-process with the LMDB storage seam injected."""
    import fin3.cli as cli
    from fin3.sync import ManifestRunner

    saved_config, saved_runner = cli._load_config, cli._build_runner
    cli._load_config = lambda: config
    cli._build_runner = lambda cfg: ManifestRunner(cfg, storage=storage)
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(args)
        return rc, out.getvalue()
    finally:
        cli._load_config, cli._build_runner = saved_config, saved_runner


def _check_plan(tmp: Path) -> CheckResult:
    """Manifest planning over LMDB: coverage -> skip, gap -> update, dry-run writes nothing."""
    from fin3.schemas import library_name
    from fin3.schemas import AssetType, Resolution

    res = CheckResult("plan")
    storage, config = _lmdb_setup(tmp)
    lib = library_name(AssetType.CRYPTO, Resolution.ONE_DAY, "binance")
    plan_symbol = "VERIFYUSDT"
    seeded = storage.read(lib, plan_symbol)
    rows_before = 0 if seeded is None else len(seeded)
    versions_before = _version_count(storage, lib, plan_symbol)

    def envelope_for(args: list[str]) -> tuple[int, dict[str, Any]]:
        rc, out = _drive_cli(args, config, storage)
        return rc, json.loads(out)

    covered = _write_yaml(
        tmp / "covered.yaml",
        _manifest_yaml("verify-plan-covered", "2026-01-01", "2026-01-10"),
    )
    rc, env = envelope_for(["status", covered, "--json"])
    res.expect("status on covered manifest exits 0", rc, ok=rc == 0)
    res.expect(
        "fully covered range plans a skip",
        env["results"][0]["action"],
        ok=env["results"][0]["action"] == "skip",
        expected="skip",
        fix="planner must skip a symbol whose stored index covers the window",
    )

    gapped = _write_yaml(
        tmp / "gapped.yaml",
        _manifest_yaml("verify-plan-gap", "2026-01-01", "2026-01-20"),
    )
    rc, env = envelope_for(["status", gapped, "--json"])
    result = env["results"][0]
    fetch_start = result["fetch_ranges"][0][0] if result["fetch_ranges"] else ""
    res.expect("status on gapped manifest exits 0", rc, ok=rc == 0)
    res.expect(
        "missing tail plans a fill starting at the first uncovered day",
        (result["action"], fetch_start),
        ok=result["action"] == "fill" and fetch_start.startswith("2026-01-11"),
        expected=("fill", "2026-01-11T00:00:00+00:00"),
        fix="planner must plan exactly the uncovered tail (2026-01-11..)",
    )

    rc, env = envelope_for(["sync", gapped, "--dry-run", "--json"])
    stored = storage.read(lib, plan_symbol)
    rows_after = 0 if stored is None else len(stored)
    versions_after = _version_count(storage, lib, plan_symbol)
    res.expect("sync --dry-run exits 0", rc, ok=rc == 0)
    res.expect(
        "dry-run reports itself as dry_run",
        env["dry_run"],
        ok=env["dry_run"] is True,
        expected=True,
    )
    res.expect(
        "dry-run has no failures",
        env["totals"]["failed"],
        ok=env["totals"]["failed"] == 0,
        expected=0,
    )
    res.expect(
        "dry-run writes nothing (row count unchanged)",
        (rows_before, rows_after),
        ok=rows_before == rows_after,
        expected=f"{rows_before} == {rows_after}",
    )
    res.expect(
        "dry-run writes nothing (version count unchanged)",
        (versions_before, versions_after),
        ok=versions_before == versions_after,
        expected=f"{versions_before} == {versions_after}",
    )

    # ensure --dry-run: the creation-planning path. ensure only creates
    # missing targets; filling an existing symbol is sync's job.
    rc, env = envelope_for(["ensure", covered, "--dry-run", "--json"])
    res.expect("ensure --dry-run on covered manifest exits 0", rc, ok=rc == 0)
    res.expect(
        "ensure skips an existing symbol instead of filling it",
        (env["results"][0]["action"], env["results"][0]["reason"]),
        ok=env["results"][0]["action"] == "skip"
        and env["results"][0]["reason"] == "already_exists",
        expected=("skip", "already_exists"),
        fix="ensure must only create missing targets; filling is sync's job",
    )

    new_sym = _write_yaml(
        tmp / "new_symbol.yaml", _manifest_yaml("verify-plan-new", "2026-01-01", "2026-01-10", symbol="NEWSYM")
    )
    rc, env = envelope_for(["ensure", new_sym, "--dry-run", "--json"])
    result = env["results"][0]
    fetch_start = result["fetch_ranges"][0][0] if result["fetch_ranges"] else ""
    res.expect("ensure --dry-run on missing symbol exits 0", rc, ok=rc == 0)
    res.expect(
        "ensure plans a create with fetch ranges from the window start",
        (result["action"], result["status"], fetch_start, env["created_libraries"]),
        ok=result["action"] == "create"
        and result["status"] == "planned"
        and fetch_start.startswith("2026-01-01")
        and env["created_libraries"] == [],
        expected=("create", "planned", "2026-01-01T00:00:00+00:00", []),
        fix="ensure must plan creation of a missing symbol without writing",
    )
    res.expect(
        "ensure --dry-run creates nothing (symbol still absent)",
        storage.has_symbol(lib, "NEWSYM"),
        ok=not storage.has_symbol(lib, "NEWSYM"),
        expected=False,
        fix="dry-run must not write; the preview gate is ManifestRunner._run's `preview` branch",
    )

    new_lib = _write_yaml(
        tmp / "new_lib.yaml", _manifest_yaml("verify-plan-newlib", "2026-01-01", "2026-01-10", resolution="1h")
    )
    rc, env = envelope_for(["ensure", new_lib, "--dry-run", "--json"])
    res.expect("ensure --dry-run on absent library exits 0", rc, ok=rc == 0)
    res.expect(
        "ensure previews the library creation without creating it",
        (env["planned_libraries"], storage.has_library("crypto-1h-binance")),
        ok=env["planned_libraries"] == ["crypto-1h-binance"]
        and not storage.has_library("crypto-1h-binance"),
        expected=(["crypto-1h-binance"], False),
        fix="planned_libraries must preview ensure's library creations; dry-run creates none",
    )
    return res


def _check_idempotent(tmp: Path) -> CheckResult:
    """get_data over a covered range is a read-only no-op returning the canonical frame."""
    from fin3.core import MarketDataFetcher
    from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution

    res = CheckResult("idempotent")
    storage, config = _lmdb_setup(tmp)
    fetcher = MarketDataFetcher(config)
    fetcher._storage = storage  # documented test seam: swap MinIO backend for LMDB
    fetcher._metadata._storage = storage

    lib = "equities-1d-databento"
    args = (AssetType.EQUITY_US, "databento", Resolution.ONE_DAY, [VERIFY_SYMBOL])
    sessions = _seed_sessions()
    start, end = sessions[0].to_pydatetime(), sessions[-1].to_pydatetime()

    first = fetcher.get_data(*args, start=start, end=end)
    cols = (
        tuple(first.columns.get_level_values(1).unique())
        if hasattr(first.columns, "get_level_values")
        else ()
    )
    res.expect(
        "returns (symbol, field) MultiIndex columns over canonical OHLCV",
        cols,
        ok=set(cols) == set(OHLCV_COLUMNS),
        expected=list(OHLCV_COLUMNS),
        fix="get_data must return the canonical wide MultiIndex frame",
    )
    res.expect(
        "index is UTC",
        str(first.index.tz),
        ok=str(first.index.tz) == "UTC",
        expected="UTC",
    )
    res.expect(
        "covered range returns every seeded bar (no gaps invented)",
        len(first),
        ok=len(first) == len(sessions),
        expected=len(sessions),
    )

    versions_before = _version_count(storage, lib, VERIFY_SYMBOL)
    second = fetcher.get_data(*args, start=start, end=end)
    versions_after = _version_count(storage, lib, VERIFY_SYMBOL)
    res.expect(
        "repeat call writes no new version (read-only no-op)",
        (versions_before, versions_after),
        ok=versions_before == versions_after,
        expected=f"{versions_before} == {versions_after}",
        fix="repeated get_data over a covered range must not store anything",
    )
    res.expect(
        "repeat call returns identical data",
        first.equals(second),
        ok=first.equals(second),
        expected=True,
    )
    return res


def _check_store(tmp: Path) -> CheckResult:
    """Read-only fin3 status against the configured MinIO; no bucket created."""
    from fin3.config.settings import ClientConfig
    from fin3.storage.arctic import ArcticStorage

    res = CheckResult("store")
    manifest = _write_yaml(
        tmp / "store_manifest.yaml",
        "version: 1\n"
        "id: verify-store\n"
        "libraries:\n"
        "  verify:\n"
        "    asset_type: equity_us\n"
        "    provider: databento\n"
        "    resolution: 1d\n"
        "    symbols:\n"
        "      include: [AAPL]\n"
        "    start: 2026-01-05\n"
        "    end: 2026-01-09\n"
        "    update_policy: incremental\n",
    )
    try:
        config = ClientConfig()
    except Exception as exc:
        res.expect(
            "load config from .env/environment",
            str(exc)[:200],
            ok=False,
            fix="run from the repo root so .env with FIN3_MINIO__* is visible",
        )
        return res
    storage = ArcticStorage(config.minio)
    existed_before = storage.has_library("equities-1d-databento")

    proc = _run_fin3(["status", manifest, "--json"])
    res.expect(
        f"fin3 status against {config.minio.endpoint} exits 0",
        proc.returncode,
        ok=proc.returncode == 0,
        fix="check MinIO reachability at FIN3_MINIO__ENDPOINT; `all` runs the offline checks",
    )
    try:
        env = _parse_json_envelope(proc)
        failed, actions = env["totals"]["failed"], [r["action"] for r in env["results"]]
    except (json.JSONDecodeError, KeyError, TypeError):
        failed, actions = -1, []
    res.expect(
        "status report has no failed targets",
        failed,
        ok=failed == 0,
        expected=0,
    )
    res.expect(
        "every target plans a known action",
        actions,
        ok=bool(actions)
        and all(a in {"skip", "fill", "create", "append", "refresh"} for a in actions),
    )

    existed_after = storage.has_library("equities-1d-databento")
    res.expect(
        "read-only command creates no bucket",
        (existed_before, existed_after),
        ok=existed_before == existed_after,
        expected=f"{existed_before} == {existed_after}",
        fix="status must never create libraries; check ManifestRunner dry-run path",
    )
    return res


CHECKS = {
    "cli": _check_cli,
    "plan": _check_plan,
    "idempotent": _check_idempotent,
    "store": _check_store,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _human(res: CheckResult) -> str:
    mark = "PASS" if res.ok else "FAIL"
    lines = [f"[{mark}] {res.name}"]
    for a in res.assertions:
        bullet = "  ok  " if a["ok"] else "  FAIL"
        lines.append(f"{bullet} {a['assert']}: {a['actual']!r}")
        if not a["ok"] and a.get("fix"):
            lines.append(f"        fix: {a['fix']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify.py",
        description="fin3 end-to-end verification: drive the real CLI/library surfaces",
    )
    parser.add_argument(
        "check",
        choices=[*CHECKS, "all"],
        help="which check to run; 'all' is the offline set (cli, plan, idempotent)",
    )
    parser.add_argument(
        "--human",
        action="store_true",
        help="human-readable output instead of one JSON value",
    )
    args = parser.parse_args(argv)

    os.chdir(REPO_ROOT)  # ClientConfig reads .env relative to cwd
    names = ["cli", "plan", "idempotent"] if args.check == "all" else [args.check]

    results: list[CheckResult] = []
    # In-process libraries (structlog, rich, the Databento SDK) print to the
    # real stdout, some holding a stream reference captured at import time,
    # so contextlib.redirect_stdout is not enough: silence at fd level so the
    # JSON payload stays parseable. Subprocess checks capture their own pipes
    # and are unaffected.
    saved_fd = os.dup(1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    try:
        for name in names:
            tmp = Path(tempfile.mkdtemp(prefix=f"fin3-verify-{name}-"))
            try:
                results.append(CHECKS[name](tmp))
            except Exception as exc:  # noqa: BLE001 - report as failed check
                r = CheckResult(name)
                r.expect(
                    "check completed",
                    f"{type(exc).__name__}: {exc}",
                    ok=False,
                    fix="a crashed check means the driver is broken, not the feature",
                )
                results.append(r)
            finally:
                with contextlib.suppress(OSError):
                    for child in tmp.rglob("*"):
                        child.chmod(0o700)
                    import shutil

                    shutil.rmtree(tmp, ignore_errors=True)
    finally:
        os.dup2(saved_fd, 1)
        os.close(saved_fd)
        os.close(devnull)

    payload = {
        "tool": "verify.py",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "checks": [
            {
                "name": r.name,
                "outcome": "pass" if r.ok else "fail",
                "assertions": r.assertions,
            }
            for r in results
        ],
        "passed": sum(1 for r in results if r.ok),
        "failed": sum(1 for r in results if not r.ok),
    }
    if args.human:
        print("\n\n".join(_human(r) for r in results))
        print(f"\n{payload['passed']} passed, {payload['failed']} failed")
    else:
        print(json.dumps(payload))
    return 1 if payload["failed"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except ImportError as exc:
        print(
            f"setup error: {exc}\nfix: run via `uv run python scripts/verify.py ...`",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

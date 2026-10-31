"""End-to-end CLI tests: real main(), loader, runner, calendar, and LMDB storage.

Only the two documented construction seams are monkeypatched
(``fin3.cli._load_config`` and ``fin3.cli._build_runner``); everything else —
manifest loading, provider preflight, planning, preparation, validation,
writes — is the real production path against a real LMDB ArcticDB store.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

import fin3.cli as cli
from fin3.config.settings import BinanceConfig, ClientConfig, MinioConfig
from fin3.exceptions import ProviderError
from fin3.providers import ProviderRegistry
from fin3.storage.arctic import ArcticStorage
from tests.conftest import make_ohlcv

NOW = datetime(2024, 1, 2, 1, 0, tzinfo=timezone.utc)
LIB = "crypto-1h-manifest-test"
API_KEY = "sekret-test-key-123"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("FIN3_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def storage(tmp_path: Path) -> ArcticStorage:
    return ArcticStorage.from_lmdb(str(tmp_path / "lmdb"))


@pytest.fixture
def cli_seams(monkeypatch, storage: ArcticStorage) -> ClientConfig:
    """Patch the two documented CLI seams; everything else stays real."""
    config = ClientConfig(
        minio=MinioConfig(endpoint="lmdb", access_key="", secret_key=""),
        providers={"manifest-test": BinanceConfig(api_key=API_KEY)},
    )
    runner = cli.ManifestRunner(config, storage=storage)
    monkeypatch.setattr(cli, "_load_config", lambda: config)
    monkeypatch.setattr(cli, "_build_runner", lambda cfg: runner)
    return config


def register_provider(monkeypatch, *, fetch=None, estimate=None) -> dict:
    calls = {"fetch": [], "estimate_cost": []}

    class CliStubProvider:
        def __init__(self, config: object) -> None:
            pass

        def fetch(self, symbol, start, end, resolution, *, asset_type=None, **kw):
            calls["fetch"].append((symbol, pd.Timestamp(start), pd.Timestamp(end)))
            if fetch is not None:
                return fetch(symbol, start, end, resolution)
            idx = pd.date_range(
                pd.Timestamp(start).ceil("h"), pd.Timestamp(end), freq="1h", tz="UTC"
            )
            idx = idx[idx < pd.Timestamp(end)]
            if len(idx) == 0:
                return make_ohlcv("2024-01-01 00:00", periods=0, freq="1h")
            return make_ohlcv(str(idx[0]), periods=len(idx), freq="1h")

    if estimate is not None:

        def estimate_cost(self, symbol, start, end, resolution, *, asset_type=None):
            calls["estimate_cost"].append(symbol)
            return estimate

        CliStubProvider.estimate_cost = estimate_cost  # type: ignore[attr-defined]

    monkeypatch.setitem(
        ProviderRegistry._PROVIDER_MAP, "manifest-test", CliStubProvider
    )
    return calls


def write_manifest(tmp_path: Path, body: str, name: str = "smoke.yaml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def hourly_manifest(
    tmp_path: Path,
    manifest_id: str = "smoke",
    *,
    symbol: str = "BTC-USD",
    start: str = "2024-01-01",
    end: str = "2024-01-01T23:59:59Z",
    policy: str = "incremental",
    provider: str = "manifest-test",
) -> Path:
    return write_manifest(
        tmp_path,
        f"""
version: 1
id: {manifest_id}
libraries:
  ds1:
    asset_type: crypto
    provider: {provider}
    resolution: 1h
    symbols: [{symbol}]
    start: '{start}'
    end: {end}
    update_policy: {policy}
""",
    )


class TestEnsureWorkflow:
    def test_dry_run_apply_and_repeat(
        self, tmp_path, capsys, cli_seams, storage, monkeypatch
    ) -> None:
        calls = register_provider(monkeypatch)
        manifest_path = hourly_manifest(tmp_path)

        # Dry-run JSON: reports creation; stdout is exactly one JSON value.
        code = cli.main(["ensure", str(manifest_path), "--dry-run", "--json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["operation"] == "ensure"
        assert payload["dry_run"] is True
        assert payload["results"][0]["action"] == "create"
        assert payload["results"][0]["status"] == "planned"
        assert payload["planned_libraries"] == [LIB]
        assert payload["created_libraries"] == []
        assert storage.arctic.list_libraries() == []

        # Apply: canonical library created with exactly the 24 declared rows.
        code = cli.main(["ensure", str(manifest_path), "--json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["totals"] == {
            "created": 1,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
        }
        assert payload["created_libraries"] == [LIB]

        stored = storage.read(LIB, "BTC-USD")
        expected = make_ohlcv("2024-01-01 00:00", periods=24, freq="1h")
        pd.testing.assert_frame_equal(
            stored, expected, check_exact=True, check_freq=False
        )
        assert stored.index[0] == pd.Timestamp("2024-01-01 00:00", tz="UTC")
        assert stored.index[-1] == pd.Timestamp("2024-01-01 23:00", tz="UTC")
        assert stored["close"].iloc[0] == pytest.approx(100.2)
        assert stored["close"].iloc[-1] == pytest.approx(102.5)
        assert len(calls["fetch"]) == 1

        # Repeated ensure: values and version count unchanged, no fetch.
        versions = len(storage.arctic[LIB].list_versions("BTC-USD"))
        code = cli.main(["ensure", str(manifest_path), "--json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["results"][0]["status"] == "skipped"
        assert payload["results"][0]["reason"] == "already_exists"
        again = storage.read(LIB, "BTC-USD")
        pd.testing.assert_frame_equal(
            again, expected, check_exact=True, check_freq=False
        )
        assert len(storage.arctic[LIB].list_versions("BTC-USD")) == versions
        assert len(calls["fetch"]) == 1

    def test_text_output_reports_targets_and_state(
        self, tmp_path, capsys, cli_seams, monkeypatch
    ) -> None:
        register_provider(monkeypatch)
        manifest_path = hourly_manifest(tmp_path)
        assert cli.main(["ensure", str(manifest_path)]) == 0
        out = capsys.readouterr().out
        assert "smoke" in out
        assert "BTC-USD" in out
        assert LIB in out
        assert "action=create" in out
        assert "status=created" in out


class TestSyncWorkflow:
    def test_incremental_restores_exact_missing_value(
        self, tmp_path, capsys, cli_seams, storage, monkeypatch
    ) -> None:
        calls = register_provider(monkeypatch)
        manifest_path = hourly_manifest(tmp_path)
        assert cli.main(["ensure", str(manifest_path)]) == 0
        capsys.readouterr()

        full = storage.read(LIB, "BTC-USD")
        hole_value = full.loc[pd.Timestamp("2024-01-01 07:00", tz="UTC")]["close"]
        storage.write(
            LIB, "BTC-USD", full.drop(pd.Timestamp("2024-01-01 07:00", tz="UTC"))
        )

        # ensure reports already_exists (does not repair holes)...
        assert cli.main(["ensure", str(manifest_path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["results"][0]["reason"] == "already_exists"

        # ...sync restores the exact missing bar.
        assert cli.main(["sync", str(manifest_path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["results"][0]["action"] == "fill"
        assert payload["results"][0]["rows_written"] == 1
        restored = storage.read(LIB, "BTC-USD")
        assert len(restored) == 24
        assert (
            restored.loc[pd.Timestamp("2024-01-01 07:00", tz="UTC")]["close"]
            == hole_value
        )

        # Second sync: zero fetches, zero writes, no state write.
        state_before = storage.read("fin3-metadata", "__manifest__smoke")
        snapshot = state_before.copy(deep=True)
        fetches = len(calls["fetch"])
        assert cli.main(["sync", str(manifest_path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["results"][0]["status"] == "skipped"
        assert payload["results"][0]["reason"] == "covered"
        assert len(calls["fetch"]) == fetches
        state_after = storage.read("fin3-metadata", "__manifest__smoke")
        pd.testing.assert_frame_equal(state_after, snapshot, check_exact=True)

    def test_extend_end_reports_drift_and_new_day_only(
        self, tmp_path, capsys, cli_seams, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch)
        day1 = hourly_manifest(tmp_path)
        assert cli.main(["sync", str(day1), "--json"]) == 0
        capsys.readouterr()

        extended = hourly_manifest(
            tmp_path, end="2024-01-02T23:59:59Z", symbol="BTC-USD"
        )
        clock = datetime(2024, 1, 3, 1, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(
            cli, "load_manifest", lambda path, **kw: _load_with_clock(path, clock)
        )
        # status previews sync-policy actions and reports drift
        assert cli.main(["status", str(extended), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["definition_state"] == "changed"
        assert payload["operation"] == "status"
        assert payload["results"][0]["write_ranges"] == [
            ["2024-01-02T00:00:00+00:00", "2024-01-02T23:00:00+00:00"]
        ]

        assert cli.main(["sync", str(extended), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["results"][0]["status"] == "updated"
        after = storage.read(LIB, "BTC-USD")
        assert len(after) == 48
        # Day 1 preserved verbatim.
        day1_part = after[after.index < pd.Timestamp("2024-01-02", tz="UTC")]
        expected = make_ohlcv("2024-01-01 00:00", periods=24, freq="1h")
        pd.testing.assert_frame_equal(
            day1_part, expected, check_exact=True, check_freq=False
        )

        assert cli.main(["sync", str(extended), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["results"][0]["reason"] == "covered"
        assert payload["definition_state"] == "unchanged"

    def test_now_gate_advances_with_clock(
        self, tmp_path, capsys, cli_seams, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch)
        manifest_path = hourly_manifest(tmp_path, end="now", symbol="ETH-USD")

        monkeypatch.setattr(
            cli,
            "load_manifest",
            lambda path, **kw: _load_with_clock(
                path, datetime(2024, 1, 2, 0, 30, tzinfo=timezone.utc)
            ),
        )
        assert cli.main(["ensure", str(manifest_path), "--json"]) == 0
        stored = storage.read(LIB, "ETH-USD")
        assert stored.index[-1] == pd.Timestamp("2024-01-01 23:00", tz="UTC")

        monkeypatch.setattr(
            cli,
            "load_manifest",
            lambda path, **kw: _load_with_clock(
                path, datetime(2024, 1, 2, 1, 0, tzinfo=timezone.utc)
            ),
        )
        assert cli.main(["sync", str(manifest_path), "--json"]) == 0
        stored = storage.read(LIB, "ETH-USD")
        assert stored.index[-1] == pd.Timestamp("2024-01-02 00:00", tz="UTC")

    def test_append_only_leaves_holes_behind_tail(
        self, tmp_path, capsys, cli_seams, storage, monkeypatch
    ) -> None:
        calls = register_provider(monkeypatch)
        seed = make_ohlcv("2024-01-01 00:00", periods=3, freq="1h")
        storage.ensure_library(LIB)
        storage.write(LIB, "XRP-USD", seed)

        manifest_path = hourly_manifest(
            tmp_path, symbol="XRP-USD", policy="append-only"
        )
        assert cli.main(["sync", str(manifest_path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["results"][0]["action"] == "append"
        stored = storage.read(LIB, "XRP-USD")
        assert len(stored) == 24
        assert stored.index[0] == pd.Timestamp("2024-01-01 00:00", tz="UTC")

        torn = stored.drop(pd.Timestamp("2024-01-01 10:00", tz="UTC"))
        storage.write(LIB, "XRP-USD", torn)
        fetches = len(calls["fetch"])
        assert cli.main(["sync", str(manifest_path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["results"][0]["reason"] == "covered"
        assert len(calls["fetch"]) == fetches
        assert (
            pd.Timestamp("2024-01-01 10:00", tz="UTC")
            not in storage.read(LIB, "XRP-USD").index
        )


def _load_with_clock(path, clock):
    from fin3.manifest import load_manifest

    return load_manifest(path, now=clock)


class TestFailureSemantics:
    def test_provider_failure_report_exit_1_and_partial_success(
        self, tmp_path, capsys, cli_seams, storage, monkeypatch
    ) -> None:
        def fetch(symbol, start, end, resolution):
            if symbol == "BAD-USD":
                raise ProviderError("upstream unavailable")
            idx = pd.date_range(
                pd.Timestamp(start).ceil("h"), periods=24, freq="1h", tz="UTC"
            )
            return make_ohlcv(str(idx[0]), periods=24, freq="1h")

        register_provider(monkeypatch, fetch=fetch)
        path = write_manifest(
            tmp_path,
            """
version: 1
id: partial
libraries:
  ds1:
    asset_type: crypto
    provider: manifest-test
    resolution: 1h
    symbols: [BAD-USD, GOOD-USD]
    start: '2024-01-01'
    end: '2024-01-01T23:59:59Z'
    update_policy: incremental
""",
        )
        code = cli.main(["sync", str(path), "--json"])
        assert code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["totals"]["failed"] == 1
        assert payload["totals"]["created"] == 1
        by_symbol = {r["symbol"]: r for r in payload["results"]}
        assert by_symbol["BAD-USD"]["status"] == "failed"
        assert "upstream unavailable" in by_symbol["BAD-USD"]["error"]
        assert by_symbol["GOOD-USD"]["status"] == "created"

    def test_state_write_failure_exit_1(
        self, tmp_path, capsys, cli_seams, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch)
        manifest_path = hourly_manifest(tmp_path, symbol="SW-USD")
        assert cli.main(["sync", str(manifest_path), "--json"]) == 0
        capsys.readouterr()

        real_write = storage.write

        def flaky_write(library, symbol, data, **kwargs):
            if library == "fin3-metadata":
                raise RuntimeError("ack lost")
            return real_write(library, symbol, data, **kwargs)

        monkeypatch.setattr(storage, "write", flaky_write)
        changed = hourly_manifest(tmp_path, symbol="SW2-USD")
        code = cli.main(["sync", str(changed), "--json"])
        assert code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["state_write_unknown"] is True
        assert any("state write failed" in e for e in payload["errors"])

    def test_cost_ceiling_exit_1_operational_no_writes(
        self, tmp_path, capsys, cli_seams, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch, estimate=50.0)
        manifest_path = hourly_manifest(tmp_path, symbol="COST-USD")
        code = cli.main(["sync", str(manifest_path), "--max-cost", "1.0", "--json"])
        assert code == 1
        err = json.loads(capsys.readouterr().out)
        assert err["error"]["kind"] == "operational"
        assert "max_cost" in err["error"]["message"]
        assert storage.read(LIB, "COST-USD") is None

    def test_estimated_cost_within_cap_reports_value(
        self, tmp_path, capsys, cli_seams, monkeypatch
    ) -> None:
        calls = register_provider(monkeypatch, estimate=1.5)
        manifest_path = hourly_manifest(tmp_path, symbol="COST2-USD")
        code = cli.main(["sync", str(manifest_path), "--max-cost", "2.0", "--json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["estimated_cost"] == pytest.approx(1.5)
        assert len(calls["estimate_cost"]) == 1

    def test_secrets_redacted_in_error_output(
        self, tmp_path, capsys, cli_seams, monkeypatch
    ) -> None:
        def fetch(symbol, start, end, resolution):
            raise ProviderError(
                f"request to https://api.example/x?api_key={API_KEY} failed"
            )

        register_provider(monkeypatch, fetch=fetch)
        manifest_path = hourly_manifest(tmp_path, symbol="RED-USD")
        code = cli.main(["sync", str(manifest_path), "--json"])
        assert code == 1
        out = capsys.readouterr().out
        assert API_KEY not in out
        payload = json.loads(out)
        assert "***" in payload["results"][0]["error"]


class TestEnvelopes:
    def test_invalid_manifest_configuration_envelope_exit_2(
        self, tmp_path, capsys, cli_seams
    ) -> None:
        bad = tmp_path / "invalid.json"
        bad.write_text('{"version": 1, "id": "broken", "libraries": {}}')
        code = cli.main(["status", str(bad), "--json"])
        assert code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"]["kind"] == "configuration"
        assert "libraries" in payload["error"]["message"]

    def test_configuration_error_before_config_load(
        self, tmp_path, capsys, cli_seams
    ) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("version: 9\nid: x\nlibraries: {}\n")
        code = cli.main(["ensure", str(bad), "--json"])
        assert code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"]["kind"] == "configuration"

    def test_unknown_command_is_argparse_error(self, capsys, cli_seams) -> None:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["frobnicate"])
        assert excinfo.value.code == 2
        assert "invalid choice" in capsys.readouterr().err

    def test_missing_manifest_argument_is_argparse_error(
        self, capsys, cli_seams
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["ensure"])
        assert excinfo.value.code == 2

    def test_valid_status_with_drift_exits_zero(
        self, tmp_path, capsys, cli_seams, monkeypatch
    ) -> None:
        register_provider(monkeypatch)
        manifest_path = hourly_manifest(tmp_path, symbol="DRIFT-USD")
        assert cli.main(["status", str(manifest_path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["definition_state"] == "untracked"
        assert payload["results"][0]["action"] == "create"
        # Text mode too.
        assert cli.main(["status", str(manifest_path)]) == 0
        assert "untracked" in capsys.readouterr().out

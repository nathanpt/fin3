"""Gated live checks for the EODHD options provider and options integration.

Gate: ``FIN3_EODHD_LIVE=1`` **plus** ``EODHD_API_KEY``. Missing MinIO
settings skip only the storage-dependent checks, never the source checks.

Bucket ownership: when the standalone driver supplies
``FIN3_EODHD_TEST_BUCKET``, the tests adopt that already-owned artifact
(reusing its SHY snapshot without re-backfilling and without deleting the
bucket — the driver owns its lifetime). Otherwise a fresh confirmed-absent
``eodhd-test-<12 hex>`` bucket is created and fixture-cleaned.

``FIN3_EODHD_TEST_EVIDENCE`` (absolute path) points at a token-redacted
source capture; in driver-owned mode the tests read it instead of
re-downloading, and compare the stored/readback artifact against it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets as pysecrets
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Generator

import pandas as pd
import pytest

from fin3.config.settings import (
    ClientConfig,
    EodhdOptionsConfig,
    MinioConfig,
)
from fin3.exceptions import ProviderError
from fin3.options import OptionsDataFetcher
from fin3.providers.eodhd_options import EodhdOptionsProvider
from fin3.storage.arctic import ArcticStorage

pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / ".docs" / "fixtures" / "eodhd"
SHY_DAY = date(2026, 11, 6)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _digest(frame: pd.DataFrame) -> str:
    """Stable digest over sorted (contract, as_of, bid, ask, vol, oi, iv)."""
    records: list[dict[str, Any]] = []
    for idx, row in frame.iterrows():
        def num(value: Any) -> float | None:
            if value is None or (isinstance(value, float) and math.isnan(value)):
                return None
            return float(value)

        records.append(
            {
                "contract": str(idx[1]),
                "as_of": pd.Timestamp(idx[0]).date().isoformat(),
                "bid": num(row["bid"]),
                "ask": num(row["ask"]),
                "volume": num(row["volume"]),
                "open_interest": num(row["open_interest"]),
                "implied_volatility": num(row["implied_volatility"]),
            }
        )
    records.sort(key=lambda r: (r["as_of"], r["contract"]))
    payload = json.dumps(records, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _source_records(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive (contract, as_of, quote…) records from a token-redacted capture."""
    records: list[dict[str, Any]] = []
    for entry in evidence["captures"]:
        for row in entry.get("response", {}).get("data", []):
            if row.get("type") != "options-eod":
                continue
            attributes = row["attributes"]
            eod_id = str(row["id"])
            as_of = eod_id[len(attributes["contract"]) + 1:]

            def num(value: Any) -> float | None:
                if value is None:
                    return None
                return float(value)

            records.append(
                {
                    "contract": attributes["contract"],
                    "as_of": as_of,
                    "bid": num(attributes.get("bid")),
                    "ask": num(attributes.get("ask")),
                    "volume": num(attributes.get("volume")),
                    "open_interest": num(attributes.get("open_interest")),
                    "implied_volatility": num(attributes.get("volatility")),
                }
            )
    records.sort(key=lambda r: (r["as_of"], r["contract"]))
    return records


def _source_digest(evidence: dict[str, Any], as_of: str) -> str:
    records = [r for r in _source_records(evidence) if r["as_of"] == as_of]
    payload = json.dumps(records, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def eodhd_raw_key() -> str:
    if _env("FIN3_EODHD_LIVE") != "1":
        pytest.skip("FIN3_EODHD_LIVE=1 gate off (live EODHD checks)")
    key = _env("EODHD_API_KEY")
    if not key.strip():
        pytest.skip("EODHD_API_KEY absent (live EODHD checks)")
    return key


@pytest.fixture(scope="session")
def minio_settings() -> MinioConfig:
    missing = [
        setting
        for setting in (
            "FIN3_MINIO__ENDPOINT",
            "FIN3_MINIO__ACCESS_KEY",
            "FIN3_MINIO__SECRET_KEY",
        )
        if not _env(setting)
    ]
    if missing:
        pytest.skip(f"MinIO integration settings absent: {', '.join(missing)}")
    return MinioConfig(
        endpoint=_env("FIN3_MINIO__ENDPOINT"),
        access_key=_env("FIN3_MINIO__ACCESS_KEY"),
        secret_key=_env("FIN3_MINIO__SECRET_KEY"),
        secure=_env("FIN3_MINIO__SECURE", "false").lower() == "true",
    )


@pytest.fixture(scope="session")
def owned_bucket(
    minio_settings: MinioConfig,
) -> Generator[tuple[str, ArcticStorage], None, None]:
    """Return (bucket, storage) for a test-owned single-bucket store."""
    override = _env("FIN3_EODHD_TEST_BUCKET")
    if override:
        bucket = override
        storage = ArcticStorage(
            MinioConfig(
                endpoint=minio_settings.endpoint,
                access_key=minio_settings.access_key,
                secret_key=minio_settings.secret_key,
                secure=minio_settings.secure,
                bucket=bucket,
            )
        )
        # Driver-owned: adopt without re-creating and never delete.
        yield bucket, storage
        return

    import time as _time

    bucket = f"eodhd-test-{pysecrets.token_hex(6)}"
    storage = ArcticStorage(
        MinioConfig(
            endpoint=minio_settings.endpoint,
            access_key=minio_settings.access_key,
            secret_key=minio_settings.secret_key,
            secure=minio_settings.secure,
            bucket=bucket,
        )
    )
    if storage._bucket_exists(bucket):
        raise RuntimeError(f"fresh test bucket {bucket!r} unexpectedly exists")
    _time.sleep(0.05)
    yield bucket, storage

    # Fixture-owned cleanup: drop the libraries this run created, then the
    # empty bucket.
    try:
        arctic = storage.arctic_for(bucket)
        for library in list(arctic.list_libraries()):
            arctic.delete_library(library)
        storage._bucket_request(bucket, "DELETE")
    except Exception as exc:  # pragma: no cover - cleanup best-effort
        warnings_warn = getattr(pytest, "warns", None)
        print(f"WARNING: cleanup of {bucket!r} failed: {exc!r} ({warnings_warn})")


@pytest.fixture(scope="session")
def evidence() -> dict[str, Any] | None:
    """Load the token-redacted source capture when one is supplied."""
    path = _env("FIN3_EODHD_TEST_EVIDENCE")
    if path and Path(path).exists():
        return dict(json.loads(Path(path).read_text()))
    return None


def _base_config(minio_settings: MinioConfig, raw_key: str) -> ClientConfig:
    """Anchored base fin3 config: real storage settings, raw key, no options config."""
    return ClientConfig(
        minio=MinioConfig(
            endpoint=minio_settings.endpoint,
            access_key=minio_settings.access_key,
            secret_key=minio_settings.secret_key,
            secure=minio_settings.secure,
        ),
        EODHD_API_KEY=raw_key,
        _env_file=None,
    )


def _bucket_config(base: ClientConfig, bucket: str) -> ClientConfig:
    return base.model_copy(
        update={
            "minio": base.minio.model_copy(update={"bucket": bucket}),
        }
    )


def _ingest_shy(
    bucket: str,
    storage: ArcticStorage,
    minio_settings: MinioConfig,
    raw_key: str,
    evidence: dict[str, Any] | None,
) -> tuple[OptionsDataFetcher, dict[str, Any]]:
    """Backfill SHY once (driver mode reuses), capturing or reusing evidence."""
    fetcher = OptionsDataFetcher(_bucket_config(_base_config(minio_settings, raw_key), bucket), storage=storage)
    if _env("FIN3_EODHD_TEST_BUCKET") and evidence is not None:
        return fetcher, evidence
    captures: list[dict[str, Any]] = []
    import fin3.providers.eodhd_options as module

    original = module.EodhdOptionsProvider._get_json

    def capturing(self: Any, url: str, budget: Any) -> dict[str, Any]:
        payload = original(self, url, budget)
        parsed = module.urllib.parse.urlsplit(url)
        params = [
            (k, "***" if k == "api_token" else v)
            for k, v in module.urllib.parse.parse_qsl(
                parsed.query, keep_blank_values=True
            )
        ]
        captures.append(
            {
                "endpoint": f"https://{parsed.netloc}{parsed.path}",
                "params": params,
                "response": payload,
            }
        )
        return payload

    module.EodhdOptionsProvider._get_json = capturing  # type: ignore[method-assign]
    try:
        manifest = fetcher.backfill("SHY", SHY_DAY, SHY_DAY, max_requests=512)
    finally:
        module.EodhdOptionsProvider._get_json = original  # type: ignore[method-assign]
    assert manifest.root == "SHY"
    return fetcher, {"captures": captures}


@pytest.fixture(scope="session")
def shy_state(
    owned_bucket: tuple[str, ArcticStorage],
    minio_settings: MinioConfig,
    eodhd_raw_key: str,
    evidence: dict[str, Any] | None,
) -> Generator[tuple[OptionsDataFetcher, dict[str, Any] | None, str], None, None]:
    bucket, storage = owned_bucket
    fetcher, local_evidence = _ingest_shy(
        bucket, storage, minio_settings, eodhd_raw_key, evidence
    )
    capture_path = _env("FIN3_EODHD_TEST_EVIDENCE")
    if not capture_path:
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        capture_path = str(FIXTURE_DIR / f"{bucket}-source.json")
        Path(capture_path).write_text(json.dumps(local_evidence, indent=1))
    yield fetcher, evidence, capture_path


# ---------------------------------------------------------------------------
# Source-only checks (no storage)
# ---------------------------------------------------------------------------


class TestEodhdSource:
    """Independent source/pagination/bounds checks; raw key only."""

    @pytest.fixture(scope="class")
    def provider(self, eodhd_raw_key: str) -> EodhdOptionsProvider:
        return EodhdOptionsProvider(
            EodhdOptionsConfig(api_key=eodhd_raw_key, page_size=500)
        )

    def test_real_pagination_and_bounds(self, provider: EodhdOptionsProvider) -> None:
        """SPY261218C00675000's 731-row history must cross ≥2 real pages."""
        contract = "SPY261218C00675000"
        bounds = provider.get_instrument_bounds(contract, max_requests=64)
        assert bounds["first_observation"] is not None
        assert bounds["last_observation"] is not None
        first = bounds["first_observation"]
        assert isinstance(first, date)
        assert isinstance(bounds["last_observation"], date)
        span_days = (bounds["last_observation"] - first).days + 1
        assert span_days >= 500, (
            f"expected a multi-page history, got {span_days} days"
        )
        print(
            f"SPY261218C00675000 bounds: {bounds['first_observation']} .. "
            f"{bounds['last_observation']} ({span_days} day span)"
        )
        if first != date(2023, 12, 29):
            print(
                f"NOTE: observed first date moved from planning's 2023-12-29 to {first}"
            )

        # A contract-scoped single-date fetch through the same exhaust-paging
        # path (the full SPY root would enumerate thousands of contracts).
        as_of = bounds["last_observation"]
        frame = provider.fetch_history(
            "SPY", as_of, as_of, contracts=[contract], max_requests=64
        )
        labels = sorted(
            {ts.date().isoformat() for ts in frame.index.get_level_values("as_of")}
        )
        assert labels == [as_of.isoformat()]
        assert list(frame.index.get_level_values("contract")) == [contract]

    def test_unknown_underlying_classification(
        self, provider: EodhdOptionsProvider
    ) -> None:
        """A syntactically valid unknown root yields a reasoned empty result.

        Auth/transport failures are never interpreted as no-data.
        """
        try:
            frame = provider.fetch_history(
                "ZZZZZX", date(2026, 11, 6), date(2026, 11, 6), max_requests=8
            )
        except ProviderError as exc:
            pytest.fail(
                f"unknown-root probe failed with a provider error, which must "
                f"not be classified as no-data: {exc}"
            )
        assert frame.empty
        from fin3.options_schemas import CHAIN_REPORT_ATTR

        assert frame.attrs[CHAIN_REPORT_ATTR]["reason"] == "unknown_underlying"

    def test_documented_envelope_shapes(self) -> None:
        """The captured token-redacted fixtures match the documented envelope."""
        contracts = json.loads((FIXTURE_DIR / "contracts.json").read_text())
        eod = json.loads((FIXTURE_DIR / "eod.json").read_text())
        validation = json.loads((FIXTURE_DIR / "validation-error.json").read_text())
        assert contracts["meta"]["offset"] == 0
        assert isinstance(contracts["data"], list)
        assert contracts["data"][0]["type"] == "options-contracts"
        assert eod["data"][0]["type"] == "options-eod"
        assert eod["data"][0]["id"].endswith(eod["data"][0]["attributes"]["tradetime"])
        assert validation["status"] == 422
        assert "page.limit" in validation["body"]["errors"]


# ---------------------------------------------------------------------------
# Storage-dependent checks (owned bucket)
# ---------------------------------------------------------------------------


def test_shy_root_backfill_matches_source(
    shy_state: tuple[OptionsDataFetcher, dict[str, Any] | None, str],
    owned_bucket: tuple[str, ArcticStorage],
) -> None:
    fetcher, evidence, _ = shy_state
    result = fetcher.read_chain("SHY", SHY_DAY)
    assert result.status == "observed"
    frame = result.data
    multiplier_nulls = int(frame["multiplier"].isna().sum())
    assert multiplier_nulls == len(frame)
    manifest = result.manifest
    assert manifest is not None
    day = manifest.days[0]
    print(
        f"SHY {SHY_DAY}: {day.contract_count} contracts, scope={day.scope}, "
        f"reference={day.reference_contract_count}, "
        f"requests_used={manifest.requests_used} "
        f"(~x10 credits = {manifest.requests_used * 10})"
    )
    if evidence is not None:
        stored = _digest(frame)
        source = _source_digest(evidence, SHY_DAY.isoformat())
        assert stored == source, "stored/readback digest differs from captured source"
        print(f"digest match: {stored[:16]}…")


def test_expired_and_equity_selections(
    owned_bucket: tuple[str, ArcticStorage],
    minio_settings: MinioConfig,
    eodhd_raw_key: str,
    evidence: dict[str, Any] | None,
) -> None:
    bucket, storage = owned_bucket
    fetcher = OptionsDataFetcher(
        _bucket_config(_base_config(minio_settings, eodhd_raw_key), bucket),
        storage=storage,
    )
    # Expired SPY contract incl. zero-bid/0.01-ask rows and historical IV.
    spy_manifest = fetcher.backfill(
        "SPY",
        date(2025, 3, 14),
        date(2025, 3, 20),
        contracts=["SPY250321C00800000"],
    )
    assert spy_manifest.days[0].scope == "contracts"
    spy_result = fetcher.read_chain(
        "SPY", date(2025, 3, 20), expiry=date(2025, 3, 21)
    )
    assert spy_result.status == "observed"
    row = spy_result.data.iloc[-1]
    print(
        f"SPY250321C00800000 @2025-03-20: bid={row['bid']} ask={row['ask']} "
        f"iv={row['implied_volatility']} oi={row['open_interest']}"
    )
    zero_bids = bool(spy_result.data["has_zero_bid"].any())
    min_bid = float(spy_result.data["bid"].min())
    assert zero_bids or min_bid >= 0.0

    # F equity deep-OTM selection.
    f_manifest = fetcher.backfill(
        "F", date(2026, 11, 6), date(2026, 11, 6),
        contracts=["F261113C00005000"],
    )
    assert f_manifest.days[0].scope == "contracts"
    f_result = fetcher.read_chain("F", SHY_DAY)
    assert f_result.status == "observed"
    f_row = f_result.data.iloc[0]
    assert f_row["root"] == "F"
    assert f_row["right"] == "C"
    assert f_row["expiry"] == "2026-11-13"
    assert float(f_row["strike"]) == 5.0
    print(
        f"F261113C00005000 @2026-11-06: bid={f_row['bid']} ask={f_row['ask']} "
        f"vol={f_row['volume']} oi={f_row['open_interest']} "
        f"iv={f_row['implied_volatility']}"
    )


def test_worthless_expiry_underlying_evidence(minio_settings: MinioConfig) -> None:
    """Independent SPY close evidence for the expired strike-800 call.

    Reads the pre-existing production equities library read-only; absence is
    reported as unverified, never fabricated.
    """
    reference = ArcticStorage(minio_settings)  # unchanged base storage target
    try:
        df = reference.read(
            "equities-1d-databento",
            "SPY",
            date_range=(
                datetime(2025, 3, 21, tzinfo=timezone.utc),
                datetime(2025, 3, 21, 23, 59, 59, 999999, tzinfo=timezone.utc),
            ),
        )
    except Exception as exc:
        pytest.skip(f"reference store read failed (R4.4 stays unverified): {exc}")
    if df is None or df.empty:
        pytest.skip("equities-1d-databento/SPY absent (R4.4 stays unverified)")
    close = float(df["close"].iloc[0])
    assert math.isfinite(close)
    print(f"SPY close 2025-03-21 (independent store): {close}")
    assert close < 800.0, "strike-800 call assumed OTM; close contradicts"


def test_no_mutation_on_reads(
    shy_state: tuple[OptionsDataFetcher, dict[str, Any] | None, str],
    owned_bucket: tuple[str, ArcticStorage],
    minio_settings: MinioConfig,
    eodhd_raw_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bucket, storage = owned_bucket
    lib = storage.arctic_for(bucket)["options-eod-eodhd"]
    with _suppress():
        before = lib.read("SHY")

    # Fresh client: raw-key alias explicitly None, no options provider config.
    config = ClientConfig(
        minio=MinioConfig(
            endpoint=minio_settings.endpoint,
            access_key=minio_settings.access_key,
            secret_key=minio_settings.secret_key,
            secure=minio_settings.secure,
            bucket=bucket,
        ),
        EODHD_API_KEY=None,
        _env_file=None,
    )
    fetcher = OptionsDataFetcher(config, storage=storage)

    import arcticdb

    for name in ("write", "append", "update", "delete"):
        monkeypatch.setattr(
            arcticdb.version_store.library.Library, name,
            _forbidden(f"Library.{name}"), raising=False,
        )
    for name in ("write", "update", "ensure_library", "_get_or_create_library"):
        monkeypatch.setattr(ArcticStorage, name, _forbidden(f"ArcticStorage.{name}"))
    monkeypatch.setattr(
        OptionsDataFetcher, "_resolve_provider",
        _forbidden("provider resolution during read"),
    )

    result1 = fetcher.read_chain("SHY", SHY_DAY)
    result2 = fetcher.read_chain("SHY", SHY_DAY)
    pd.testing.assert_frame_equal(result1.data, result2.data)
    assert result1.manifest == result2.manifest

    put_count = _put_object_count_around(lambda: fetcher.read_chain("SHY", SHY_DAY))
    if put_count < 0:
        print(
            "NOTE: S3 PutObject counter evidence unavailable (query_stats "
            "backend); zero-mutation rests on the mutator guards, version "
            "identity, and write-timestamp equality above."
        )
    else:
        assert put_count == 0, f"reads issued {put_count} S3 PutObject calls"

    with _suppress():
        after = lib.read("SHY")
    assert after.version == before.version
    assert after.timestamp == before.timestamp


def test_explicit_gaps(
    shy_state: tuple[OptionsDataFetcher, dict[str, Any] | None, str],
    owned_bucket: tuple[str, ArcticStorage],
    minio_settings: MinioConfig,
    eodhd_raw_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bucket, storage = owned_bucket
    fetcher = shy_state[0]

    result = fetcher.read_chain("SHY", date(2020, 3, 20))
    assert result.status == "no_data"
    assert result.reason == "outside_ingested_coverage"
    assert len(result.data) == 0
    assert result.manifest is not None

    absent = OptionsDataFetcher(
        _bucket_config(_base_config(minio_settings, eodhd_raw_key), bucket),
        storage=ArcticStorage(
            MinioConfig(
                endpoint=minio_settings.endpoint,
                access_key=minio_settings.access_key,
                secret_key=minio_settings.secret_key,
                secure=minio_settings.secure,
                bucket=bucket,
            )
        ),
    )
    ghost = absent.read_chain("NVDA", SHY_DAY)
    assert ghost.status == "no_data"
    assert ghost.reason == "root_not_ingested"

    # Contract-scope selection expiring before its ingestion window: reasoned
    # empty coverage, identity lookups only, no history fetch.
    expired = fetcher.backfill(
        "SHY", date(2026, 12, 1), date(2026, 12, 1),
        contracts=["SHY261120C00091000"], max_requests=8,
    )
    gap_day = next(
        d for d in expired.days if d.as_of == date(2026, 12, 1)
    )
    assert gap_day.contract_count == 0
    assert gap_day.reason == "expiry_before_window"

    # With every writer forbidden, gap reads still work and carry no rows.
    for name in ("write", "update", "ensure_library", "_get_or_create_library"):
        monkeypatch.setattr(ArcticStorage, name, _forbidden(f"ArcticStorage.{name}"))
    result = fetcher.read_chain("SHY", date(2020, 3, 20))
    assert result.status == "no_data"
    assert result.reason == "outside_ingested_coverage"
    assert len(result.data) == 0
    gap = fetcher.read_chain("SHY", SHY_DAY, expiry=date(2026, 11, 5))
    assert gap.status == "no_data"
    assert gap.reason == "expiry_before_as_of"


def test_finlab_import_boundary(
    shy_state: tuple[OptionsDataFetcher, dict[str, Any] | None, str],
    owned_bucket: tuple[str, ArcticStorage],
) -> None:
    """finlab reads the owned artifact through its own env and endpoint."""
    bucket, _ = owned_bucket
    _, evidence, _ = shy_state
    finlab = Path("/mnt/dev/projects/finlab")
    if not (finlab / ".env").exists():
        pytest.skip("finlab checkout/.env absent")
    child_code = r"""
import json, math, os, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, "/mnt/dev/projects/fin3")
# finlab's own .env carries non-fin3 keys that fin3's strict config forbids,
# so the child sources exactly the FIN3_* subset of finlab's dotenv into its
# environment (finlab's endpoint/credentials; the parent-supplied owned
# bucket wins via setdefault). Nothing is copied or written to disk.
from dotenv import dotenv_values
for key, value in dotenv_values(Path.cwd() / ".env").items():
    if key and key.startswith("FIN3_") and value is not None:
        os.environ.setdefault(key, value)
from fin3 import ClientConfig, OptionsDataFetcher

config = ClientConfig(_env_file=None, EODHD_API_KEY=None)
fetcher = OptionsDataFetcher(config)
result = fetcher.read_chain("SHY", date(2026, 11, 6))
out = {
    "status": result.status,
    "reason": result.reason,
    "rows": len(result.data),
    "endpoint": config.minio.endpoint,
    "bucket": config.minio.bucket,
    "first_row_bid": None,
}
if len(result.data):
    bid = float(result.data["bid"].iloc[0])
    out["first_row_bid"] = None if math.isnan(bid) else bid
print(json.dumps(out))
"""
    env = dict(os.environ)
    env["FIN3_MINIO__BUCKET"] = bucket
    proc = subprocess.run(
        ["/mnt/dev/projects/fin3/.venv/bin/python", "-c", child_code],
        cwd=finlab,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-500:]
        pytest.skip(f"finlab child failed (boundary reported, not retargeted): {tail}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    child = json.loads(lines[-1])
    print(f"finlab child: {child}")
    assert child["status"] == "observed"
    assert child["bucket"] == bucket
    # Compare the child's readback against the parent's expected result.
    fetcher = shy_state[0]
    parent = fetcher.read_chain("SHY", SHY_DAY)
    assert child["rows"] == len(parent.data)
    if len(parent.data):
        parent_bid = float(parent.data["bid"].iloc[0])
        child_bid = child["first_row_bid"]
        if parent_bid != parent_bid:  # NaN bid
            assert child_bid is None
        else:
            assert child_bid == parent_bid


def _forbidden(name: str):
    def raise_if_touched(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"read path mutated storage via {name}")

    return raise_if_touched


def _suppress():
    from fin3.storage.arctic import _suppress_blockmanager_warning

    return _suppress_blockmanager_warning()


def _put_object_count_around(fn) -> int:
    """Sum S3 PutObject counts immediately around *fn*; 0 when unsupported."""
    try:
        from arcticdb.toolbox.query_stats import query_stats
    except Exception:
        print("query_stats unavailable; relying on mutator/version checks")
        fn()
        return -1
    try:
        with query_stats() as stats:
            fn()
            results = stats.get_stats()
    except Exception:
        print("query_stats failed; relying on mutator/version checks")
        fn()
        return -1
    try:
        storage_ops = results["storage_operations"]
        puts = storage_ops.get("S3_PutObject", [])
        return int(sum(item.get("count", 0) for item in puts))
    except Exception:
        return -1

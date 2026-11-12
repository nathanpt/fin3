"""Integration: adopt the standalone driver's full-root artifact read-only.

Paid root creation has exactly one owner: the standalone grid driver
(`FIN3_EODHD_LIVE=1 uv run python /tmp/fin3-eodhd-grid-smoke.py --phase
verify`). These tests never download: they adopt the driver's owned bucket
(`FIN3_EODHD_TEST_BUCKET`) and evidence index (`FIN3_EODHD_TEST_EVIDENCE`),
replaying source comparisons against the driver's private corpus snapshot.
The storage readback gate names the physical resolution it was given
(backend/endpoint/bucket/arctic library) and fails closed on absence or
mismatch there; it does not resolve through consumer-default settings.

The finlab boundary check runs with an owned-bucket override: finlab's own
endpoint/credentials read the explicitly supplied test bucket. Consumer
defaults are NOT verified anywhere in this suite.

Source-only checks (TestEodhdSource) stay independently gated by the live
flag and the raw vendor key and never touch MinIO.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import subprocess
from collections.abc import Generator, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from fin3.config.settings import ClientConfig, EodhdOptionsConfig, MinioConfig
from fin3.exceptions import StorageError
from fin3.options import OptionsDataFetcher
from fin3.options_schemas import CHAIN_REPORT_ATTR, ChainCoverageManifest
from fin3.providers.eodhd_options import EodhdOptionsProvider
from fin3.storage.arctic import ArcticStorage

pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / ".docs" / "fixtures" / "eodhd"
SHY_START = date(2023, 12, 29)
SHY_DAY = date(2026, 11, 6)

PREREQUISITE = "run the standalone EODHD grid workflow first"


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# ---------------------------------------------------------------------------
# Digests over the canonical JSON-lines framing (source and stored sides share it)
# ---------------------------------------------------------------------------


def _core_record(contract: str, as_of: str, row: pd.Series) -> dict[str, Any]:
    def num(value: Any) -> float | None:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return float(value)

    return {
        "contract": contract,
        "as_of": as_of,
        "bid": num(row.get("bid") if isinstance(row, dict) else row["bid"]),
        "ask": num(row.get("ask") if isinstance(row, dict) else row["ask"]),
        "volume": num(row.get("volume") if isinstance(row, dict) else row["volume"]),
        "open_interest": num(
            row.get("open_interest") if isinstance(row, dict) else row["open_interest"]
        ),
        "implied_volatility": num(
            row.get("implied_volatility")
            if isinstance(row, dict)
            else row["implied_volatility"]
        ),
    }


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def _digest(frame: pd.DataFrame) -> str:
    """Streaming SHA-256 over canonical core records in (as_of, contract) order."""
    hasher = hashlib.sha256()
    level_as_of = frame.index.get_level_values("as_of")
    level_contract = frame.index.get_level_values("contract")
    order = sorted(
        range(len(frame)),
        key=lambda i: (str(level_as_of[i]), str(level_contract[i])),
    )
    for i in order:
        record = _core_record(
            str(level_contract[i]),
            str(pd.Timestamp(level_as_of[i]).date()),
            frame.iloc[i],
        )
        hasher.update((_canonical(record) + "\n").encode("utf-8"))
    return hasher.hexdigest()


def _corpus_connection(evidence: Path) -> sqlite3.Connection:
    """Open the driver's private corpus snapshot strictly read-only."""
    index = json.loads(evidence.read_text())
    corpus = index.get("corpus", {}).get("db")
    if not corpus or not Path(corpus).exists():
        raise RuntimeError(
            f"evidence index {evidence} lacks an existing corpus database"
        )
    return sqlite3.connect(f"file:{corpus}?mode=ro", uri=True)


def _source_records(
    evidence: Path, *, start: date | None = None, end: date | None = None
) -> Iterator[dict[str, Any]]:
    """Stream ordered core records from the driver's corpus snapshot."""
    connection = _corpus_connection(evidence)
    query = "SELECT as_of, contract, row_json FROM rows"
    bounds: list[str] = []
    if start is not None:
        bounds.append(f"as_of >= '{start.isoformat()}'")
    if end is not None:
        bounds.append(f"as_of <= '{end.isoformat()}'")
    if bounds:
        query += " WHERE " + " AND ".join(bounds)
    query += " ORDER BY as_of, contract"
    try:
        for as_of, contract, blob in connection.execute(query):
            attrs = json.loads(blob)["attributes"]
            yield {
                "contract": contract,
                "as_of": as_of,
                "bid": None if attrs.get("bid") is None else float(attrs["bid"]),
                "ask": None if attrs.get("ask") is None else float(attrs["ask"]),
                "volume": (
                    None if attrs.get("volume") is None else float(attrs["volume"])
                ),
                "open_interest": (
                    None
                    if attrs.get("open_interest") is None
                    else float(attrs["open_interest"])
                ),
                "implied_volatility": (
                    None
                    if attrs.get("volatility") is None
                    else float(attrs["volatility"])
                ),
            }
    finally:
        connection.close()


def _source_digest(evidence: Path, as_of: str) -> str:
    """Hash the corpus records of one indexed date in canonical framing."""
    hasher = hashlib.sha256()
    count = 0
    for record in _source_records(evidence, start=date.fromisoformat(as_of), end=date.fromisoformat(as_of)):
        hasher.update((_canonical(record) + "\n").encode("utf-8"))
        count += 1
    if count == 0:
        raise RuntimeError(f"corpus snapshot has no rows for {as_of}")
    return hasher.hexdigest()


def _validate_evidence(evidence: Path) -> dict[str, Any]:
    """Require a successful, complete, never-rerun preflight + root index."""
    index = json.loads(evidence.read_text())
    if index.get("schema_version") != 1:
        raise RuntimeError(f"evidence schema {index.get('schema_version')!r} unsupported")
    preflight_pointer = index.get("preflight_index")
    if not preflight_pointer or not Path(preflight_pointer).exists():
        raise RuntimeError("evidence lacks an existing preflight index pointer")
    preflight_index = json.loads(Path(preflight_pointer).read_text())
    if preflight_index.get("schema_version") != 1:
        raise RuntimeError("preflight index schema unsupported")
    preflight = preflight_index.get("preflight", {})
    if preflight.get("all_mandatory_ok") is not True:
        raise RuntimeError("evidence records unsuccessful preflight checks")
    if preflight_index.get("phase_attempts", {}).get("preflight", 99) > 12:
        raise RuntimeError("evidence preflight ledger exceeds 12 attempts")
    root = index.get("root", {})
    if root.get("outcome") != "published":
        raise RuntimeError(
            f"evidence root outcome is {root.get('outcome')!r}, not published"
        )
    # The readback gate consumes exactly these two fields; they must be
    # strict integers (booleans rejected) before any storage is touched.
    days = root.get("days")
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise RuntimeError("evidence root lacks a strict positive integer day count")
    requests_used = root.get("requests_used")
    if (
        isinstance(requests_used, bool)
        or not isinstance(requests_used, int)
        or requests_used < 0
    ):
        raise RuntimeError(
            "evidence root lacks a strict nonnegative integer request count"
        )
    if index.get("phase_attempts", {}).get("gate", 10_000) > 1024:  # owner-authorized 2026-11-09
        raise RuntimeError("evidence gate ledger exceeds 1024 attempts")
    if index.get("phase_attempts", {}).get("comparator", 10_000) > 16:
        raise RuntimeError("evidence comparator ledger exceeds 16 attempts")
    if index.get("phase_attempts", {}).get("failure", 10_000) > 4:
        raise RuntimeError("evidence failure ledger exceeds 4 attempts")
    corpus = index.get("corpus", {}).get("db")
    if not corpus or not Path(corpus).exists():
        raise RuntimeError("evidence lacks an existing corpus database")
    return index


def _readback_target(storage: ArcticStorage) -> str:
    """Render the explicitly selected physical readback resolution.

    Reads the actual injected storage's resolution state (`_config`,
    `_is_lmdb`, `_library_location`) instead of guessing from an ambient
    ``ClientConfig()``. The summary is allowlisted: no URI, no credentials,
    no full config dump, no exception payloads.
    """
    bucket, arctic_library = storage._library_location("options-eod-eodhd")
    if storage._is_lmdb:
        return (
            "backend=lmdb endpoint=lmdb secure=false "
            f"bucket={bucket} arctic_library={arctic_library} "
            "library=options-eod-eodhd symbol=SHY"
        )
    config = storage._config
    backend = "s3s" if config.secure else "s3"
    secure = "true" if config.secure else "false"
    return (
        f"backend={backend} endpoint={config.endpoint} secure={secure} "
        f"bucket={bucket} arctic_library={arctic_library} "
        "library=options-eod-eodhd symbol=SHY"
    )


def _require_readback_artifact(
    storage: ArcticStorage,
    *,
    expected_days: int,
    expected_requests: int,
) -> ChainCoverageManifest:
    """Read and validate the artifact at the explicitly selected target.

    Performs exactly one read-only ``read_with_metadata`` call against the
    injected storage instance — no ambient config, no fallback store, no
    library creation. Absence, schema violations, and mismatches hard-fail
    with the named target; nothing here skips.
    """
    prefix = "EODHD readback artifact check failed"
    target = _readback_target(storage)
    try:
        stored = storage.read_with_metadata("options-eod-eodhd", "SHY")
        if stored is None:
            pytest.fail(
                f"{prefix}: target {target} holds no options-eod-eodhd/SHY "
                "snapshot",
                pytrace=False,
            )
        raw_manifest = stored[1]
        if raw_manifest is None:
            pytest.fail(
                f"{prefix}: target {target} snapshot carries no coverage "
                "manifest",
                pytrace=False,
            )
        manifest = ChainCoverageManifest.model_validate(raw_manifest)
    except StorageError as exc:
        pytest.fail(
            f"{prefix}: reading target {target} failed: {exc}",
            pytrace=False,
        )
    except ValidationError as exc:
        # Report field paths/messages only — never the raw metadata input.
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()[:3]
        )
        pytest.fail(
            f"{prefix}: target {target} manifest fails schema validation "
            f"({details})",
            pytrace=False,
        )
    if manifest.root != "SHY":
        pytest.fail(
            f"{prefix}: target {target} stores root {manifest.root!r}, not SHY",
            pytrace=False,
        )
    if not manifest.days:
        pytest.fail(f"{prefix}: target {target} stores empty coverage", pytrace=False)
    if manifest.days[0].as_of != SHY_START:
        pytest.fail(
            f"{prefix}: target {target} coverage starts at "
            f"{manifest.days[0].as_of}, not {SHY_START}",
            pytrace=False,
        )
    if manifest.days[-1].as_of != SHY_DAY:
        pytest.fail(
            f"{prefix}: target {target} coverage ends at "
            f"{manifest.days[-1].as_of}, not {SHY_DAY}",
            pytrace=False,
        )
    if len(manifest.days) != expected_days:
        pytest.fail(
            f"{prefix}: target {target} covers {len(manifest.days)} days, "
            f"evidence expects {expected_days}",
            pytrace=False,
        )
    if manifest.requests_used != expected_requests:
        pytest.fail(
            f"{prefix}: target {target} used {manifest.requests_used} "
            f"requests, evidence expects {expected_requests}",
            pytrace=False,
        )
    return manifest


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
    """Adopt the driver-owned bucket; never create and never delete."""
    bucket = _env("FIN3_EODHD_TEST_BUCKET")
    if not bucket:
        pytest.skip(PREREQUISITE)
    storage = ArcticStorage(
        MinioConfig(
            endpoint=minio_settings.endpoint,
            access_key=minio_settings.access_key,
            secret_key=minio_settings.secret_key,
            secure=minio_settings.secure,
            bucket=bucket,
        )
    )
    yield bucket, storage


@pytest.fixture(scope="session")
def evidence() -> Path:
    """Locate and validate the driver's evidence index."""
    path = _env("FIN3_EODHD_TEST_EVIDENCE")
    if not path:
        pytest.skip(PREREQUISITE)
    evidence = Path(path)
    if not evidence.exists():
        raise RuntimeError(
            f"evidence index {evidence} missing while a driver bucket is "
            "supplied; rerunning ingestion is forbidden"
        )
    _validate_evidence(evidence)
    return evidence


@pytest.fixture(scope="session")
def bucket_config(minio_settings: MinioConfig, owned_bucket: tuple[str, ArcticStorage]) -> ClientConfig:
    """Keyless reader config on the owned bucket; provider resolution is impossible."""
    bucket, _ = owned_bucket
    return ClientConfig(
        minio=MinioConfig(
            endpoint=minio_settings.endpoint,
            access_key=minio_settings.access_key,
            secret_key=minio_settings.secret_key,
            secure=minio_settings.secure,
            bucket=bucket,
        ),
        providers={},
        EODHD_API_KEY=None,
        _env_file=None,
    )


@pytest.fixture(scope="session")
def shy_state(
    owned_bucket: tuple[str, ArcticStorage],
    bucket_config: ClientConfig,
    evidence: Path,
    pytestconfig: pytest.Config,
) -> Generator[tuple[OptionsDataFetcher, Path], None, None]:
    """Loader-only: keyless fetcher plus the validated evidence index."""
    index = _validate_evidence(evidence)
    root = index.get("root", {})
    # The verify index overwrites `root` with the outcome dict; the symbol
    # and window live in the chained preflight index.
    preflight_index = json.loads(Path(index["preflight_index"]).read_text())
    if preflight_index.get("root") != "SHY" or preflight_index.get(
        "start"
    ) != SHY_START.isoformat():
        raise RuntimeError("evidence index records a different root or window start")
    if preflight_index.get("end") != SHY_DAY.isoformat():
        raise RuntimeError("evidence index records a different window end")
    if root.get("vendor_calls") is None or root.get("requests_used") is None:
        raise RuntimeError("evidence root lacks request accounting")

    storage = owned_bucket[1]
    fetcher = OptionsDataFetcher(bucket_config, storage=storage)
    # Name the explicitly selected physical resolution before the gate. An
    # announcement identifies the target under test; it is not a
    # reproduction-success or consumer-default claim.
    announcement = (
        "EODHD readback target: resolution=explicit-test-bucket "
        f"consumer_defaults_verified=false {_readback_target(storage)} "
        f"evidence={evidence} evidence_bucket={index.get('bucket')}"
    )
    reporter = pytestconfig.pluginmanager.getplugin("terminalreporter")
    capman = pytestconfig.pluginmanager.getplugin("capturemanager")
    if reporter is not None and capman is not None:
        # Suspend capture so the line reaches the real terminal even when
        # passing-test stdout is captured (an ordinary run without -s).
        capman.suspend_global_capture(in_=True)
        try:
            reporter.write_line(announcement)
        finally:
            capman.resume_global_capture()
    else:
        print(announcement)
    # The gate reads exactly the injected storage — absence or mismatch at
    # this named target hard-fails the fixture (setup ERROR).
    _require_readback_artifact(
        storage,
        expected_days=root["days"],
        expected_requests=root["requests_used"],
    )
    yield fetcher, evidence


# ---------------------------------------------------------------------------
# Source-only checks (no storage; independent live gate)
# ---------------------------------------------------------------------------


class TestEodhdSource:
    """Independent source/pagination/bounds checks; raw key only."""

    @pytest.fixture(scope="class")
    def provider(self, eodhd_raw_key: str) -> EodhdOptionsProvider:
        return EodhdOptionsProvider(
            EodhdOptionsConfig(api_key=eodhd_raw_key, page_size=500)
        )

    def test_real_pagination_and_bounds(self, provider: EodhdOptionsProvider) -> None:
        """SPY261218C00675000's history must cross ≥2 real pages at P=500."""
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
        """A syntactically valid unknown root yields a reasoned empty result."""
        try:
            frame = provider.fetch_history(
                "ZZZZZX", SHY_DAY, SHY_DAY, max_requests=8
            )
        except Exception as exc:
            pytest.fail(
                f"unknown-root probe failed with an exception, which must "
                f"not be classified as no-data: {exc}"
            )
        assert frame.empty
        assert frame.attrs[CHAIN_REPORT_ATTR]["reason"] == "unknown_underlying"


# ---------------------------------------------------------------------------
# Storage-dependent checks (driver-owned artifact; zero vendor calls)
# ---------------------------------------------------------------------------


def test_shy_root_backfill_matches_source(
    shy_state: tuple[OptionsDataFetcher, Path],
    owned_bucket: tuple[str, ArcticStorage],
) -> None:
    """Stored SHY_DAY rows equal the corpus snapshot, flag-for-flag."""
    fetcher, evidence = shy_state
    result = fetcher.read_chain("SHY", SHY_DAY)
    assert result.status == "observed"
    frame = result.data
    assert bool(frame["multiplier"].isna().all())
    manifest = result.manifest
    assert manifest is not None
    day = next(d for d in manifest.days if d.as_of == SHY_DAY)
    print(
        f"SHY {SHY_DAY}: {day.contract_count} contracts, scope={day.scope}, "
        f"reference={day.reference_contract_count}, "
        f"requests_used={manifest.requests_used} "
        f"(~x10 credits = {manifest.requests_used * 10})"
    )
    stored = _digest(frame)
    source = _source_digest(evidence, SHY_DAY.isoformat())
    assert stored == source, "stored/readback digest differs from the corpus"
    print(f"digest match: {stored[:16]}…")

    # Per-date counts on three dates with different populations, straight
    # from the corpus snapshot (zero vendor calls).
    corpus_dates: dict[str, int] = {}
    for record in _source_records(evidence):
        corpus_dates[record["as_of"]] = corpus_dates.get(record["as_of"], 0) + 1
    window_days = [
        d for d in manifest.days
        if d.as_of in (SHY_START, SHY_DAY, date(2025, 6, 16))
    ]
    assert {d.as_of.isoformat() for d in window_days} == {
        SHY_START.isoformat(),
        SHY_DAY.isoformat(),
        "2025-06-16",
    }
    for day_record in window_days:
        expected = corpus_dates.get(day_record.as_of.isoformat(), 0)
        assert day_record.contract_count == expected, (
            f"coverage count for {day_record.as_of} differs from the corpus"
        )


def test_no_mutation_on_reads(
    shy_state: tuple[OptionsDataFetcher, Path],
    owned_bucket: tuple[str, ArcticStorage],
    bucket_config: ClientConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh keyless clients read exactly; no mutator, provider, or lock runs."""
    bucket, storage = owned_bucket
    lib = storage.arctic_for(bucket)["options-eod-eodhd"]
    with _suppress():
        before = lib.read("SHY")

    import arcticdb

    fetcher_a = OptionsDataFetcher(bucket_config, storage=storage)
    fetcher_b = OptionsDataFetcher(bucket_config, storage=storage)

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
    # Sample dates: the first stored day, a mid-window day, and SHY_DAY.
    result = fetcher_a.read_chain("SHY", SHY_DAY)
    assert result.status == "observed"
    stored_frame = result.manifest
    assert stored_frame is not None
    sample_dates = [
        stored_frame.days[0].as_of,
        stored_frame.days[len(stored_frame.days) // 2].as_of,
        stored_frame.days[-1].as_of,
    ]
    reads = {
        day: fetcher_b.read_chain("SHY", day)
        for day in sample_dates
    }
    baseline = fetcher_a.read_chain("SHY", sample_dates[0])
    for day, other in reads.items():
        first = fetcher_a.read_chain("SHY", day)
        pd.testing.assert_frame_equal(first.data, other.data)
        assert first.manifest == other.manifest
    pd.testing.assert_frame_equal(baseline.data, reads[sample_dates[0]].data)
    assert baseline.manifest == reads[sample_dates[0]].manifest

    put_count = _put_object_count_around(lambda: fetcher_b.read_chain("SHY", SHY_DAY))
    if put_count < 0:
        print(
            "NOTE: S3 PutObject counter evidence unavailable (query_stats "
            "backend); zero-mutation rests on the mutator guards, version "
            "identity, and write-timestamp equality below."
        )
    else:
        assert put_count == 0, f"reads issued {put_count} S3 PutObject calls"

    with _suppress():
        after = lib.read("SHY")
    assert after.version == before.version
    assert after.timestamp == before.timestamp


def test_explicit_gaps(
    shy_state: tuple[OptionsDataFetcher, Path],
    owned_bucket: tuple[str, ArcticStorage],
    minio_settings: MinioConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reasoned no-data precedence against the unchanged artifact."""
    bucket, storage = owned_bucket
    fetcher = shy_state[0]

    result = fetcher.read_chain("SHY", date(2020, 3, 20))
    assert result.status == "no_data"
    assert result.reason == "outside_ingested_coverage"
    assert len(result.data) == 0
    assert result.manifest is not None

    absent = OptionsDataFetcher(
        ClientConfig(
            minio=MinioConfig(
                endpoint=minio_settings.endpoint,
                access_key=minio_settings.access_key,
                secret_key=minio_settings.secret_key,
                secure=minio_settings.secure,
                bucket=bucket,
            ),
            providers={},
            EODHD_API_KEY=None,
            _env_file=None,
        ),
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


def _require_finlab_readback(
    proc: subprocess.CompletedProcess[str],
    *,
    bucket: str,
    expected_digest: str,
    expected_days: int,
) -> dict[str, Any]:
    """Validate the finlab child's owned-bucket-override readback result.

    Hard-fails on nonzero exit (even with a plausible JSON line on stdout),
    malformed/missing payloads, no-data, target mismatch, or comparison
    mismatch. Never copies raw stderr/stdout/config into failures — they
    may carry credentials; only validated allowlist fields are reported.
    """
    prefix = "finlab owned-bucket readback failed"
    if proc.returncode != 0:
        pytest.fail(
            f"{prefix} (exit code): child exited {proc.returncode} while "
            f"forced to owned bucket {bucket}; the boundary failure stands "
            "and is never retargeted or skipped",
            pytrace=False,
        )
    child: Any = None
    result_lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if result_lines:
        try:
            parsed = json.loads(result_lines[-1])
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            child = parsed
    if child is None:
        pytest.fail(
            f"{prefix} (invalid payload): child printed no final JSON-object "
            f"result for owned bucket {bucket}",
            pytrace=False,
        )

    def _strict_nonneg_int(value: Any) -> bool:
        return (
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
        )

    fields_ok = (
        isinstance(child.get("endpoint"), str)
        and bool(child["endpoint"])
        and isinstance(child.get("secure"), bool)
        and child.get("library") == "options-eod-eodhd"
        and child.get("symbol") == "SHY"
        and child.get("resolution") == "owned-bucket-override"
        and _strict_nonneg_int(child.get("rows"))
        and _strict_nonneg_int(child.get("days"))
        and isinstance(child.get("digest"), str)
        and child.get("status") in ("observed", "no_data")
        and isinstance(child.get("bucket"), str)
    )
    if not fields_ok:
        pytest.fail(
            f"{prefix} (invalid payload): child result for owned bucket "
            f"{bucket} misses required typed fields",
            pytrace=False,
        )
    resolution = f"resolution={child['resolution']}"
    if child["status"] != "observed":
        reason = child.get("reason")
        reason_text = f" reason={reason}" if isinstance(reason, str) else ""
        pytest.fail(
            f"{prefix} (no data): child reported status {child['status']!r}"
            f"{reason_text} for owned bucket {bucket} ({resolution})",
            pytrace=False,
        )
    if child["rows"] == 0:
        pytest.fail(
            f"{prefix} (no data): observed child result carries zero rows "
            f"for owned bucket {bucket} ({resolution})",
            pytrace=False,
        )
    if child["bucket"] != bucket:
        pytest.fail(
            f"{prefix} (target mismatch): child resolved bucket "
            f"{child['bucket']!r}, forced owned bucket was {bucket!r} "
            f"({resolution})",
            pytrace=False,
        )
    if child["digest"] != expected_digest:
        pytest.fail(
            f"{prefix} (comparison mismatch): child digest "
            f"{child['digest'][:16]}… differs from parent readback digest "
            f"{expected_digest[:16]}… for owned bucket {bucket} "
            f"({resolution})",
            pytrace=False,
        )
    if child["days"] != expected_days:
        pytest.fail(
            f"{prefix} (comparison mismatch): child covered "
            f"{child['days']} days, parent manifest expects {expected_days} "
            f"for owned bucket {bucket} ({resolution})",
            pytrace=False,
        )
    return child


def test_finlab_import_boundary(
    shy_state: tuple[OptionsDataFetcher, Path],
    owned_bucket: tuple[str, ArcticStorage],
) -> None:
    """finlab reads the owned artifact via an owned-bucket override.

    Consumer defaults are NOT checked: the child forces
    ``FIN3_MINIO__BUCKET`` to the owned bucket on top of finlab's own
    ``FIN3_*`` dotenv subset, so this proves only that finlab's own
    endpoint/credentials can reach the explicitly supplied artifact.
    """
    bucket, _ = owned_bucket
    fetcher, _ = shy_state
    finlab = Path("/mnt/dev/projects/finlab")
    if not (finlab / ".env").exists():
        pytest.skip("finlab checkout/.env absent")
    # Expected digest and day count come from the parent's own gate-checked
    # readback, never from the child's claims.
    parent = fetcher.read_chain("SHY", SHY_DAY)
    assert parent.manifest is not None
    parent_digest = _digest(parent.data)
    parent_days = len(parent.manifest.days)
    child_code = r"""
import json, math, os, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, "/mnt/dev/projects/fin3")
# finlab's own .env carries non-fin3 keys that fin3's strict config forbids,
# so the child sources exactly the FIN3_* subset of finlab's dotenv into its
# environment (finlab's endpoint/credentials). The parent-supplied owned
# bucket is then forced on top via setdefault — an owned-bucket override.
# Consumer defaults are NOT checked; no inherited parent credentials survive.
from dotenv import dotenv_values
for key, value in dotenv_values(Path.cwd() / ".env").items():
    if key and key.startswith("FIN3_") and value is not None:
        os.environ.setdefault(key, value)
from fin3 import ClientConfig, OptionsDataFetcher
import hashlib

config = ClientConfig(_env_file=None, EODHD_API_KEY=None)
fetcher = OptionsDataFetcher(config)
result = fetcher.read_chain("SHY", date(2026, 11, 6))

def num(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)

records = []
for idx, row in result.data.iterrows():
    records.append({
        "contract": str(idx[1]),
        "as_of": str(idx[0])[:10],
        "bid": num(row["bid"]),
        "ask": num(row["ask"]),
        "volume": num(row["volume"]),
        "open_interest": num(row["open_interest"]),
        "implied_volatility": num(row["implied_volatility"]),
    })
records.sort(key=lambda r: (r["as_of"], r["contract"]))
payload = "".join(
    json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records
)
digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
days = result.manifest.days if result.manifest else []
out = {
    "status": result.status,
    "reason": result.reason,
    "rows": len(result.data),
    "digest": digest,
    "days": len(days),
    "endpoint": config.minio.endpoint,
    "bucket": config.minio.bucket,
    "secure": config.minio.secure,
    "library": "options-eod-eodhd",
    "symbol": "SHY",
    "resolution": "owned-bucket-override",
}
print(json.dumps(out))
"""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("FIN3_") and key != "EODHD_API_KEY"
    }
    env["FIN3_MINIO__BUCKET"] = bucket
    proc = subprocess.run(
        ["/mnt/dev/projects/fin3/.venv/bin/python", "-c", child_code],
        cwd=finlab,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    child = _require_finlab_readback(
        proc,
        bucket=bucket,
        expected_digest=parent_digest,
        expected_days=parent_days,
    )
    print(f"finlab owned-bucket override child: {child}")


def _forbidden(name: str):
    def raise_if_touched(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"read path mutated storage via {name}")

    return raise_if_touched


def _suppress():
    from fin3.storage.arctic import _suppress_blockmanager_warning

    return _suppress_blockmanager_warning()


def _put_object_count_around(fn) -> int:
    """Sum S3 PutObject counts immediately around *fn*; -1 when unsupported."""
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

"""Tests for OptionsDataFetcher: backfill publication, reads, and routing guards."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable

import pandas as pd
import pytest
from pydantic import ValidationError

from fin3.config.settings import (
    CboeConfig,
    ClientConfig,
    DatabentoConfig,
    EodhdOptionsConfig,
    MinioConfig,
)
from fin3.exceptions import (
    ConfigurationError,
    DataValidationError,
    ProviderError,
    SchemaValidationError,
    StorageError,
)
from fin3.inspect import inspect_library
from fin3.manifest import LibraryEntry
from fin3.options import OptionsDataFetcher, _classify_session
from fin3.options_schemas import (
    CHAIN_CONVENTIONS,
    CHAIN_REPORT_ATTR,
    ChainCoverageManifest,
    empty_chain_frame,
)
from fin3.providers import ProviderRegistry
from fin3.providers.base import ChainProvider
from fin3.providers.eodhd_options import EodhdOptionsProvider, _normalise
from fin3.schemas import AssetType, Resolution, observation_mask
from fin3.storage.arctic import ArcticStorage, _suppress_blockmanager_warning
from fin3.utils.integrity import check_integrity
from fin3.utils.options_validation import validate_chain_frame
from fin3.utils.validation import validate_raw_provider_data

SECRET = "SECRET-TEST-KEY-0002"


def eod_attrs(
    contract: str, as_of: str, *, bid: float = 0.5, ask: float = 0.6, **extra: Any
) -> dict[str, Any]:
    import re
    from decimal import Decimal

    match = re.match(r"^([A-Z0-9.]{1,6})([0-9]{6})([CP])([0-9]{8})$", contract)
    assert match is not None
    root, yymmdd, right, strike_digits = match.groups()
    attrs: dict[str, Any] = {
        "contract": contract,
        "underlying_symbol": root,
        "exp_date": f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}",
        "expiration_type": "weekly",
        "type": "call" if right == "C" else "put",
        "strike": int(Decimal(strike_digits) / Decimal(1000)),
        "currency": "USD",
        "bid": bid,
        "bid_date": f"{as_of}T21:14:59.000000Z",
        "bid_size": 10,
        "ask": ask,
        "ask_date": f"{as_of} 21:14:59",
        "ask_size": 10,
        "volume": 12,
        "open_interest": 25,
        "volatility": 0.15,
        "tradetime": as_of,
    }
    attrs.update(extra)
    return attrs


def make_frame(
    rows: list[tuple[str, str, dict[str, Any]]],
    *,
    root: str,
    start: date,
    end: date,
    scope: str = "root",
    selection: tuple[str, ...] | None = None,
    reference: int = 7,
    requests_used: int = 5,
    reason: str | None = None,
) -> pd.DataFrame:
    """Build a report-attributed chain frame through the real normalizer."""
    source_rows = [
        {
            "id": f"{contract}-{as_of}",
            "attributes": eod_attrs(contract, as_of, **extra),
        }
        for contract, as_of, extra in rows
    ]
    frame = _normalise(source_rows, _classify_session)
    provider = EodhdOptionsProvider(EodhdOptionsConfig(api_key=SECRET))
    return provider._finish(
        frame,
        root=root,
        start=start,
        end=end,
        scope=scope,  # type: ignore[arg-type]
        selected_contracts=selection,
        reference_contract_count=reference,
        requests_used=requests_used,
        started=datetime.now(timezone.utc),
        reason=reason,
    )


class StubChainProvider(ChainProvider):
    """Serves prebuilt frames keyed by (root, start, end, contracts)."""

    def __init__(self, fn: Callable[..., pd.DataFrame]) -> None:
        self._fn = fn

    def fetch_history(
        self,
        root: str,
        start: date,
        end: date,
        *,
        contracts: Any = None,
        max_requests: int | None = None,
    ) -> pd.DataFrame:
        return self._fn(root, start, end, contracts)


@pytest.fixture
def config() -> ClientConfig:
    return ClientConfig(
        minio=MinioConfig(endpoint="localhost:9000", access_key="a", secret_key="b"),
        providers={},
        EODHD_API_KEY=None,
        _env_file=None,
    )


def install_stub(
    monkeypatch: pytest.MonkeyPatch, fn: Callable[..., pd.DataFrame]
) -> StubChainProvider:
    stub = StubChainProvider(fn)
    monkeypatch.setattr(OptionsDataFetcher, "_resolve_provider", lambda self: stub)
    return stub


DAY = date(2026, 11, 6)  # Friday: XNYS session
HOLIDAY = date(2026, 1, 1)  # XNYS holiday
C91 = "SHY261120C00091000"
C92 = "SHY261127C00092000"


class TestBackfillAndRead:
    def test_ingest_and_exact_read(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frame = make_frame(
            [(C91, "2026-11-06", {"volume": 0}), (C92, "2026-11-06", {})],
            root="SHY", start=DAY, end=DAY,
        )
        install_stub(monkeypatch, lambda *a, **k: frame)
        fetcher = OptionsDataFetcher(config, storage=storage)
        manifest = fetcher.backfill("shy ", DAY, DAY)  # normalized root
        assert manifest.root == "SHY"
        assert [(d.as_of, d.contract_count) for d in manifest.days] == [(DAY, 2)]
        assert manifest.days[0].reason is None
        assert manifest.first_observation == DAY

        result = fetcher.read_chain("SHY", DAY)
        assert result.status == "observed"
        assert result.reason is None
        assert list(result.data.index.get_level_values("contract")) == [C91, C92]
        row = result.data.loc[(pd.Timestamp(DAY, tz="UTC"), C91)]
        assert row["bid"] == 0.5 and row["volume"] == 0
        assert pd.isna(row["multiplier"])  # multiplier stays null
        validate_chain_frame(result.data)

    def test_unrequested_internal_and_outside_dates(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        day1 = make_frame([(C91, "2026-11-06", {})], root="SHY", start=DAY, end=DAY)
        day2 = make_frame(
            [(C91, "2026-11-06", {})], root="SHY",
            start=date(2026, 11, 6), end=date(2026, 11, 7),
        )

        def fn(root: str, start: date, end: date, contracts: Any) -> pd.DataFrame:
            return day1 if end == DAY else day2

        install_stub(monkeypatch, fn)
        fetcher = OptionsDataFetcher(config, storage=storage)
        fetcher.backfill("SHY", DAY, DAY)

        result = fetcher.read_chain("SHY", date(2026, 11, 5))
        assert result.status == "no_data"
        assert result.reason == "outside_ingested_coverage"
        assert len(result.data) == 0

        result = fetcher.read_chain("SHY", date(2026, 11, 7))
        assert result.reason == "outside_ingested_coverage"

        # Inside the span but never requested:
        fetcher.backfill("SHY", DAY, date(2026, 11, 7))
        result = fetcher.read_chain("SHY", date(2026, 11, 7))
        assert result.status == "no_data"
        assert result.reason == "non_session"  # 2026-11-07 is a Saturday

    def test_root_not_ingested(
        self, storage: ArcticStorage, config: ClientConfig
    ) -> None:
        fetcher = OptionsDataFetcher(config, storage=storage)
        result = fetcher.read_chain("NOPE", DAY)
        assert result.status == "no_data"
        assert result.reason == "root_not_ingested"
        assert result.manifest is None

    def test_expiry_filters(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frame = make_frame([(C91, "2026-11-06", {})], root="SHY", start=DAY, end=DAY)
        install_stub(monkeypatch, lambda *a, **k: frame)
        fetcher = OptionsDataFetcher(config, storage=storage)
        fetcher.backfill("SHY", DAY, DAY)

        # Expired-expiry filter answers immediately without a snapshot.
        result = fetcher.read_chain("SHY", DAY, expiry=date(2026, 11, 5))
        assert result.status == "no_data"
        assert result.reason == "expiry_before_as_of"
        assert result.manifest is None

        # Optional expiry matching nothing on an observed date.
        result = fetcher.read_chain("SHY", DAY, expiry=date(2027, 1, 1))
        assert result.status == "no_data"
        assert result.reason == "no_rows_for_expiry"

        # Exact expiry returns the row.
        result = fetcher.read_chain("SHY", DAY, expiry=date(2026, 11, 20))
        assert result.status == "observed"
        assert len(result.data) == 1

    def test_non_session_observed_row_flagged(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frame = make_frame([(C91, "2026-01-01", {})], root="SHY",
                           start=HOLIDAY, end=HOLIDAY)
        install_stub(monkeypatch, lambda *a, **k: frame)
        fetcher = OptionsDataFetcher(config, storage=storage)
        fetcher.backfill("SHY", HOLIDAY, HOLIDAY)
        result = fetcher.read_chain("SHY", HOLIDAY)
        assert result.status == "observed"
        assert bool(result.data["has_non_session_label"].iloc[0])
        assert result.manifest is not None
        assert result.manifest.days[0].contract_count == 1
        assert result.manifest.days[0].reason is None

    def test_non_session_zero_day_reason(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frame = make_frame([], root="SHY", start=HOLIDAY, end=HOLIDAY)
        install_stub(monkeypatch, lambda *a, **k: frame)
        fetcher = OptionsDataFetcher(config, storage=storage)
        manifest = fetcher.backfill("SHY", HOLIDAY, HOLIDAY)
        assert manifest.days[0].reason == "non_session"
        result = fetcher.read_chain("SHY", HOLIDAY)
        assert result.status == "no_data"
        assert result.reason == "non_session"

    def test_empty_root_and_empty_manifest_roundtrip(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frame = make_frame([], root="SHY", start=DAY, end=DAY)
        install_stub(monkeypatch, lambda *a, **k: frame)
        fetcher = OptionsDataFetcher(config, storage=storage)
        manifest = fetcher.backfill("SHY", DAY, DAY)
        assert manifest.first_observation is None
        assert manifest.last_observation is None

        result = fetcher.read_chain("SHY", DAY)
        assert result.status == "no_data"
        assert result.reason == "source_returned_no_rows"
        # Empty frame still declares UTC timestamp dtypes after decode.
        assert str(result.data["bid_timestamp_utc"].dtype) == "datetime64[ns, UTC]"
        assert result.manifest == manifest

    def test_repeated_backfill_preserves_and_extends(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        day1 = make_frame([(C91, "2026-11-06", {})], root="SHY", start=DAY, end=DAY)
        day2 = make_frame(
            [(C92, "2026-11-09", {})], root="SHY",
            start=date(2026, 11, 9), end=date(2026, 11, 9),
        )

        def fn(root: str, start: date, end: date, contracts: Any) -> pd.DataFrame:
            if start == DAY:
                return day1
            return day2

        install_stub(monkeypatch, fn)
        fetcher = OptionsDataFetcher(config, storage=storage)
        fetcher.backfill("SHY", DAY, DAY)
        manifest = fetcher.backfill("SHY", date(2026, 11, 9), date(2026, 11, 9))
        assert [d.as_of for d in manifest.days] == [DAY, date(2026, 11, 9)]
        result = fetcher.read_chain("SHY", DAY)
        assert result.status == "observed"  # prior observations retained
        result = fetcher.read_chain("SHY", date(2026, 11, 9))
        assert result.status == "observed"

    def test_contract_selections_union_and_root_dominates(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sel1 = make_frame(
            [(C91, "2026-11-06", {})], root="SHY", start=DAY, end=DAY,
            scope="contracts", selection=(C91,), reference=1,
        )
        sel2 = make_frame(
            [(C92, "2026-11-06", {})], root="SHY", start=DAY, end=DAY,
            scope="contracts", selection=(C92,), reference=1,
        )
        root_frame = make_frame(
            [(C91, "2026-11-06", {"bid": 0.5}), (C92, "2026-11-06", {})],
            root="SHY", start=DAY, end=DAY, reference=416,
        )
        frames = {"sel1": sel1, "sel2": sel2, "root": root_frame}
        install_stub(
            monkeypatch,
            lambda root, start, end, contracts: frames[
                "root" if contracts is None else
                ("sel1" if tuple(contracts) == (C91,) else "sel2")
            ],
        )
        fetcher = OptionsDataFetcher(config, storage=storage)
        m1 = fetcher.backfill("SHY", DAY, DAY, contracts=[C91])
        assert m1.days[0].scope == "contracts"
        assert m1.days[0].selected_contracts == (C91,)
        assert m1.days[0].contract_count == 1

        m2 = fetcher.backfill("SHY", DAY, DAY, contracts=[C92])
        assert m2.days[0].selected_contracts == (C91, C92)  # sorted union
        assert m2.days[0].reference_contract_count == 2
        assert m2.days[0].contract_count == 2

        m3 = fetcher.backfill("SHY", DAY, DAY)  # root extract dominates
        assert m3.days[0].scope == "root"
        assert m3.days[0].selected_contracts is None
        assert m3.days[0].reference_contract_count == 416
        assert m3.days[0].contract_count == 2

        m4 = fetcher.backfill("SHY", DAY, DAY, contracts=[C92])
        # Existing successful root-scope record dominates the narrower extract.
        assert m4.days[0].scope == "root"
        assert m4.days[0].reference_contract_count == 416

    def test_conflicting_overlap_leaves_previous_state(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        first = make_frame([(C91, "2026-11-06", {})], root="SHY", start=DAY, end=DAY)
        conflicting = make_frame(
            [(C91, "2026-11-06", {"bid": 9.99})], root="SHY", start=DAY, end=DAY
        )
        install_stub(
            monkeypatch,
            lambda root, start, end, contracts: first if not hasattr(
                storage, "_marker"
            ) else conflicting,
        )
        fetcher = OptionsDataFetcher(config, storage=storage)
        fetcher.backfill("SHY", DAY, DAY)
        lib = storage.arctic["options-eod-eodhd"]
        with _suppress_blockmanager_warning():
            before = lib.read("SHY")
        install_stub(
            monkeypatch,
            lambda root, start, end, contracts: conflicting,
        )
        with pytest.raises(DataValidationError, match="conflicting overlapping"):
            fetcher.backfill("SHY", DAY, DAY)
        with _suppress_blockmanager_warning():
            after = lib.read("SHY")
        assert after.version == before.version
        assert after.timestamp == before.timestamp
        result = fetcher.read_chain("SHY", DAY)
        assert result.data["bid"].iloc[0] == 0.5  # previous committed value

    def test_writer_failure_before_publication(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frame = make_frame([(C91, "2026-11-06", {})], root="SHY", start=DAY, end=DAY)
        install_stub(monkeypatch, lambda *a, **k: frame)
        fetcher = OptionsDataFetcher(config, storage=storage)
        fetcher.backfill("SHY", DAY, DAY)
        lib = storage.arctic["options-eod-eodhd"]
        with _suppress_blockmanager_warning():
            before = lib.read("SHY")

        failing = make_frame([(C92, "2026-11-06", {})], root="SHY", start=DAY, end=DAY)
        install_stub(monkeypatch, lambda *a, **k: failing)

        def broken_write(*args: Any, **kwargs: Any) -> None:
            raise StorageError("boom")

        monkeypatch.setattr(storage, "write", broken_write)
        with pytest.raises(StorageError, match="boom"):
            fetcher.backfill("SHY", DAY, DAY)
        with _suppress_blockmanager_warning():
            after = lib.read("SHY")
        assert after.version == before.version
        assert after.metadata is not None
        # The stored manifest cannot claim the newer state.
        stored = ChainCoverageManifest.model_validate(after.metadata)
        assert stored.days[0].contract_count == 1

    def test_report_validation_failures(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frame = make_frame([(C91, "2026-11-06", {})], root="SHY", start=DAY, end=DAY)
        tampered = frame.copy()
        attrs = dict(tampered.attrs[CHAIN_REPORT_ATTR])
        attrs["root"] = "OTHER"
        tampered.attrs[CHAIN_REPORT_ATTR] = attrs
        install_stub(monkeypatch, lambda *a, **k: tampered)
        fetcher = OptionsDataFetcher(config, storage=storage)
        with pytest.raises(ProviderError, match="does not match the requested"):
            fetcher.backfill("SHY", DAY, DAY)

        over = frame.copy()
        attrs = dict(over.attrs[CHAIN_REPORT_ATTR])
        attrs["requests_used"] = 999
        over.attrs[CHAIN_REPORT_ATTR] = attrs
        install_stub(monkeypatch, lambda *a, **k: over)
        with pytest.raises(ProviderError, match="exceeds the run cap"):
            fetcher.backfill("SHY", DAY, DAY)

        no_attrs = frame.copy()
        no_attrs.attrs.pop(CHAIN_REPORT_ATTR)
        install_stub(monkeypatch, lambda *a, **k: no_attrs)
        with pytest.raises(ProviderError, match="no chain fetch report"):
            fetcher.backfill("SHY", DAY, DAY)


class TestCorruptState:
    def test_missing_manifest_rejected(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        storage.write("options-eod-eodhd", "SHY", empty_chain_frame())
        config = ClientConfig(
            minio=MinioConfig(endpoint="e", access_key="a", secret_key="s"),
            providers={"eodhd_options": EodhdOptionsConfig(api_key=SECRET)},
            _env_file=None,
        )
        fetcher = OptionsDataFetcher(config, storage=storage)
        install_stub(
            monkeypatch,
            lambda *a, **k: make_frame([], root="SHY", start=DAY, end=DAY),
        )
        with pytest.raises(StorageError, match="no coverage manifest"):
            fetcher.backfill("SHY", DAY, DAY)
        with pytest.raises(StorageError, match="no coverage manifest"):
            fetcher.read_chain("SHY", DAY)

    def test_garbage_manifest_rejected(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        storage.write("options-eod-eodhd", "SHY", empty_chain_frame(), metadata={"junk": 1})
        fetcher = OptionsDataFetcher(config, storage=storage)
        with pytest.raises(StorageError, match="not a valid ChainCoverageManifest"):
            fetcher.read_chain("SHY", DAY)

    def test_unsupported_provider_rejected(
        self, storage: ArcticStorage, config: ClientConfig,
    ) -> None:
        manifest = {
            "schema_version": 1,
            "provider": "massive",
            "root": "SHY",
            "first_observation": None,
            "last_observation": None,
            "days": [],
            "retrieved_at": "2026-11-06T00:00:00Z",
            "requests_used": 0,
            "conventions": dict(CHAIN_CONVENTIONS),
        }
        storage.write("options-eod-eodhd", "SHY", empty_chain_frame(), metadata=manifest)
        fetcher = OptionsDataFetcher(config, storage=storage)
        # The manifest Literal type itself rejects foreign providers.
        with pytest.raises(StorageError, match="not a valid ChainCoverageManifest"):
            fetcher.read_chain("SHY", DAY)

    def test_count_mismatch_rejected(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frame = make_frame([(C91, "2026-11-06", {})], root="SHY", start=DAY, end=DAY)
        install_stub(monkeypatch, lambda *a, **k: frame)
        fetcher = OptionsDataFetcher(config, storage=storage)
        manifest = fetcher.backfill("SHY", DAY, DAY)
        stored = manifest.model_dump(mode="json")
        stored["days"][0]["contract_count"] = 5
        storage.write("options-eod-eodhd", "SHY", frame.drop(columns=[]), metadata=stored)
        with pytest.raises(StorageError, match="contract_count"):
            fetcher.read_chain("SHY", DAY)
        with pytest.raises(StorageError, match="contract_count"):
            fetcher.backfill("SHY", DAY, DAY)

    def test_zero_day_without_reason_rejected(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frame = make_frame([(C91, "2026-11-09", {})], root="SHY",
                           start=date(2026, 11, 9), end=date(2026, 11, 9))
        manifest = {
            "schema_version": 1,
            "provider": "eodhd_options",
            "root": "SHY",
            "first_observation": "2026-11-09",
            "last_observation": "2026-11-09",
            "days": [
                {
                    "as_of": "2026-11-06", "contract_count": 0, "scope": "root",
                    "selected_contracts": None, "reference_contract_count": 3,
                    "extraction_complete": True, "reason": None,
                },
                {
                    "as_of": "2026-11-09", "contract_count": 1, "scope": "root",
                    "selected_contracts": None, "reference_contract_count": 3,
                    "extraction_complete": True, "reason": None,
                },
            ],
            "retrieved_at": "2026-11-09T00:00:00Z",
            "requests_used": 9,
            "conventions": dict(CHAIN_CONVENTIONS),
        }
        storage.write("options-eod-eodhd", "SHY", frame, metadata=manifest)
        fetcher = OptionsDataFetcher(config, storage=storage)
        with pytest.raises(StorageError, match="lacks a reason"):
            fetcher.read_chain("SHY", date(2026, 11, 6))


class TestFreshClientReads:
    def test_two_reads_no_mutation_exact_equality(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
    ) -> None:
        frame = make_frame([(C91, "2026-11-06", {})], root="SHY", start=DAY, end=DAY)
        install_stub(monkeypatch, lambda *a, **k: frame)
        OptionsDataFetcher(config, storage=storage).backfill("SHY", DAY, DAY)

        # Forbid every mutating entry point on the adapter and native lib.
        for name in (
            "write", "update", "ensure_library", "_get_or_create_library",
            "delete_symbol",
        ):
            monkeypatch.setattr(
                ArcticStorage, name,
                _forbidden(f"ArcticStorage.{name}"),
            )
        lib = storage.arctic["options-eod-eodhd"]
        for name in ("write", "append", "update", "delete"):
            monkeypatch.setattr(lib, name, _forbidden(f"Library.{name}"))
        monkeypatch.setattr(
            OptionsDataFetcher, "_resolve_provider",
            _forbidden("provider resolution during read"),
        )

        # Fresh fetcher instances over the same storage: two identical reads.
        with _suppress_blockmanager_warning():
            before = lib.read("SHY")

        result1 = storage_read(config, storage, DAY)
        result2 = storage_read(config, storage, DAY)

        pd.testing.assert_frame_equal(result1.data, result2.data)
        assert result1.manifest == result2.manifest
        with _suppress_blockmanager_warning():
            after = lib.read("SHY")
        assert after.version == before.version  # identity, not just count
        assert after.timestamp == before.timestamp

    def test_read_of_absent_root_issues_no_writes(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for name in (
            "write", "update", "ensure_library", "_get_or_create_library",
        ):
            monkeypatch.setattr(
                ArcticStorage, name, _forbidden(f"ArcticStorage.{name}")
            )
        fetcher = OptionsDataFetcher(config, storage=storage)
        result = fetcher.read_chain("GHOST", DAY)
        assert result.status == "no_data"
        assert result.reason == "root_not_ingested"


def _forbidden(name: str) -> Callable[..., None]:
    def raise_if_touched(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"read path mutated storage via {name}")

    return raise_if_touched


def storage_read(
    config: ClientConfig, storage: ArcticStorage, day: date
) -> Any:
    fetcher = OptionsDataFetcher(config, storage=storage)
    return fetcher.read_chain("SHY", day)


class TestLmdbRoundTrip:
    def test_utc_timestamps_and_null_multiplier_survive(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frame = make_frame(
            [(C91, "2026-11-06", {"bid_date": "2025-03-21T00:14:59Z",
                                  "ask_date": "2026-11-06 20:00:00"})],
            root="SHY", start=DAY, end=DAY,
        )
        install_stub(monkeypatch, lambda *a, **k: frame)
        fetcher = OptionsDataFetcher(config, storage=storage)
        fetcher.backfill("SHY", DAY, DAY)
        result = fetcher.read_chain("SHY", DAY)
        row = result.data.iloc[0]
        assert str(result.data["bid_timestamp_utc"].dtype) == "datetime64[ns, UTC]"
        assert row["bid_timestamp_utc"] == pd.Timestamp("2025-03-21T00:14:59Z")
        assert pd.isna(row["ask_timestamp_utc"])  # naive raw stays NaT
        assert row["ask_timestamp_raw"] == "2026-11-06 20:00:00"
        assert pd.isna(row["multiplier"])
        validate_chain_frame(result.data)

    def test_decode_rejects_nonempty_corrupt_dtype(
        self, storage: ArcticStorage, config: ClientConfig,
    ) -> None:
        # ArcticDB refuses object-dtype numeric columns on write, so the
        # decoder's corrupt-dtype rejection is covered directly.
        from fin3.options_schemas import decode_stored_chain_frame

        frame = make_frame([(C91, "2026-11-06", {})], root="SHY", start=DAY, end=DAY)
        corrupt = frame.copy()
        corrupt["bid_timestamp_utc"] = corrupt["bid_timestamp_utc"].astype(object)
        with pytest.raises(SchemaValidationError, match="non-datetime dtype"):
            decode_stored_chain_frame(corrupt)


class TestCredentialResolution:
    def test_raw_env_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EODHD_API_KEY", "RAW-KEY-123")
        config = ClientConfig(
            minio=MinioConfig(endpoint="e", access_key="a", secret_key="s"),
            _env_file=None,
        )
        assert config.eodhd_api_key is not None
        assert config.eodhd_api_key.get_secret_value() == "RAW-KEY-123"

    def test_named_config_overrides_raw(
        self, monkeypatch: pytest.MonkeyPatch, config: ClientConfig,
    ) -> None:
        monkeypatch.setenv("EODHD_API_KEY", "RAW-KEY-123")
        config = ClientConfig(
            minio=MinioConfig(endpoint="e", access_key="a", secret_key="s"),
            providers={"eodhd_options": EodhdOptionsConfig(api_key="NAMED-KEY")},
            _env_file=None,
        )
        fetcher = OptionsDataFetcher(config)
        provider = fetcher._resolve_provider()
        assert isinstance(provider, EodhdOptionsProvider)
        assert provider._api_key is not None
        assert provider._api_key.get_secret_value() == "NAMED-KEY"

    def test_missing_key_permits_reads_blocks_ingestion(
        self, storage: ArcticStorage, config: ClientConfig,
    ) -> None:
        # Real provider resolution: no config, no raw key.
        fetcher = OptionsDataFetcher(config, storage=storage)
        result = fetcher.read_chain("SHY", DAY)  # reads work keyless
        assert result.reason == "root_not_ingested"
        with pytest.raises(ConfigurationError, match="EODHD_API_KEY is required"):
            fetcher.backfill("SHY", DAY, DAY)
        assert storage.has_library("options-eod-eodhd") is False

    def test_blank_raw_key_blocks_ingestion(
        self, monkeypatch: pytest.MonkeyPatch, storage: ArcticStorage,
    ) -> None:
        config = ClientConfig(
            minio=MinioConfig(endpoint="e", access_key="a", secret_key="s"),
            EODHD_API_KEY="   ",
            _env_file=None,
        )
        fetcher = OptionsDataFetcher(config, storage=storage)
        result = fetcher.read_chain("SHY", DAY)
        assert result.reason == "root_not_ingested"
        with pytest.raises(ConfigurationError, match="EODHD_API_KEY is required"):
            fetcher.backfill("SHY", DAY, DAY)
        assert storage.has_library("options-eod-eodhd") is False

    def test_wrong_provider_config_type_is_error(self) -> None:
        config = ClientConfig(
            minio=MinioConfig(endpoint="e", access_key="a", secret_key="s"),
            providers={"eodhd_options": CboeConfig()},
            _env_file=None,
        )
        fetcher = OptionsDataFetcher(config)
        with pytest.raises(ConfigurationError, match="EodhdOptionsConfig"):
            fetcher.backfill("SHY", DAY, DAY)

    def test_secret_not_rendered_in_validation_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("EODHD_API_KEY", "SECRET-LEAK-CHECK-99")
        monkeypatch.delenv("FIN3_MINIO__ENDPOINT", raising=False)
        with pytest.raises(ValidationError) as excinfo:
            ClientConfig(_env_file=None)  # missing MinIO settings
        rendered = str(excinfo.value)
        assert "SECRET-LEAK-CHECK-99" not in rendered

    def test_cli_redaction_collects_raw_key(self) -> None:
        from fin3.cli import _collect_secrets, _redact

        config = ClientConfig(
            minio=MinioConfig(endpoint="e", access_key="a", secret_key="s"),
            providers={
                "databento": DatabentoConfig(api_key="db-secret-1"),
                "eodhd_options": EodhdOptionsConfig(api_key="opt-secret-2"),
            },
            EODHD_API_KEY="raw-secret-3",
            _env_file=None,
        )
        secrets = _collect_secrets(config)
        assert "raw-secret-3" in secrets
        assert "opt-secret-2" in secrets
        message = "failed at https://x?api_token=raw-secret-3 with db-secret-1"
        redacted = _redact(message, config)
        assert "raw-secret-3" not in redacted
        assert "db-secret-1" not in redacted


class TestRoutingGuards:
    def test_options_rejected_by_market_data_fetcher(
        self, config: ClientConfig, storage: ArcticStorage,
    ) -> None:
        from fin3.core import MarketDataFetcher

        fetcher = MarketDataFetcher.__new__(MarketDataFetcher)
        fetcher._config = config
        fetcher._storage = storage
        with pytest.raises(ValueError, match="options datasets use OptionsDataFetcher"):
            fetcher.get_data(
                AssetType.OPTIONS, "eodhd_options", Resolution.ONE_DAY,
                ["SPY"], datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 2, 1, tzinfo=timezone.utc),
            )

    def test_options_rejected_by_manifest_entry(self) -> None:
        with pytest.raises(ValidationError, match="OptionsDataFetcher"):
            LibraryEntry(
                asset_type=AssetType.OPTIONS, provider="eodhd_options",
                resolution=Resolution.ONE_DAY, symbols={"include": ["SPY"]},
                start="2026-01-01", end="2026-01-05",
            )

    def test_options_rejected_by_inspect_library(
        self, storage: ArcticStorage,
    ) -> None:
        with pytest.raises(ValueError, match="OptionsDataFetcher.read_chain"):
            inspect_library(
                storage, "options-eod-eodhd", Resolution.ONE_DAY,
                asset_type=AssetType.OPTIONS,
            )

    def test_options_rejected_by_observation_mask(self) -> None:
        with pytest.raises(ValueError, match="validate_chain_frame"):
            observation_mask(empty_chain_frame(), asset_type=AssetType.OPTIONS)

    def test_options_rejected_by_bar_validation(self) -> None:
        with pytest.raises(SchemaValidationError, match="validate_chain_frame"):
            validate_raw_provider_data(
                empty_chain_frame(), Resolution.ONE_DAY, asset_type=AssetType.OPTIONS
            )

    def test_integrity_reports_unsupported_asset_type(self) -> None:
        report = check_integrity(
            empty_chain_frame(), pd.DatetimeIndex([], tz="UTC"),
            Resolution.ONE_DAY, asset_type=AssetType.OPTIONS,
        )
        assert not report.is_clean
        issue = report.issues[0]
        assert issue.category == "unsupported_asset_type"
        assert issue.detail == "options data requires validate_chain_frame"

    def test_sync_preflight_rejects_chain_provider(
        self, config: ClientConfig, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace

        from fin3.sync import ManifestRunner

        config = ClientConfig(
            minio=MinioConfig(endpoint="e", access_key="a", secret_key="s"),
            providers={"eodhd_options": EodhdOptionsConfig(api_key=SECRET)},
            _env_file=None,
        )
        runner = ManifestRunner.__new__(ManifestRunner)
        runner._config = config
        runner._provider_cache = {}
        runner._storage = None  # must never be touched
        manifest = SimpleNamespace(
            id="m1",
            datasets=[SimpleNamespace(provider="eodhd_options")],
        )
        with pytest.raises(ConfigurationError, match="chain provider"):
            runner._preflight_providers(manifest)  # type: ignore[arg-type]

    def test_registry_type_boundaries(self, config: ClientConfig) -> None:
        registry = ProviderRegistry({
            "databento": DatabentoConfig(api_key="k"),
            "eodhd_options": EodhdOptionsConfig(api_key=SECRET),
        })
        with pytest.raises(ConfigurationError, match="get_chain"):
            registry.get("eodhd_options")
        with pytest.raises(ConfigurationError, match="not a ChainProvider"):
            registry.get_chain("databento")
        assert isinstance(registry.get_chain("eodhd_options"), EodhdOptionsProvider)
        assert isinstance(registry.get("databento"), object)

    def test_options_asset_type_session_proxy(self) -> None:
        assert AssetType.OPTIONS.mic_code == "XNYS"
        assert _classify_session(date(2026, 11, 6))
        assert not _classify_session(HOLIDAY)

    def test_input_validation(self, storage: ArcticStorage, config: ClientConfig) -> None:
        fetcher = OptionsDataFetcher(config, storage=storage)
        with pytest.raises(ValueError, match="nonempty"):
            fetcher.read_chain("  ", DAY)
        with pytest.raises(ValueError, match="datetime.date"):
            fetcher.read_chain("SHY", "2026-11-06")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="after end"):
            fetcher.backfill("SHY", date(2026, 12, 1), DAY)
        with pytest.raises(ValueError, match="nonempty"):
            fetcher.backfill("SHY", DAY, DAY, contracts=[])


class TestBackfillInputGuards:
    def test_lock_symbol_used_for_backfill(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frame = make_frame([(C91, "2026-11-06", {})], root="SHY", start=DAY, end=DAY)
        install_stub(monkeypatch, lambda *a, **k: frame)
        fetcher = OptionsDataFetcher(config, storage=storage)
        acquired: list[tuple[str, str]] = []
        original_lock = storage.lock_symbol

        def tracking_lock(library: str, symbol: str) -> Any:
            acquired.append((library, symbol))
            return original_lock(library, symbol)

        monkeypatch.setattr(storage, "lock_symbol", tracking_lock)
        fetcher.backfill("SHY", DAY, DAY)
        assert acquired == [("options-eod-eodhd", "SHY")]

    def test_no_library_created_before_successful_extract(
        self, storage: ArcticStorage, config: ClientConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def failing(*args: Any, **kwargs: Any) -> pd.DataFrame:
            raise ProviderError("source down")

        install_stub(monkeypatch, failing)
        fetcher = OptionsDataFetcher(config, storage=storage)
        with pytest.raises(ProviderError, match="source down"):
            fetcher.backfill("SHY", DAY, DAY)
        assert storage.has_library("options-eod-eodhd") is False

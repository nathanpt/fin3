"""Options EOD ingestion and read API.

``OptionsDataFetcher`` is the only sanctioned path for options chains: 
``backfill`` is the explicitly mutating operation (extract → merge →
validate → one committed data/coverage write per root symbol), while
``read_chain`` is strictly read-only — it never resolves an API key,
acquires a write lock, bootstraps metadata, or creates anything.

Chains never pass through OHLCV padding, aggregation, or lifecycle
bootstrap; coverage metadata lives in the symbol's ArcticDB metadata, not
in ``fin3-metadata``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Literal

import exchange_calendars
import pandas as pd
import structlog

from fin3.config.settings import ClientConfig, EodhdOptionsConfig
from fin3.exceptions import (
    ConfigurationError,
    DataValidationError,
    ProviderError,
    StorageError,
)
from fin3.options_schemas import (
    CHAIN_CONVENTIONS,
    CHAIN_REPORT_ATTR,
    OPTIONS_EOD_LIBRARY,
    REASON_EXPIRY_BEFORE_AS_OF,
    REASON_EXPIRY_BEFORE_WINDOW,
    REASON_NO_CONTRACTS,
    REASON_NO_ROWS,
    REASON_NO_ROWS_FOR_EXPIRY,
    REASON_OUTSIDE_COVERAGE,
    REASON_DATE_NOT_INGESTED,
    REASON_ROOT_NOT_INGESTED,
    REASON_UNKNOWN_UNDERLYING,
    ChainCoverageManifest,
    ChainDateCoverage,
    ChainFetchReport,
    ChainResult,
    decode_stored_chain_frame,
    empty_chain_frame,
)
from fin3.providers.base import ChainProvider
from fin3.providers.eodhd_options import EodhdOptionsProvider
from fin3.storage.arctic import ArcticStorage
from fin3.utils.options_validation import validate_chain_frame

logger = structlog.get_logger(__name__)

_PRECISE_REASONS = frozenset(
    {
        REASON_UNKNOWN_UNDERLYING,
        REASON_NO_CONTRACTS,
        REASON_EXPIRY_BEFORE_WINDOW,
    }
)

PROVIDER_NAME: Literal["eodhd_options"] = "eodhd_options"


def _classify_session(day: date) -> bool:
    """Classify *day* against the XNYS session-label proxy.

    Raises ``ProviderError`` when the proxy's calendar coverage cannot
    classify the date — no invented booleans.
    """
    calendar = exchange_calendars.get_calendar("XNYS")
    try:
        return bool(calendar.is_session(pd.Timestamp(day)))
    except ValueError as exc:
        raise ProviderError(
            "XNYS session proxy cannot classify source observation dates"
        ) from exc


def _parse_manifest_metadata(metadata: object) -> ChainCoverageManifest:
    """Parse and strictly validate stored manifest metadata."""
    if not isinstance(metadata, dict):
        raise StorageError("stored options metadata is not a mapping")
    try:
        manifest = ChainCoverageManifest.model_validate(metadata)
    except Exception as exc:
        raise StorageError(
            f"stored options metadata is not a valid ChainCoverageManifest: {exc}"
        ) from exc
    if manifest.conventions != CHAIN_CONVENTIONS:
        raise StorageError(
            "stored options manifest conventions do not match the required "
            "exact convention set"
        )
    for day in manifest.days:
        if not day.extraction_complete:
            raise StorageError(
                f"stored coverage for {day.as_of} is not marked extraction_complete"
            )
    return manifest


def _validate_manifest_consistency(
    frame: pd.DataFrame, manifest: ChainCoverageManifest
) -> None:
    """Check stored manifest/frame agreement; corrupt state raises StorageError."""
    as_of_series = frame.index.get_level_values("as_of")
    observed = sorted({ts.date() for ts in as_of_series}) if len(frame) else []
    coverage_days = [day.as_of for day in manifest.days]
    if len(set(coverage_days)) != len(coverage_days) or coverage_days != sorted(
        coverage_days
    ):
        raise StorageError(
            "stored coverage days are not sorted and unique by as_of"
        )
    missing = [day for day in observed if day not in set(coverage_days)]
    if missing:
        raise StorageError(
            "observation dates missing coverage records: "
            f"{[d.isoformat() for d in missing]}"
        )
    per_day = (
        frame.index.get_level_values("as_of").value_counts()
        if len(frame)
        else pd.Series(dtype=int)
    )
    for day in manifest.days:
        actual = int(per_day.get(pd.Timestamp(day.as_of, tz="UTC"), 0))
        if actual != day.contract_count:
            raise StorageError(
                f"coverage contract_count for {day.as_of} is "
                f"{day.contract_count}, stored data holds {actual}"
            )
        if day.contract_count == 0 and day.reason is None:
            raise StorageError(
                f"zero-count coverage day {day.as_of} lacks a reason"
            )
        if day.contract_count > 0 and day.reason is not None:
            raise StorageError(
                f"observed coverage day {day.as_of} carries a zero-day reason"
            )
        if day.scope == "contracts":
            selected = day.selected_contracts
            if not selected or list(selected) != sorted(set(selected)):
                raise StorageError(
                    f"contract-scope coverage day {day.as_of} has invalid "
                    "selected_contracts"
                )
        elif day.selected_contracts is not None:
            raise StorageError(
                f"root-scope coverage day {day.as_of} must not carry "
                "selected_contracts"
            )
    first = min(observed) if observed else None
    last = max(observed) if observed else None
    if manifest.first_observation != first or manifest.last_observation != last:
        raise StorageError(
            "manifest first/last observation do not match stored chain data"
        )


class OptionsDataFetcher:
    """Root-snapshot publisher and read-only accessor for options EOD chains."""

    def __init__(self, config: ClientConfig, *, storage: ArcticStorage | None = None) -> None:
        """Store config and storage; no provider, bucket, or metadata work.

        Parameters
        ----------
        config : ClientConfig
            Top-level client configuration (MinIO/lock plus optional raw
            ``EODHD_API_KEY`` and options provider config).
        storage : ArcticStorage or None
            Storage override (tests inject LMDB); default builds
            ``ArcticStorage(config.minio, lock=config.lock)``.
        """
        self._config = config
        self._storage = (
            storage if storage is not None else ArcticStorage(config.minio, lock=config.lock)
        )
        self._provider: ChainProvider | None = None

    # -- provider resolution ---------------------------------------------

    def _resolve_provider(self) -> ChainProvider:
        """Lazily resolve the options provider with credentials applied.

        A configured ``EodhdOptionsConfig`` under ``eodhd_options`` is used
        as-is when it carries a key; otherwise it is copied with the raw
        secret. With no provider config, the default config is constructed
        with the raw secret. Wrong config types are configuration errors,
        never fallbacks. Missing keys raise at fetch time (reads unaffected).
        """
        if self._provider is not None:
            return self._provider
        raw_key = self._config.eodhd_api_key
        if raw_key is not None and not raw_key.get_secret_value().strip():
            # Blank raw values are harmless for reads; when ingestion
            # resolves the key they behave as missing (the provider raises
            # ConfigurationError at fetch time).
            raw_key = None
        configured = self._config.providers.get(PROVIDER_NAME)
        if configured is not None:
            if not isinstance(configured, EodhdOptionsConfig):
                raise ConfigurationError(
                    "provider 'eodhd_options' must be configured with "
                    "EodhdOptionsConfig"
                )
            config = configured
            if config.api_key is None or not config.api_key.get_secret_value().strip():
                config = config.model_copy(update={"api_key": raw_key})
        else:
            config = EodhdOptionsConfig(api_key=raw_key)
        self._provider = EodhdOptionsProvider(config)
        return self._provider

    def _effective_cap(self, max_requests: int | None) -> int:
        if max_requests is not None:
            return max_requests
        configured = self._config.providers.get(PROVIDER_NAME)
        if isinstance(configured, EodhdOptionsConfig):
            return configured.max_requests()
        return EodhdOptionsConfig().max_requests()

    # -- input validation ---------------------------------------------------

    @staticmethod
    def _normalize_root(root: str) -> str:
        if not isinstance(root, str):
            raise ValueError("root must be a string")
        normalized = root.strip().upper()
        if not normalized:
            raise ValueError("root must be nonempty")
        return normalized

    @staticmethod
    def _validate_dates(start: date, end: date) -> None:
        if isinstance(start, datetime) or isinstance(end, datetime):
            raise ValueError("start/end must be datetime.date, not datetime")
        if not isinstance(start, date) or not isinstance(end, date):
            raise ValueError("start/end must be datetime.date")
        if start > end:
            raise ValueError(f"start {start} is after end {end}")

    @staticmethod
    def _validate_as_of(as_of: date) -> date:
        if isinstance(as_of, datetime) or not isinstance(as_of, date):
            raise ValueError("as_of must be a datetime.date")
        return as_of

    # -- backfill -----------------------------------------------------------

    def backfill(
        self,
        root: str,
        start: date,
        end: date,
        *,
        contracts: Sequence[str] | None = None,
        max_requests: int | None = None,
    ) -> ChainCoverageManifest:
        """Extract, merge, and publish one root snapshot version.

        Holds the per-symbol lock through extraction, old-state read,
        validation, and the single-symbol publication. A failure before
        ``write()`` leaves existing data/coverage intact.
        """
        symbol = self._normalize_root(root)
        self._validate_dates(start, end)
        if contracts is not None:
            if isinstance(contracts, str) or not isinstance(contracts, Sequence):
                raise ValueError("contracts must be a sequence of strings or None")
            if not contracts:
                raise ValueError("contracts selection must be nonempty when provided")
        cap = self._effective_cap(max_requests)
        provider = self._resolve_provider()

        with self._storage.lock_symbol(OPTIONS_EOD_LIBRARY, symbol):
            frame = provider.fetch_history(
                symbol,
                start,
                end,
                contracts=contracts,
                max_requests=max_requests,
            )
            report = self._consume_report(frame, symbol, start, end, contracts, cap)
            existing = self._read_stored_state(symbol)
            merged_frame, merged_days = self._merge(
                existing, frame, report, start, end
            )
            validate_chain_frame(merged_frame)
            manifest = self._build_manifest(
                symbol, merged_frame, merged_days, report
            )
            _validate_manifest_consistency(merged_frame, manifest)
            self._storage.write(
                OPTIONS_EOD_LIBRARY,
                symbol,
                merged_frame,
                metadata=manifest.model_dump(mode="json"),
            )
        logger.info(
            "options.backfilled",
            root=symbol,
            start=start.isoformat(),
            end=end.isoformat(),
            rows=len(merged_frame),
            requests_used=report.requests_used,
        )
        return manifest

    def _consume_report(
        self,
        frame: pd.DataFrame,
        symbol: str,
        start: date,
        end: date,
        contracts: Sequence[str] | None,
        cap: int,
    ) -> ChainFetchReport:
        """Validate the mandatory provider report against the actual call."""
        raw = frame.attrs.get(CHAIN_REPORT_ATTR)
        if raw is None:
            raise ProviderError(
                "provider returned no chain fetch report in frame attrs"
            )
        try:
            report = ChainFetchReport.model_validate(raw)
        except Exception as exc:
            raise ProviderError(
                f"provider chain fetch report is invalid: {exc}"
            ) from exc
        if report.root != symbol or report.start != start or report.end != end:
            raise ProviderError(
                "provider chain fetch report does not match the requested "
                "root/window"
            )
        expected_scope: Literal["root", "contracts"] = (
            "contracts" if contracts is not None else "root"
        )
        if report.scope != expected_scope:
            raise ProviderError(
                f"provider chain fetch report scope {report.scope!r} does not "
                f"match the call ({expected_scope!r})"
            )
        if contracts is None:
            if report.selected_contracts is not None:
                raise ProviderError(
                    "root-scope report must not carry selected_contracts"
                )
        else:
            if report.selected_contracts is None:
                raise ProviderError(
                    "contract-scope report must carry selected_contracts"
                )
            if tuple(report.selected_contracts) != tuple(
                sorted({c.strip().upper() for c in contracts})
            ):
                raise ProviderError(
                    "contract-scope report selection does not match the call"
                )
        if not report.extraction_complete:
            raise ProviderError("provider report is not extraction_complete")
        if report.retrieved_at.tzinfo is None:
            raise ProviderError("provider report retrieved_at must be UTC-aware")
        if report.requests_used > cap:
            raise ProviderError(
                f"provider report requests_used {report.requests_used} exceeds "
                f"the run cap {cap}"
            )
        return report

    def _read_stored_state(
        self, symbol: str
    ) -> tuple[pd.DataFrame, ChainCoverageManifest] | None:
        """Read and fully validate the existing root snapshot, or None."""
        result = self._storage.read_with_metadata(OPTIONS_EOD_LIBRARY, symbol)
        if result is None:
            return None
        stored_frame, metadata = result
        if not metadata:
            raise StorageError(
                f"stored options root {symbol!r} has no coverage manifest"
            )
        manifest = _parse_manifest_metadata(metadata)
        if manifest.root != symbol:
            raise StorageError(
                f"stored options manifest root {manifest.root!r} does not "
                f"match symbol {symbol!r}"
            )
        frame = decode_stored_chain_frame(stored_frame)
        validate_chain_frame(frame)
        _validate_manifest_consistency(frame, manifest)
        return frame, manifest

    def _merge(
        self,
        existing: tuple[pd.DataFrame, ChainCoverageManifest] | None,
        new_frame: pd.DataFrame,
        report: ChainFetchReport,
        start: date,
        end: date,
    ) -> tuple[pd.DataFrame, dict[date, ChainDateCoverage]]:
        """Merge the new extract under prior observations and coverage.

        Equal overlapping rows are harmless; conflicting overlapping rows
        raise ``DataValidationError`` naming the key and leave storage
        unchanged. DataFrame attrs are ignored for row comparison (the
        report is consumed explicitly).
        """
        if existing is None:
            base = empty_chain_frame()
            days: dict[date, ChainDateCoverage] = {}
        else:
            base, base_manifest = existing
            days = {day.as_of: day for day in base_manifest.days}

        if base.empty:
            merged = new_frame.copy()
        elif new_frame.empty:
            merged = base.copy()
        else:
            self._check_overlaps(base, new_frame)
            combined = pd.concat([base, new_frame])
            merged = combined[~combined.index.duplicated(keep="first")].sort_index()
            for column in merged.columns:
                merged[column] = merged[column].astype(base[column].dtype)

        # Coverage: requested calendar dates only are recomputed; coverage
        # for prior days outside the request is preserved as-is.
        day = start
        new_days: dict[date, ChainDateCoverage] = dict(days)
        while day <= end:
            as_of_ts = pd.Timestamp(day, tz="UTC")
            if merged.empty:
                actual = 0
            else:
                level = merged.index.get_level_values("as_of")
                actual = int((level == as_of_ts).sum())
            previous = days.get(day)
            new_days[day] = self._merge_coverage_day(day, actual, previous, report)
            day += timedelta(days=1)
        return merged, new_days

    def _merge_coverage_day(
        self,
        day: date,
        actual: int,
        previous: ChainDateCoverage | None,
        report: ChainFetchReport,
    ) -> ChainDateCoverage:
        """Fold one requested date's coverage record per the merge rules."""
        scope: Literal["root", "contracts"]
        if previous is None:
            scope = report.scope
            selected = report.selected_contracts
            reference = report.reference_contract_count
        elif previous.scope == "root":
            if report.scope == "root":
                scope = "root"
                selected = None
                reference = report.reference_contract_count
            else:
                # An existing successful root-scope record dominates a new
                # contract-scope extract.
                scope = "root"
                selected = None
                reference = previous.reference_contract_count
        else:
            if report.scope == "root":
                scope = "root"
                selected = None
                reference = report.reference_contract_count
            else:
                previous_selected = previous.selected_contracts or ()
                new_selected = report.selected_contracts or ()
                union = tuple(sorted(set(previous_selected) | set(new_selected)))
                scope = "contracts"
                selected = union
                reference = len(union)

        reason: str | None = None
        if actual == 0:
            if report.reason is not None and report.reason in _PRECISE_REASONS:
                reason = report.reason
            elif previous is not None and previous.reason in _PRECISE_REASONS:
                # Preserve old precise zero-day reasons when a narrower
                # contract selection is added to an existing day.
                reason = previous.reason
            else:
                try:
                    reason = (
                        "non_session" if not _classify_session(day) else REASON_NO_ROWS
                    )
                except ProviderError:
                    reason = REASON_NO_ROWS
        return ChainDateCoverage(
            as_of=day,
            contract_count=actual,
            scope=scope,
            selected_contracts=selected,
            reference_contract_count=reference,
            extraction_complete=True,
            reason=reason,
        )

    @staticmethod
    def _check_overlaps(base: pd.DataFrame, new_frame: pd.DataFrame) -> None:
        """Raise DataValidationError on conflicting overlapping rows."""
        shared_keys = base.index.intersection(new_frame.index)
        if len(shared_keys) == 0:
            return
        base_shared = base.loc[shared_keys]
        new_shared = new_frame.loc[shared_keys]
        for column in base.columns:
            left = base_shared[column]
            right = new_shared[column]
            if column in ("bid_timestamp_utc", "ask_timestamp_utc"):
                equal = (left == right) | (left.isna() & right.isna())
            elif left.dtype == bool:
                equal = left == right
            else:
                left_num = pd.to_numeric(left, errors="coerce")
                right_num = pd.to_numeric(right, errors="coerce")
                equal = (left_num == right_num) | (
                    left_num.isna() & right_num.isna()
                )
            if not bool(equal.all()):
                mismatched = shared_keys[~equal]
                key = mismatched[0]
                raise DataValidationError(
                    f"conflicting overlapping chain rows at "
                    f"({key[0].date().isoformat()}, {key[1]}) in column "
                    f"{column!r}; storage left unchanged"
                )

    def _build_manifest(
        self,
        symbol: str,
        merged_frame: pd.DataFrame,
        days: dict[date, ChainDateCoverage],
        report: ChainFetchReport,
    ) -> ChainCoverageManifest:
        observed = sorted({ts.date() for ts in merged_frame.index.get_level_values("as_of")})
        return ChainCoverageManifest(
            schema_version=1,
            provider=PROVIDER_NAME,
            root=symbol,
            first_observation=observed[0] if observed else None,
            last_observation=observed[-1] if observed else None,
            days=tuple(sorted(days.values(), key=lambda d: d.as_of)),
            retrieved_at=report.retrieved_at,
            requests_used=report.requests_used,
            conventions=dict(CHAIN_CONVENTIONS),
        )

    # -- reads --------------------------------------------------------------

    def read_chain(
        self, root: str, as_of: date, *, expiry: date | None = None
    ) -> ChainResult:
        """Read one root/date chain from storage; strictly read-only.

        No API key resolution, provider construction, write lock, library
        creation, metadata bootstrap, fetch, update, write, or defrag happens
        here. No-data precedence: expired-expiry filter, then the stored
        root snapshot, then coverage lookup before any min/max check.
        """
        symbol = self._normalize_root(root)
        as_of = self._validate_as_of(as_of)
        if expiry is not None:
            if isinstance(expiry, datetime) or not isinstance(expiry, date):
                raise ValueError("expiry must be a datetime.date or None")
            if expiry < as_of:
                return ChainResult(
                    data=empty_chain_frame(),
                    manifest=None,
                    status="no_data",
                    reason=REASON_EXPIRY_BEFORE_AS_OF,
                )
        result = self._storage.read_with_metadata(OPTIONS_EOD_LIBRARY, symbol)
        if result is None:
            return ChainResult(
                data=empty_chain_frame(),
                manifest=None,
                status="no_data",
                reason=REASON_ROOT_NOT_INGESTED,
            )
        stored_frame, metadata = result
        if not metadata:
            raise StorageError(
                f"stored options root {symbol!r} has no coverage manifest"
            )
        manifest = _parse_manifest_metadata(metadata)
        frame = decode_stored_chain_frame(stored_frame)
        validate_chain_frame(frame)
        _validate_manifest_consistency(frame, manifest)

        as_of_ts = pd.Timestamp(as_of, tz="UTC")
        day_record: ChainDateCoverage | None = None
        for day in manifest.days:
            if day.as_of == as_of:
                day_record = day
                break
        if day_record is not None:
            level = frame.index.get_level_values("as_of")
            day_frame = frame[level == as_of_ts]
            if expiry is not None:
                day_frame = day_frame[day_frame["expiry"] == expiry.isoformat()]
                if day_frame.empty:
                    return ChainResult(
                        data=empty_chain_frame(),
                        manifest=manifest,
                        status="no_data",
                        reason=REASON_NO_ROWS_FOR_EXPIRY,
                    )
            if day_frame.empty and day_record.reason is not None:
                return ChainResult(
                    data=empty_chain_frame(),
                    manifest=manifest,
                    status="no_data",
                    reason=day_record.reason,
                )
            return ChainResult(
                data=day_frame.copy(),
                manifest=manifest,
                status="observed",
                reason=None,
            )
        # Absent from coverage: range check spans the union of coverage and
        # observation dates (observations always carry coverage records).
        if manifest.days:
            span_low = min(day.as_of for day in manifest.days)
            span_high = max(day.as_of for day in manifest.days)
            if as_of < span_low or as_of > span_high:
                reason = REASON_OUTSIDE_COVERAGE
            else:
                reason = REASON_DATE_NOT_INGESTED
        else:
            reason = REASON_OUTSIDE_COVERAGE
        return ChainResult(
            data=empty_chain_frame(),
            manifest=manifest,
            status="no_data",
            reason=reason,
        )


__all__ = ["OptionsDataFetcher", "ChainResult", "ChainCoverageManifest"]

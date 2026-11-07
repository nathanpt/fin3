"""Manifest runner: read-only planning and policy application.

``ManifestRunner`` compares a :class:`~fin3.manifest.ResolvedManifest` with
ArcticDB reality, plans exact completed-bar work in bounded chunks, and
applies the declared update policy (``ensure`` bootstraps only; ``sync``
reconciles incrementally / append-only / full-refresh). Status and dry-run
are strictly read-only: no provider SDKs, no fetches, no locks, no
library/bucket creation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import pandas as pd
import structlog

from fin3.config.settings import ClientConfig
from fin3.core import _prepare_ohlcv, _write_metadata
from fin3.exceptions import (
    ConfigurationError,
    CostLimitExceededError,
    DataValidationError,
    Fin3Error,
    StorageError,
)
from fin3.manifest import ResolvedDataset, ResolvedManifest
from fin3.providers import ProviderRegistry
from fin3.providers.base import ChainProvider, DataProvider
from fin3.schemas import METADATA_LIBRARY, AssetType, observation_mask
from fin3.storage.arctic import ArcticStorage
from fin3.utils.validation import validate_storage_artifact

logger = structlog.get_logger(__name__)

MANIFEST_STATE_PREFIX = "__manifest__"
"""Symbol prefix for manifest drift-state records in ``METADATA_LIBRARY``."""

_SyncAction = Literal["create", "fill", "append", "refresh", "skip"]
_SyncStatus = Literal["planned", "created", "updated", "skipped", "failed"]


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------


@dataclass
class SymbolResult:
    """Per-symbol outcome (or plan, for previews)."""

    dataset_id: str
    library: str
    symbol: str
    policy: str
    action: _SyncAction
    status: _SyncStatus
    reason: str
    fetch_ranges: list[tuple[datetime, datetime]] = field(default_factory=list)
    write_ranges: list[tuple[datetime, datetime]] = field(default_factory=list)
    rows_written: int = 0
    write_state_unknown: bool = False
    error: str | None = None


@dataclass
class ManifestReport:
    """Aggregated outcome of a manifest operation."""

    manifest_id: str
    declaration_hash: str
    last_successful_hash: str | None
    definition_state: Literal["untracked", "unchanged", "changed"]
    operation: Literal["ensure", "sync", "status"]
    dry_run: bool
    resolved_at: datetime
    planned_libraries: list[str]
    created_libraries: list[str]
    estimated_cost: float | None
    results: list[SymbolResult]
    errors: list[str]
    state_write_unknown: bool = False

    @property
    def created_count(self) -> int:
        return sum(1 for r in self.results if r.status == "created")

    @property
    def updated_count(self) -> int:
        return sum(1 for r in self.results if r.status == "updated")

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.results if r.status == "skipped")

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")


# ---------------------------------------------------------------------------
# Planning structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ExpectedChunk:
    """One bounded chunk of completed, in-window expected bar starts."""

    stamps: pd.DatetimeIndex
    fetch_start: datetime
    fetch_end: datetime

    @property
    def first_stamp(self) -> datetime:
        return self.stamps[0].to_pydatetime()

    @property
    def last_stamp(self) -> datetime:
        return self.stamps[-1].to_pydatetime()


@dataclass(frozen=True)
class _ActiveChunk:
    """A chunk with planned writes: exact write stamps plus widened fetch bounds."""

    stamps: pd.DatetimeIndex
    write_stamps: pd.DatetimeIndex
    fetch_start: datetime
    fetch_end: datetime

    @property
    def write_start(self) -> datetime:
        return self.write_stamps[0].to_pydatetime()

    @property
    def write_end(self) -> datetime:
        return self.write_stamps[-1].to_pydatetime()


@dataclass
class _SymbolPlan:
    dataset: ResolvedDataset
    symbol: str
    library_exists: bool
    symbol_exists: bool
    action: _SyncAction
    skip_reason: str | None
    chunks: list[_ActiveChunk]

    @property
    def fetch_ranges(self) -> list[tuple[datetime, datetime]]:
        return [(c.fetch_start, c.fetch_end) for c in self.chunks]

    @property
    def write_ranges(self) -> list[tuple[datetime, datetime]]:
        return [(c.write_start, c.write_end) for c in self.chunks]


# ---------------------------------------------------------------------------
# Expected-bar chunk generation
# ---------------------------------------------------------------------------


def _expected_chunks(
    dataset: ResolvedDataset, resolved_at: datetime
) -> list[_ExpectedChunk]:
    """Generate completed-bar chunks for the requested window.

    Crypto: UTC-day chunks anchored at midnight (``ContinuousCalendarStrategy``),
    each ending just before the following midnight. Exchanges: one chunk per
    trading session label; the label range is widened by one day so an
    overnight session opening on the final requested date is considered.
    A stamp is included only when its bar close (start + resolution for
    crypto; ``min(start + resolution, session_close)`` for exchange intraday;
    ``session_close`` for daily) is <= *resolved_at* — never plan an
    unfinished bar.
    """
    resolved_ts = pd.Timestamp(resolved_at)
    start_ts = pd.Timestamp(dataset.start)
    end_ts = pd.Timestamp(dataset.end)
    delta = pd.Timedelta(dataset.resolution.timedelta_alias)
    strategy = dataset.asset_type.calendar_strategy

    if dataset.asset_type == AssetType.CRYPTO:
        chunks: list[_ExpectedChunk] = []
        day = start_ts.floor("D")
        while day <= end_ts:
            next_day = day + pd.Timedelta(days=1)
            grid = strategy.generate_grid(day, next_day - delta, dataset.resolution)
            stamps = grid[(grid >= start_ts) & (grid <= end_ts)]
            finished = stamps[(stamps + delta) <= resolved_ts]
            if len(finished) > 0:
                chunks.append(
                    _ExpectedChunk(
                        stamps=finished,
                        fetch_start=day.to_pydatetime(),
                        fetch_end=min(next_day.to_pydatetime(), resolved_at),
                    )
                )
            day = next_day
        return chunks

    import exchange_calendars

    mic = dataset.asset_type.mic_code
    assert mic is not None  # crypto handled above
    naive_start = start_ts.tz_localize(None).normalize()
    naive_end = end_ts.tz_localize(None).normalize()
    try:
        cal = exchange_calendars.get_calendar(mic)
        sessions = cal.sessions_in_range(naive_start, naive_end + pd.Timedelta(days=1))
    except Exception as exc:
        raise ConfigurationError(
            f"Calendar {mic!r} cannot serve the requested range "
            f"[{start_ts.isoformat()}, {end_ts.isoformat()}]: {exc}"
        ) from exc

    chunks = []
    for session in sessions:
        session_ts = pd.Timestamp(session)
        label_date = session_ts.date()
        open_ts, close_ts = cal.session_open_close(session_ts)
        label_midnight = session_ts.tz_localize("UTC")
        grid = strategy.generate_grid(
            label_midnight, label_midnight, dataset.resolution
        )
        stamps = grid[(grid >= start_ts) & (grid <= end_ts)]
        if len(stamps) == 0:
            continue
        closes = pd.DatetimeIndex([min(s + delta, close_ts) for s in stamps])
        finished = stamps[closes <= resolved_ts]
        if len(finished) == 0:
            continue
        first_date = min(label_date, finished[0].date())
        last_date = max(label_date, finished[-1].date())
        fetch_start = datetime(
            first_date.year, first_date.month, first_date.day, tzinfo=resolved_at.tzinfo
        )
        fetch_end = min(
            datetime(
                last_date.year,
                last_date.month,
                last_date.day,
                tzinfo=resolved_at.tzinfo,
            )
            + pd.Timedelta(days=1),
            resolved_at,
        )
        chunks.append(
            _ExpectedChunk(
                stamps=finished, fetch_start=fetch_start, fetch_end=fetch_end
            )
        )
    return chunks


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _stored_index(
    storage: ArcticStorage, library: str, symbol: str
) -> pd.DatetimeIndex | None:
    """Return the stored UTC index for a symbol, or None when absent."""
    df = storage.read(library, symbol, columns=["volume"])
    if df is None or df.empty:
        return None
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        return None
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx


def _group_missing(
    stamps: pd.DatetimeIndex, actual: pd.DatetimeIndex, delta: pd.Timedelta
) -> list[pd.DatetimeIndex]:
    """Group missing expected stamps into contiguous inclusive runs.

    Two missing stamps are adjacent when separated by exactly the expected
    bar spacing, so one multi-bar hole becomes one write range. Runs never
    span chunks.
    """
    missing = stamps.difference(actual)
    if len(missing) == 0:
        return []
    runs: list[list[pd.Timestamp]] = [[missing[0]]]
    for ts in missing[1:]:
        if ts - runs[-1][-1] == delta:
            runs[-1].append(ts)
        else:
            runs.append([ts])
    return [pd.DatetimeIndex(run) for run in runs]


def _plan_symbol(
    storage: ArcticStorage,
    dataset: ResolvedDataset,
    symbol: str,
    resolved_at: datetime,
    *,
    mode: Literal["ensure", "sync"],
) -> _SymbolPlan:
    """Read-only plan for one target under the dataset's update policy."""
    delta = pd.Timedelta(dataset.resolution.timedelta_alias)
    library_exists = storage.has_library(dataset.library)
    symbol_exists = library_exists and storage.has_symbol(dataset.library, symbol)

    def skip(reason: str) -> _SymbolPlan:
        return _SymbolPlan(
            dataset=dataset,
            symbol=symbol,
            library_exists=library_exists,
            symbol_exists=symbol_exists,
            action="skip",
            skip_reason=reason,
            chunks=[],
        )

    expected = _expected_chunks(dataset, resolved_at)

    if not symbol_exists:
        if len(expected) == 0:
            return skip("no_completed_bars")
        chunks = [
            _ActiveChunk(
                stamps=c.stamps,
                write_stamps=c.stamps,
                fetch_start=c.fetch_start,
                fetch_end=c.fetch_end,
            )
            for c in expected
        ]
        return _SymbolPlan(
            dataset=dataset,
            symbol=symbol,
            library_exists=library_exists,
            symbol_exists=False,
            action="create",
            skip_reason=None,
            chunks=chunks,
        )

    if mode == "ensure":
        return skip("already_exists")

    actual_index = _stored_index(storage, dataset.library, symbol)
    policy = dataset.update_policy

    if policy.value == "full-refresh":
        chunks = [
            _ActiveChunk(
                stamps=c.stamps,
                write_stamps=c.stamps,
                fetch_start=c.fetch_start,
                fetch_end=c.fetch_end,
            )
            for c in expected
        ]
        return _SymbolPlan(
            dataset=dataset,
            symbol=symbol,
            library_exists=library_exists,
            symbol_exists=True,
            action="refresh",
            skip_reason=None,
            chunks=chunks,
        )

    assert actual_index is not None  # symbol_exists implies readable data

    if policy.value == "append-only":
        last_ts = actual_index.max()
        chunks = []
        for c in expected:
            forward = c.stamps[c.stamps > last_ts]
            if len(forward) == 0:
                continue
            chunks.append(
                _ActiveChunk(
                    stamps=c.stamps,
                    write_stamps=forward,
                    fetch_start=c.fetch_start,
                    fetch_end=c.fetch_end,
                )
            )
        if not chunks:
            return skip("covered")
        return _SymbolPlan(
            dataset=dataset,
            symbol=symbol,
            library_exists=library_exists,
            symbol_exists=True,
            action="append",
            skip_reason=None,
            chunks=chunks,
        )

    # incremental
    chunks = []
    for c in expected:
        missing_runs = _group_missing(c.stamps, actual_index, delta)
        for run in missing_runs:
            chunks.append(
                _ActiveChunk(
                    stamps=c.stamps,
                    write_stamps=run,
                    fetch_start=c.fetch_start,
                    fetch_end=c.fetch_end,
                )
            )
    if not chunks:
        return skip("covered")
    return _SymbolPlan(
        dataset=dataset,
        symbol=symbol,
        library_exists=library_exists,
        symbol_exists=True,
        action="fill",
        skip_reason=None,
        chunks=chunks,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestState:
    """Last successfully applied sync state of a manifest."""

    declaration_hash: str
    last_sync_at: datetime


def _state_symbol(manifest_id: str) -> str:
    return f"{MANIFEST_STATE_PREFIX}{manifest_id}"


def _is_sha256_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


class ManifestRunner:
    """Plans and applies declarative manifests against ArcticDB storage."""

    def __init__(
        self, config: ClientConfig, *, storage: ArcticStorage | None = None
    ) -> None:
        """Initialise the runner.

        Parameters
        ----------
        config : ClientConfig
            Top-level configuration (MinIO, providers, lock settings).
        storage : ArcticStorage or None
            Storage backend override (e.g. an LMDB-backed instance in
            tests). Defaults to ``ArcticStorage(config.minio, lock=config.lock)``.
        """
        self._config = config
        self._storage = (
            storage
            if storage is not None
            else ArcticStorage(config.minio, lock=config.lock)
        )
        self._provider_cache: dict[str, DataProvider] = {}

    # -- public API ------------------------------------------------------

    def status(self, manifest: ResolvedManifest) -> ManifestReport:
        """Preview the actions ``sync`` would take. Strictly read-only."""
        return self._run(manifest, operation="status", dry_run=True, max_cost=None)

    def ensure(
        self,
        manifest: ResolvedManifest,
        *,
        dry_run: bool = False,
        max_cost: float | None = None,
    ) -> ManifestReport:
        """Create missing libraries/symbols; never modifies existing symbols."""
        return self._run(
            manifest, operation="ensure", dry_run=dry_run, max_cost=max_cost
        )

    def sync(
        self,
        manifest: ResolvedManifest,
        *,
        dry_run: bool = False,
        max_cost: float | None = None,
    ) -> ManifestReport:
        """Reconcile stored data with the declaration per update policy."""
        return self._run(manifest, operation="sync", dry_run=dry_run, max_cost=max_cost)

    # -- internals ---------------------------------------------------------

    def _preflight_providers(self, manifest: ResolvedManifest) -> None:
        """Verify provider names against config and registry without SDK instantiation."""
        for name in sorted({d.provider for d in manifest.datasets}):
            if name not in self._config.providers:
                raise ConfigurationError(
                    f"manifest {manifest.id!r}: provider {name!r} is not configured "
                    "(missing from ClientConfig.providers)"
                )
            if name not in ProviderRegistry._PROVIDER_MAP:
                raise ConfigurationError(
                    f"manifest {manifest.id!r}: provider {name!r} is not registered "
                    f"(registered: {sorted(ProviderRegistry._PROVIDER_MAP)})"
                )
            if issubclass(ProviderRegistry._PROVIDER_MAP[name], ChainProvider):
                raise ConfigurationError(
                    f"manifest {manifest.id!r}: provider {name!r} is a chain "
                    "provider; options datasets use OptionsDataFetcher, not "
                    "manifest sync"
                )

    def _provider_for(self, name: str) -> DataProvider:
        """Lazily construct (and cache) only the providers actually needed."""
        if name not in self._provider_cache:
            registry = ProviderRegistry({name: self._config.providers[name]})
            self._provider_cache[name] = registry.get(name)
        return self._provider_cache[name]

    def _read_manifest_state(self, manifest_id: str) -> ManifestState | None:
        """Read drift state; corrupt state is an explicit error, never 'fresh'."""
        df = self._storage.read(METADATA_LIBRARY, _state_symbol(manifest_id))
        if df is None or df.empty:
            return None
        if len(df) != 1:
            raise ConfigurationError(
                f"Manifest state {METADATA_LIBRARY}/{_state_symbol(manifest_id)} "
                f"must hold exactly one record, found {len(df)}"
            )
        row = df.iloc[0]
        operation = row.get("operation")
        declaration_hash = row.get("declaration_hash")
        last_sync_at = row.get("last_sync_at")
        if operation != "sync":
            raise ConfigurationError(
                f"Manifest state {METADATA_LIBRARY}/{_state_symbol(manifest_id)} "
                f"has unexpected operation {operation!r} (expected 'sync')"
            )
        if not _is_sha256_hex(declaration_hash):
            raise ConfigurationError(
                f"Manifest state {METADATA_LIBRARY}/{_state_symbol(manifest_id)} "
                f"has invalid declaration_hash {declaration_hash!r} "
                "(expected 64-character hex)"
            )
        if last_sync_at is None:
            raise ConfigurationError(
                f"Manifest state {METADATA_LIBRARY}/{_state_symbol(manifest_id)} "
                "is missing last_sync_at"
            )
        try:
            ts = pd.Timestamp(last_sync_at)
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(
                f"Manifest state {METADATA_LIBRARY}/{_state_symbol(manifest_id)} "
                f"has unparseable last_sync_at {last_sync_at!r}"
            ) from exc
        if ts.tz is None:
            raise ConfigurationError(
                f"Manifest state {METADATA_LIBRARY}/{_state_symbol(manifest_id)} "
                "has a timezone-naive last_sync_at"
            )
        return ManifestState(
            declaration_hash=str(declaration_hash),
            last_sync_at=ts.to_pydatetime(),
        )

    def _definition_state(
        self, manifest: ResolvedManifest, state: ManifestState | None
    ) -> Literal["untracked", "unchanged", "changed"]:
        if state is None:
            return "untracked"
        if state.declaration_hash == manifest.declaration_hash:
            return "unchanged"
        return "changed"

    def _plan_all(
        self,
        manifest: ResolvedManifest,
        *,
        mode: Literal["ensure", "sync"],
    ) -> list[_SymbolPlan]:
        plans: list[_SymbolPlan] = []
        for dataset in sorted(manifest.datasets, key=lambda d: d.dataset_id):
            for symbol in dataset.symbols:
                plans.append(
                    _plan_symbol(
                        self._storage,
                        dataset,
                        symbol,
                        manifest.resolved_at,
                        mode=mode,
                    )
                )
        return plans

    @staticmethod
    def _max_half_open_overlap(intervals: list[tuple[datetime, datetime]]) -> int:
        """Maximum number of half-open intervals overlapping at any instant."""
        if not intervals:
            return 0
        events: list[tuple[datetime, int]] = []
        for start, end in intervals:
            events.append((start, 1))
            events.append((end, -1))
        # Ends before starts at identical timestamps: touching intervals
        # ([a, b) then [b, c)) do not overlap.
        events.sort(key=lambda e: (e[0], e[1]))
        current = 0
        max_overlap = 0
        for _, delta in events:
            current += delta
            max_overlap = max(max_overlap, current)
        return max(max_overlap, 1)

    def _estimate_total_cost(self, plans: list[_SymbolPlan]) -> float:
        """One estimator call per target over its union fetch span.

        The estimate is multiplied by the target's maximum fetch-interval
        overlap count to stay conservative for widened/overlapping queries.
        """
        total = 0.0
        for plan in plans:
            if not plan.chunks:
                continue
            provider = self._provider_for(plan.dataset.provider)
            estimator = getattr(provider, "estimate_cost", None)
            if not callable(estimator):
                raise ConfigurationError(
                    f"Provider {plan.dataset.provider!r} does not support cost "
                    f"estimation; max_cost cannot be honored for "
                    f"{plan.dataset.dataset_id}/{plan.symbol}"
                )
            union_start = min(c.fetch_start for c in plan.chunks)
            union_end = max(c.fetch_end for c in plan.chunks)
            estimate = float(
                estimator(
                    symbol=plan.symbol,
                    start=union_start,
                    end=union_end,
                    resolution=plan.dataset.resolution,
                    asset_type=plan.dataset.asset_type,
                )
            )
            if not math.isfinite(estimate) or estimate < 0:
                raise ConfigurationError(
                    f"Provider {plan.dataset.provider!r} returned an invalid cost "
                    f"estimate ({estimate!r}) for {plan.dataset.dataset_id}/{plan.symbol}"
                )
            overlap = self._max_half_open_overlap(plan.fetch_ranges)
            total += estimate * overlap
        return total

    # -- application -------------------------------------------------------

    def _reconcile_rows(self, library: str, symbol: str) -> str:
        """Read-only post-failure reconciliation note for uncertain writes."""
        try:
            df = self._storage.read(library, symbol, columns=["volume"])
            rows = 0 if df is None else len(df)
            return f"post-failure read of {library}/{symbol} shows {rows} rows"
        except Exception as exc:  # pragma: no cover - storage unreachable
            return f"post-failure reconciliation read failed: {exc}"

    def _execute_chunk(
        self,
        plan: _SymbolPlan,
        chunk: _ActiveChunk,
        provider: DataProvider,
        *,
        new_symbol: bool,
        committed_any: bool,
    ) -> int:
        """Fetch, prepare, merge, validate, and commit one chunk.

        Returns the number of rows in the confirmed commit. Raises on any
        failure without touching storage.
        """
        dataset = plan.dataset
        raw = provider.fetch(
            symbol=plan.symbol,
            start=chunk.fetch_start,
            end=chunk.fetch_end,
            resolution=dataset.resolution,
            asset_type=dataset.asset_type,
        )

        prepared = _prepare_ohlcv(
            raw, chunk.stamps, dataset.resolution, asset_type=dataset.asset_type
        )
        selected = prepared.loc[chunk.write_stamps]

        # An empty or entirely unmappable response must not fabricate a
        # null-history download for a nonempty planned write grid.
        ohlc_cols = ["open", "high", "low", "close"]
        synthesized = (selected["volume"] == 0) & selected[ohlc_cols].isna().all(axis=1)
        if raw.empty or bool(synthesized.all()):
            raise DataValidationError(
                f"provider returned no mappable data for planned writes "
                f"[{chunk.write_start.isoformat()}, {chunk.write_end.isoformat()}]"
            )

        existing_span: pd.DataFrame | None = None
        if not new_symbol:
            existing_span = self._storage.read(
                dataset.library,
                plan.symbol,
                date_range=(chunk.write_start, chunk.write_end),
            )

        # Refresh safety: never replace an existing observation with
        # synthesized no-trade padding. For traded bars an observation is a
        # positive-volume row; for index data it is a populated close.
        if (
            plan.action == "refresh"
            and existing_span is not None
            and not existing_span.empty
        ):
            idx = existing_span.index
            if isinstance(idx, pd.DatetimeIndex) and idx.tz is None:
                existing_span = existing_span.copy()
                existing_span.index = idx.tz_localize("UTC")
            regressed = synthesized & (chunk.write_stamps.isin(existing_span.index))
            observed = observation_mask(existing_span, asset_type=dataset.asset_type)
            for stamp in chunk.write_stamps[regressed]:
                if not bool(observed.loc[stamp]):
                    continue
                if dataset.asset_type is AssetType.INDEX_US:
                    raise DataValidationError(
                        f"refresh would replace index observation at "
                        f"{stamp.isoformat()} with synthesized no-trade padding"
                    )
                raise DataValidationError(
                    f"refresh would replace positive-volume observation at "
                    f"{stamp.isoformat()} with synthesized no-trade padding"
                )

        merged = _merge_for_commit(existing_span, selected, chunk.write_stamps)
        validate_storage_artifact(
            merged, dataset.resolution, asset_type=dataset.asset_type
        )

        metadata = _write_metadata(
            plan.symbol, provider, chunk.write_start, chunk.write_end
        )
        if new_symbol and not committed_any:
            self._storage.write(dataset.library, plan.symbol, merged, metadata=metadata)
        else:
            self._storage.update(
                dataset.library,
                plan.symbol,
                merged,
                date_range=(chunk.write_start, chunk.write_end),
                metadata=metadata,
            )
        return len(merged)

    def _apply_target(
        self,
        plan: _SymbolPlan,
        approved: _SymbolPlan,
        *,
        max_cost: float | None,
    ) -> SymbolResult:
        """Apply one planned target under its symbol lock."""
        dataset = plan.dataset
        result = SymbolResult(
            dataset_id=dataset.dataset_id,
            library=dataset.library,
            symbol=plan.symbol,
            policy=dataset.update_policy.value,
            action=plan.action,
            status="planned",
            reason=plan.skip_reason or "",
        )

        if plan.skip_reason is not None or not plan.chunks:
            result.status = "skipped"
            result.reason = plan.skip_reason or "no_completed_bars"
            return result

        if max_cost is not None:
            approved_intervals = set(approved.fetch_ranges)
            for interval in plan.fetch_ranges:
                if interval not in approved_intervals:
                    result.status = "failed"
                    result.reason = "plan_changed"
                    result.error = (
                        f"concurrent change requires fetch interval "
                        f"[{interval[0].isoformat()}, {interval[1].isoformat()}] "
                        "outside the approved plan; rerun required"
                    )
                    return result

        provider = self._provider_for(dataset.provider)
        new_symbol = not plan.symbol_exists
        committed_any = False
        rows_written = 0
        fetch_ranges: list[tuple[datetime, datetime]] = []
        write_ranges: list[tuple[datetime, datetime]] = []

        for chunk in plan.chunks:
            fetch_ranges.append((chunk.fetch_start, chunk.fetch_end))
            write_ranges.append((chunk.write_start, chunk.write_end))
            try:
                rows = self._execute_chunk(
                    plan,
                    chunk,
                    provider,
                    new_symbol=new_symbol,
                    committed_any=committed_any,
                )
            except StorageError as exc:
                result.status = "failed"
                result.reason = "write_failed"
                result.write_state_unknown = True
                result.error = (
                    f"{dataset.dataset_id}/{dataset.library}/{plan.symbol} "
                    f"write [{chunk.write_start.isoformat()}, "
                    f"{chunk.write_end.isoformat()}]: {exc}; "
                    f"{self._reconcile_rows(dataset.library, plan.symbol)}"
                )
                break
            except Fin3Error as exc:
                result.status = "failed"
                result.reason = "fetch_or_validation_failed"
                result.error = (
                    f"{dataset.dataset_id}/{dataset.library}/{plan.symbol} "
                    f"[{chunk.write_start.isoformat()}, "
                    f"{chunk.write_end.isoformat()}]: {exc}"
                )
                break
            except Exception as exc:  # provider SDKs can raise raw errors
                result.status = "failed"
                result.reason = "fetch_or_validation_failed"
                result.error = (
                    f"{dataset.dataset_id}/{dataset.library}/{plan.symbol} "
                    f"[{chunk.write_start.isoformat()}, "
                    f"{chunk.write_end.isoformat()}]: {type(exc).__name__}: {exc}"
                )
                break
            rows_written += rows
            committed_any = True

        result.fetch_ranges = fetch_ranges
        result.write_ranges = write_ranges
        result.rows_written = rows_written
        if result.status != "failed":
            if new_symbol:
                result.status = "created"
                result.reason = "populated_requested_grid"
            else:
                result.status = "updated"
                result.reason = "applied_" + dataset.update_policy.value
        return result

    def _write_manifest_state(self, manifest: ResolvedManifest) -> None:
        # last_sync_at is stored as an aware UTC ISO string: ArcticDB strips
        # tzinfo from timestamp columns, which would corrupt the round-trip.
        record = pd.DataFrame(
            {
                "declaration_hash": [manifest.declaration_hash],
                "last_sync_at": [manifest.resolved_at.isoformat()],
                "operation": ["sync"],
            }
        )
        self._storage.write(METADATA_LIBRARY, _state_symbol(manifest.id), record)

    def _run(
        self,
        manifest: ResolvedManifest,
        *,
        operation: Literal["ensure", "sync", "status"],
        dry_run: bool,
        max_cost: float | None,
    ) -> ManifestReport:
        if max_cost is not None:
            if not math.isfinite(max_cost) or max_cost < 0:
                raise ConfigurationError(
                    f"max_cost must be a finite nonnegative number, got {max_cost!r}"
                )

        # Preflight: everything validated before any storage inspection or
        # mutation so a broken manifest/config never touches storage.
        self._preflight_providers(manifest)
        state = self._read_manifest_state(manifest.id)
        plans = self._plan_all(
            manifest, mode="ensure" if operation == "ensure" else "sync"
        )

        planned_libraries = sorted(
            {p.dataset.library for p in plans if not p.library_exists}
        )
        last_hash = state.declaration_hash if state else None
        definition_state = self._definition_state(manifest, state)

        preview = operation == "status" or dry_run
        estimated_cost: float | None = None

        if preview:
            planned_results = [
                self._result_for_plan(p, status="planned") for p in plans
            ]
            return ManifestReport(
                manifest_id=manifest.id,
                declaration_hash=manifest.declaration_hash,
                last_successful_hash=last_hash,
                definition_state=definition_state,
                operation=operation,
                dry_run=dry_run,
                resolved_at=manifest.resolved_at,
                planned_libraries=planned_libraries,
                created_libraries=[],
                estimated_cost=None,
                results=planned_results,
                errors=[],
            )

        if max_cost is not None:
            estimated_cost = self._estimate_total_cost(plans)
            if estimated_cost > max_cost:
                raise CostLimitExceededError(
                    f"Estimated cost ${estimated_cost:.2f} exceeds max_cost "
                    f"${max_cost:.2f}",
                    estimated_cost=estimated_cost,
                    max_cost=max_cost,
                )

        errors: list[str] = []
        created_libraries: list[str] = []
        state_write_unknown = False
        results: list[SymbolResult] = []
        run_failed = False
        data_changed = False

        manifest_lock = self._storage.lock_symbol(
            METADATA_LIBRARY, _state_symbol(manifest.id)
        )
        with manifest_lock:
            for plan in plans:
                dataset = plan.dataset
                if dataset.library not in created_libraries and not (
                    self._storage.has_library(dataset.library)
                ):
                    self._storage.ensure_library(dataset.library)
                    created_libraries.append(dataset.library)

                with self._storage.lock_symbol(dataset.library, plan.symbol):
                    # Re-plan under the lock: reality may have moved since the
                    # approved snapshot was taken.
                    fresh = _plan_symbol(
                        self._storage,
                        dataset,
                        plan.symbol,
                        manifest.resolved_at,
                        mode="ensure" if operation == "ensure" else "sync",
                    )
                    if fresh.action == "skip" and plan.action != "skip":
                        # Concurrent writer already satisfied the target.
                        applied = SymbolResult(
                            dataset_id=dataset.dataset_id,
                            library=dataset.library,
                            symbol=plan.symbol,
                            policy=dataset.update_policy.value,
                            action="skip",
                            status="skipped",
                            reason=fresh.skip_reason or "covered",
                        )
                        results.append(applied)
                        continue
                    applied = self._apply_target(fresh, plan, max_cost=max_cost)
                results.append(applied)
                if applied.status == "failed":
                    run_failed = True
                    assert applied.error is not None
                    errors.append(applied.error)
                    if applied.write_state_unknown:
                        state_write_unknown = True
                elif applied.status in ("created", "updated"):
                    data_changed = True

            state_written = False
            if (
                operation == "sync"
                and not run_failed
                and (last_hash != manifest.declaration_hash or data_changed)
            ):
                try:
                    self._write_manifest_state(manifest)
                    state_written = True
                except Exception as exc:
                    # Any state-write exception has an uncertain remote
                    # outcome: fail the run, never claim rollback.
                    run_failed = True
                    state_write_unknown = True
                    observed: ManifestState | None = None
                    try:
                        observed = self._read_manifest_state(manifest.id)
                    except Exception:
                        pass
                    known = (
                        observed.declaration_hash if observed is not None else last_hash
                    )
                    errors.append(
                        f"manifest {manifest.id}: state write failed ({exc}); "
                        f"last known state hash: {known!r}"
                    )

        final_definition_state = definition_state
        if state_written:
            final_definition_state = "unchanged"

        return ManifestReport(
            manifest_id=manifest.id,
            declaration_hash=manifest.declaration_hash,
            last_successful_hash=(
                manifest.declaration_hash if state_written else last_hash
            ),
            definition_state=final_definition_state,
            operation=operation,
            dry_run=dry_run,
            resolved_at=manifest.resolved_at,
            planned_libraries=planned_libraries,
            created_libraries=created_libraries,
            estimated_cost=estimated_cost,
            results=results,
            errors=errors,
            state_write_unknown=state_write_unknown,
        )

    @staticmethod
    def _result_for_plan(plan: _SymbolPlan, *, status: _SyncStatus) -> SymbolResult:
        return SymbolResult(
            dataset_id=plan.dataset.dataset_id,
            library=plan.dataset.library,
            symbol=plan.symbol,
            policy=plan.dataset.update_policy.value,
            action=plan.action,
            status=status,
            reason=plan.skip_reason or "",
            fetch_ranges=plan.fetch_ranges,
            write_ranges=plan.write_ranges,
        )


def _merge_for_commit(
    existing: pd.DataFrame | None,
    selected: pd.DataFrame,
    write_stamps: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Merge selected rows with existing rows in the write span.

    Existing rows at planned write stamps are replaced; every other stored
    row in the span — including unexpected off-grid rows — is preserved, so
    incremental/append-only commits can never delete stored history.
    """
    if existing is None or existing.empty:
        return selected
    keep = existing[~existing.index.isin(write_stamps)]
    merged = pd.concat([keep, selected])
    merged = merged[~merged.index.duplicated(keep="last")]
    return merged.sort_index()

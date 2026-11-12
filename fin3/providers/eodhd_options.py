"""EODHD US options EOD chain provider (marketplace UnicornBay).

Extracts historical per-contract option chains through the documented
paginated ``/contracts`` (reference inventory) and ``/eod`` (per-contract
history) endpoints, honoring a strict per-run request budget. Every query
is exhausted through ``links.next``; a query that would cross the source's
offset ceiling (documented >10,000 records per filtered query) is split by
expiration, then right, then strike range — never silently truncated.

Conventions preserved exactly as published: observation labels come from
the EOD id (``CONTRACT-YYYY-MM-DD``), never from ``tradetime``; unknown
multiplier stays null; naive quote timestamps stay raw with a NaT UTC
column; zero/crossed quotes are retained and flagged.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

import exchange_calendars
import pandas as pd
import structlog

from fin3.config.settings import EodhdOptionsConfig
from fin3.exceptions import (
    ConfigurationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    RequestLimitExceededError,
)
from fin3.options_schemas import (
    CHAIN_COLUMNS,
    CHAIN_INDEX_NAMES,
    CHAIN_REPORT_ATTR,
    REASON_NO_CONTRACTS,
    REASON_NO_ROWS,
    REASON_UNKNOWN_UNDERLYING,
    ChainFetchReport,
    OSIComponents,
    empty_chain_frame,
    parse_eod_id,
    parse_osi_contract,
)
from fin3.providers import ProviderRegistry
from fin3.providers.base import ChainProvider
from fin3.utils.options_validation import compute_quote_flags, standard_contract_heuristic

logger = structlog.get_logger(__name__)

_OFFSET_CEILING = 10_000
"""Documented maximum records per filtered query (offset <= 10,000)."""

_STRIKE_MAX = 99_999_999
"""Inclusive upper bound of the OSI 8-digit strike-integer interval."""

_PATH_CONTRACTS = "/contracts"
_PATH_EOD = "/eod"

_IDENTITY_FIELDS = ("contract", "underlying_symbol", "exp_date", "type", "strike")
_EOD_REQUIRED_FIELDS = ("bid", "ask", "open_interest", "volatility", "volume")

_FLOAT_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("open", "open"),
    ("high", "high"),
    ("low", "low"),
    ("last", "last"),
    ("bid", "bid"),
    ("ask", "ask"),
    ("bid_size", "bid_size"),
    ("ask_size", "ask_size"),
    ("volume", "volume"),
    ("open_interest", "open_interest"),
    ("volatility", "implied_volatility"),
    ("midpoint", "midpoint"),
    ("theoretical", "theoretical"),
    ("delta", "delta"),
    ("gamma", "gamma"),
    ("theta", "theta"),
    ("vega", "vega"),
    ("rho", "rho"),
)
"""(source field, chain column) for float64 fields; missing source → NaN."""

_RIGHT_MAP = {"call": "C", "put": "P"}


class _QueryCeiling(Exception):
    """Internal: a valid continuation would cross the source offset ceiling.

    ``minimum_rows`` is the proven row-count lower bound at the moment the
    ceiling was detected (rows already under the walked offsets plus the
    rows of the full page whose continuation crossed) — callers use it to
    size disjoint partition children.
    """

    def __init__(self, *, minimum_rows: int) -> None:
        super().__init__(
            f"documented continuation would exceed the source offset "
            f"ceiling (>= {minimum_rows} rows)"
        )
        self.minimum_rows = minimum_rows


@dataclass(frozen=True)
class _QueryPartition:
    """One disjoint filtered-query partition of a root's row space."""

    expiry_from: date
    expiry_to: date
    source_type: Literal["call", "put"] | None = None
    strike_from: int | None = None
    strike_to: int | None = None


@dataclass(frozen=True)
class _QueryPage:
    """One validated source page: its rows plus the parsed continuation."""

    data: list[dict[str, Any]]
    next_offset: int | None


class _UnknownUnderlying(ProviderError):
    """Internal: the source's documented unknown-underlying 404 body."""


class _RequestBudget:
    """Per-run request budget shared by every GET in one fetch.

    Counts each attempted request (including failures), guards the cap
    before any request is opened, and spaces request starts by the
    configured interval using the monotonic clock.
    """

    def __init__(
        self,
        max_requests: int,
        pacing_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._max = max_requests
        self._pacing_s = pacing_s
        self._clock = clock
        self._sleep = sleep
        self._used = 0
        self._last_start: float | None = None

    @property
    def used(self) -> int:
        return self._used

    @property
    def max_requests(self) -> int:
        return self._max

    def reserve(self, additional: int) -> None:
        """Preflight a known lower bound of *additional* future requests."""
        if self._used + additional > self._max:
            raise RequestLimitExceededError(
                f"EODHD run needs at least {self._used + additional} requests; "
                f"cap {self._max}",
                requests_used=self._used + additional,
                max_requests=self._max,
            )

    def take(self) -> None:
        """Account one request: guard, pace, then count."""
        if self._used >= self._max:
            raise RequestLimitExceededError(
                "EODHD per-run request limit exhausted",
                requests_used=self._used,
                max_requests=self._max,
            )
        now = self._clock()
        if self._last_start is not None and self._pacing_s > 0:
            remaining = self._pacing_s - (now - self._last_start)
            if remaining > 0:
                self._sleep(remaining)
        self._last_start = self._clock()
        self._used += 1


@dataclass(frozen=True)
class _Reference:
    """Identity of one enumerated source contract."""

    contract: str
    underlying: str
    osi_root: str
    expiry: date
    expiration_type: str | None
    right: str
    strike_thousandths: int
    currency: str | None

    @property
    def strike(self) -> float:
        return self.strike_thousandths / 1000.0


class _SessionClassifier:
    """Classifies observation dates against the XNYS session-label proxy.

    XNYS provides holiday/session labels only — it is NOT an options
    closing-time claim and never reindexes chains. Dates outside the
    proxy's supported calendar coverage raise instead of inventing a
    boolean.
    """

    def __init__(self) -> None:
        self._calendar: exchange_calendars.ExchangeCalendar | None = None
        self._cache: dict[date, bool] = {}

    def is_session(self, day: date) -> bool:
        cached = self._cache.get(day)
        if cached is not None:
            return cached
        if self._calendar is None:
            self._calendar = exchange_calendars.get_calendar("XNYS")
        assert self._calendar is not None  # for mypy narrowing only
        try:
            result = bool(self._calendar.is_session(pd.Timestamp(day)))
        except ValueError as exc:
            raise ProviderError(
                "XNYS session proxy cannot classify source observation dates"
            ) from exc
        self._cache[day] = result
        return result


@ProviderRegistry.register("eodhd_options")
class EodhdOptionsProvider(ChainProvider):
    """Historical US equity/ETF options EOD chains from EODHD.

    The constructor is side-effect-free and accepts an unresolved ``None``
    key; a usable key is required at the first public fetch/bounds call.
    All discovery, partition, history, and pagination GETs share one
    per-call request budget.
    """

    def __init__(self, config: EodhdOptionsConfig) -> None:
        self._config = config
        self._api_key = config.api_key
        self._base_url = config.base_url
        self._timeout = config.timeout
        self._page_size = config.page_size
        self._pacing_s = config.request_pacing_s
        self._classifier: _SessionClassifier | None = None

    # -- credential resolution ------------------------------------------

    def _require_key(self) -> str:
        secret = self._api_key
        if secret is None:
            raise ConfigurationError("EODHD_API_KEY is required for options backfill")
        raw = secret.get_secret_value()
        if not raw.strip():
            raise ConfigurationError("EODHD_API_KEY is required for options backfill")
        return raw

    # -- input validation -------------------------------------------------

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
    def _normalize_contracts(contracts: Sequence[str] | None) -> tuple[str, ...] | None:
        if contracts is None:
            return None
        if isinstance(contracts, str) or not isinstance(contracts, Sequence):
            raise ValueError("contracts must be a sequence of strings or None")
        items = list(contracts)
        if not items:
            raise ValueError("contracts selection must be nonempty when provided")
        normalized: set[str] = set()
        for contract in items:
            if not isinstance(contract, str):
                raise ValueError("contracts must contain only strings")
            candidate = contract.strip().upper()
            try:
                parse_osi_contract(candidate)
            except ValueError as exc:
                raise ValueError(f"contract {contract!r} is not OSI-valid: {exc}") from exc
            normalized.add(candidate)
        return tuple(sorted(normalized))

    @staticmethod
    def _validate_max_requests(max_requests: int | None) -> None:
        if max_requests is None:
            return
        if isinstance(max_requests, bool) or not isinstance(max_requests, int):
            raise ValueError("max_requests must be a positive integer or None")
        if max_requests <= 0:
            raise ValueError("max_requests must be a positive integer or None")

    # -- HTTP transport ---------------------------------------------------

    @staticmethod
    def _safe_url(url: str) -> str:
        """Render a URL with any credential parameter masked."""
        parsed = urllib.parse.urlparse(url)
        pairs = [
            (key, "***" if key == "api_token" else value)
            for key, value in urllib.parse.parse_qsl(
                parsed.query, keep_blank_values=True
            )
        ]
        return urllib.parse.urlparse(url)._replace(
            query=urllib.parse.urlencode(pairs)
        ).geturl()

    def _get_json(self, url: str, budget: _RequestBudget) -> dict[str, Any]:
        """Perform one budgeted GET and return the parsed JSON object."""
        budget.take()  # counts the attempt even when the request fails
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "fin3"})
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ProviderRateLimitError(
                    f"EODHD rate limit (429) at {self._safe_url(url)}"
                ) from exc
            if exc.code in (401, 403):
                raise ProviderError(
                    f"EODHD authentication/authorization failed (HTTP {exc.code})"
                ) from exc
            if exc.code == 404:
                body = ""
                with contextlib.suppress(Exception):
                    body = exc.read().decode("utf-8", errors="replace")
                if "Underlying Symbol Not Found" in body:
                    raise _UnknownUnderlying(
                        f"EODHD reports unknown underlying symbol (HTTP 404) "
                        f"at {self._safe_url(url)}"
                    ) from exc
                raise ProviderError(
                    f"EODHD HTTP 404 at {self._safe_url(url)}"
                ) from exc
            raise ProviderError(f"EODHD HTTP {exc.code} at {self._safe_url(url)}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise ProviderTimeoutError(
                    f"EODHD timeout at {self._safe_url(url)}"
                ) from exc
            raise ProviderError(f"EODHD network error: {exc.reason}") from exc

        try:
            data = json.loads(payload.decode("utf-8"), parse_constant=_reject_constant)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError("EODHD returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError("EODHD unexpected response shape (not an object)")
        return data

    # -- request building -------------------------------------------------

    def _build_url(
        self, path: str, pairs: list[tuple[str, str]], *, compact: bool
    ) -> str:
        query_pairs = list(pairs)
        if compact:
            query_pairs.append(("compact", "0"))
        query_pairs.append(("api_token", self._require_key()))
        return f"{self._base_url}{path}?{urllib.parse.urlencode(query_pairs)}"

    # -- pagination -------------------------------------------------------

    def _paged_query(
        self,
        *,
        path: str,
        params: list[tuple[str, str]],
        budget: _RequestBudget,
        compact: bool,
        row_validator: Callable[[dict[str, Any]], None],
        limit: int | None = None,
        single_page: bool = False,
        probe_ceiling: bool = False,
        reserve_remaining: Callable[[int], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Run one documented paged query and return all its rows.

        Ordinary pager rules: request offset 0, then the advancing offset
        from ``links.next``; same origin/path, same filters/sort/limit; on
        full pages the next offset must advance by exactly the page size;
        end on a null next or a short page. A full page whose valid
        continuation would exceed offset 10,000 raises ``_QueryCeiling``
        (carrying the proven ``minimum_rows`` bound) before that request
        is made. ``single_page`` (used by the one-page maximum-expiration
        discovery, its guard, and the grid reference guards) returns the
        first page without following ``links.next``.

        With ``probe_ceiling`` a query that does not fit under the offset
        ceiling is detected in two GETs: after a full first page the pager
        fetches the last legal page boundary ``T = (C // P) * P`` directly
        with the same filters. A full tail page proves rows past the
        ceiling; a nonempty terminal tail page proves the exact number of
        intervening pages, which are then walked in ordinary continuation
        order with the cached tail reused instead of re-fetched; an empty
        tail supplies no row bound, so the ordinary walk continues to the
        terminal page and the redundant probe is ignored. When
        *reserve_remaining* is given it is called with the proven minimum
        of additional GETs this query still needs (including the next one)
        before each actual request, so callers can fail fast on budget
        lower bounds spanning sibling partitions.
        """
        page_limit = self._page_size if limit is None else limit
        reserve = (
            reserve_remaining
            if reserve_remaining is not None
            else lambda _n: None
        )
        tail_offset = (_OFFSET_CEILING // page_limit) * page_limit
        page_digests: dict[str, int] = {}

        def note_page(page: _QueryPage, offset: int) -> None:
            if len(page.data) < page_limit:
                return
            digest = hashlib.sha256(
                json.dumps(page.data, sort_keys=True).encode("utf-8")
            ).hexdigest()
            previous = page_digests.get(digest)
            if previous is not None and previous != offset:
                raise ProviderError("EODHD repeated page at a different offset")
            page_digests[digest] = offset

        def fetch_page(offset: int) -> _QueryPage:
            return self._fetch_page(
                path=path,
                params=params,
                budget=budget,
                compact=compact,
                row_validator=row_validator,
                offset=offset,
                page_limit=page_limit,
            )

        reserve(1)
        page = fetch_page(0)
        note_page(page, 0)
        rows: list[dict[str, Any]] = list(page.data)
        if single_page or page.next_offset is None:
            return rows
        if len(page.data) < page_limit:
            raise ProviderError(
                "EODHD pagination inconsistent: short page carries a "
                "continuation link"
            )
        first_next = page.next_offset
        if first_next is None:
            raise ProviderError(
                "EODHD pagination inconsistent: full page carries no "
                "continuation link"
            )

        if not probe_ceiling:
            offset = first_next
            while True:
                reserve(1)
                page = fetch_page(offset)
                note_page(page, offset)
                rows.extend(page.data)
                follow = page.next_offset
                if follow is None:
                    return rows
                if len(page.data) < page_limit:
                    raise ProviderError(
                        "EODHD pagination inconsistent: short page carries "
                        "a continuation link"
                    )
                if follow > _OFFSET_CEILING:
                    raise _QueryCeiling(minimum_rows=offset + len(page.data))
                offset = follow

        # Ceiling-probe mode: page 0 is full with a continuation. T == 0
        # means page 0 already is the tail; T == next_offset means the
        # tail fetch is the ordinary next page rather than extra work.
        if tail_offset == 0:
            tail = page
        else:
            if first_next == tail_offset:
                reserve(1)
            else:
                # Distant probe: the probe itself plus at least one
                # required middle-page request (or the ceiling signal).
                reserve(2)
            tail = fetch_page(tail_offset)
            note_page(tail, tail_offset)
        if tail.next_offset is not None:
            if len(tail.data) >= page_limit:
                # The tail sits at the last legal page boundary; a full
                # one proves rows past the ceiling without downloading
                # them and without retaining this query's rows.
                raise _QueryCeiling(minimum_rows=tail_offset + len(tail.data))
            raise ProviderError(
                "EODHD pagination inconsistent with ceiling probe"
            )
        if not tail.data:
            # Empty tail: no T + 0 row lower bound; the ordinary walk
            # decides. The redundant probe completes the query at offset T.
            offset = first_next
            while True:
                if offset == tail_offset:
                    return rows
                reserve(1)
                page = fetch_page(offset)
                note_page(page, offset)
                rows.extend(page.data)
                follow = page.next_offset
                if follow is None:
                    return rows
                if len(page.data) < page_limit:
                    raise ProviderError(
                        "EODHD pagination inconsistent: short page carries "
                        "a continuation link"
                    )
                if follow > _OFFSET_CEILING:
                    raise _QueryCeiling(minimum_rows=offset + len(page.data))
                offset = follow

        # Nonempty terminal tail: walk the intervening offsets in ordinary
        # continuation order, then reuse the cached tail by offset equality.
        unfetched = tail_offset // page_limit - 1
        offset = first_next
        while offset != tail_offset:
            reserve(unfetched)
            try:
                middle = fetch_page(offset)
            except (ProviderTimeoutError, ProviderRateLimitError):
                raise
            except ProviderError as exc:
                raise ProviderError(
                    "EODHD pagination inconsistent with ceiling probe"
                ) from exc
            if middle.next_offset is None or len(middle.data) < page_limit:
                raise ProviderError(
                    "EODHD pagination inconsistent with ceiling probe"
                )
            note_page(middle, offset)
            rows.extend(middle.data)
            unfetched -= 1
            offset = middle.next_offset
        rows.extend(tail.data)
        return rows

    def _fetch_page(
        self,
        *,
        path: str,
        params: list[tuple[str, str]],
        budget: _RequestBudget,
        compact: bool,
        row_validator: Callable[[dict[str, Any]], None],
        offset: int,
        page_limit: int,
    ) -> _QueryPage:
        """Fetch and validate one page; parse its continuation link if sent."""
        sent = dict(params)
        url = self._build_url(
            path,
            [
                *params,
                ("page[offset]", str(offset)),
                ("page[limit]", str(page_limit)),
            ],
            compact=compact,
        )
        payload = self._get_json(url, budget)
        meta = payload.get("meta")
        data = payload.get("data")
        links = payload.get("links")
        if not isinstance(meta, dict):
            raise ProviderError("EODHD response is missing a meta object")
        if _strict_int(meta.get("offset")) != offset or _strict_int(
            meta.get("limit")
        ) != page_limit:
            raise ProviderError(
                "EODHD response meta does not match the sent paging parameters"
            )
        if not isinstance(data, list):
            raise ProviderError("EODHD response data is not a list")
        for row in data:
            if not isinstance(row, dict):
                raise ProviderError("EODHD response row is not an object")
            row_id = row.get("id")
            attributes = row.get("attributes")
            if not isinstance(row_id, str) or not isinstance(attributes, dict):
                raise ProviderError("EODHD response row lacks id/attributes")
            row_validator(row)
        next_offset: int | None = None
        if links is not None:
            if not isinstance(links, dict):
                raise ProviderError("EODHD response links is not an object")
            raw_next = links.get("next")
            if raw_next is not None:
                if not isinstance(raw_next, str):
                    raise ProviderError("EODHD continuation link is not a string")
                next_offset = self._parse_continuation(
                    raw_next,
                    path=path,
                    sent=sent,
                    expected_offset=offset + page_limit,
                    expected_limit=page_limit,
                    full_page=len(data) >= page_limit,
                    current_offset=offset,
                )
        return _QueryPage(data=data, next_offset=next_offset)

    def _parse_continuation(
        self,
        next_link: str,
        *,
        path: str,
        sent: dict[str, str],
        expected_offset: int,
        expected_limit: int,
        full_page: bool,
        current_offset: int,
    ) -> int:
        base = urllib.parse.urlparse(self._base_url)
        parsed = urllib.parse.urlparse(next_link)
        if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
            raise ProviderError("EODHD continuation link changed origin")
        if parsed.path != base.path + path:
            raise ProviderError("EODHD continuation link changed path")
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        values = dict(pairs)
        for key, expected in sent.items():
            actual = values.get(key)
            if actual is None:
                raise ProviderError(
                    f"EODHD continuation link omitted sent filter {key!r}"
                )
            if actual != expected:
                raise ProviderError(
                    f"EODHD continuation link changed sent filter {key!r}"
                )
        raw_limit = values.get("page[limit]")
        if raw_limit is None:
            raise ProviderError("EODHD continuation link lacks page[limit]")
        if raw_limit.strip() != str(expected_limit):
            raise ProviderError(
                "EODHD continuation link changed the sent page size"
            )
        raw_offset = values.get("page[offset]")
        if raw_offset is None:
            raise ProviderError("EODHD continuation link lacks page[offset]")
        try:
            next_offset = int(raw_offset, 10)
        except ValueError as exc:
            raise ProviderError(
                f"EODHD continuation page[offset] {raw_offset!r} is not an integer"
            ) from exc
        if next_offset < 0 or str(next_offset) != raw_offset.strip():
            raise ProviderError(
                f"EODHD continuation page[offset] {raw_offset!r} is not canonical"
            )
        if full_page and next_offset != expected_offset:
            raise ProviderError(
                "EODHD continuation offset does not advance by the page size"
            )
        if next_offset <= current_offset:
            raise ProviderError("EODHD continuation offset does not advance")
        return next_offset

    # -- row validation ---------------------------------------------------

    @staticmethod
    def _reference_row_validator(sent: dict[str, str]) -> Callable[[dict[str, Any]], None]:
        def validate(row: dict[str, Any]) -> None:
            attributes = row["attributes"]
            for field in _IDENTITY_FIELDS:
                if field not in attributes:
                    raise ProviderError(
                        f"EODHD reference row lacks required field {field!r}"
                    )
            _check_filters(attributes, sent)
            contract = attributes["contract"]
            if not isinstance(contract, str):
                raise ProviderError("EODHD reference contract is not a string")
            try:
                parse_osi_contract(contract)
            except ValueError as exc:
                raise ProviderError(
                    f"EODHD reference contract {contract!r} is not OSI-valid"
                ) from exc
            _check_type(attributes)
            _check_exp_date(attributes)
            _check_strike(attributes)

        return validate

    @staticmethod
    def _eod_row_validator(sent: dict[str, str]) -> Callable[[dict[str, Any]], None]:
        def validate(row: dict[str, Any]) -> None:
            attributes = row["attributes"]
            for field in _IDENTITY_FIELDS + _EOD_REQUIRED_FIELDS:
                if field not in attributes:
                    raise ProviderError(
                        f"EODHD history row lacks required field {field!r}"
                    )
            _check_filters(attributes, sent)
            contract = attributes["contract"]
            if not isinstance(contract, str):
                raise ProviderError("EODHD history contract is not a string")
            try:
                parse_osi_contract(contract)
            except ValueError as exc:
                raise ProviderError(
                    f"EODHD history contract {contract!r} is not OSI-valid"
                ) from exc
            _check_type(attributes)
            _check_exp_date(attributes)
            _check_strike(attributes)

        return validate

    # -- reference extraction ---------------------------------------------

    def _discover_max_expiration(
        self, root: str, start: date, budget: _RequestBudget
    ) -> tuple[date | None, str | None]:
        """Return (discovered max expiration | None, no-data reason | None)."""
        params = [
            ("filter[underlying_symbol]", root),
            ("filter[exp_date_from]", start.isoformat()),
            ("sort", "-exp_date"),
        ]
        try:
            rows = self._paged_query(
                path=_PATH_CONTRACTS,
                params=params,
                budget=budget,
                compact=False,
                row_validator=self._reference_row_validator(dict(params)),
                limit=1,
                single_page=True,
            )
        except _UnknownUnderlying:
            return None, REASON_UNKNOWN_UNDERLYING
        if not rows:
            return None, REASON_NO_CONTRACTS
        exp_date = _parse_iso_date(rows[0]["attributes"]["exp_date"])
        return exp_date, None

    def _guard_max_expiration(
        self, root: str, discovered_max: date, budget: _RequestBudget
    ) -> None:
        """Require zero reference rows beyond the discovered maximum."""
        params = [
            ("filter[underlying_symbol]", root),
            ("filter[exp_date_from]", (discovered_max + timedelta(days=1)).isoformat()),
            ("sort", "exp_date"),
        ]
        rows = self._paged_query(
            path=_PATH_CONTRACTS,
            params=params,
            budget=budget,
            compact=False,
            row_validator=self._reference_row_validator(dict(params)),
            limit=1,
            single_page=True,
        )
        if rows:
            raise ProviderError("EODHD reference maximum is inconsistent")

    def _walk_partitions(
        self,
        root: str,
        low: date,
        high: date,
        budget: _RequestBudget,
        *,
        resource: Literal["contracts", "eod"],
        future_requests: int,
        consume: Callable[[list[dict[str, Any]]], None],
    ) -> None:
        """Exhaustively walk a root's filtered-query row space.

        Deterministic depth-first over disjoint partitions: expiry-date
        interval, then right, then the inclusive OSI strike-integer
        interval. Every saturated partition is split; its partially seen
        rows are discarded and re-read under the children, so each leaf's
        complete rows reach *consume* exactly once and no truncated result
        can be returned. Leaf rows are never filtered against the
        reference catalog: grid rows with valid identities that the
        reference inventory lacks still belong to the grid.
        """
        is_contracts = resource == "contracts"
        path = _PATH_CONTRACTS if is_contracts else _PATH_EOD
        compact = not is_contracts
        indivisible_message = (
            "EODHD contract partition cannot be exhausted"
            if is_contracts
            else "EODHD grid partition cannot be exhausted"
        )
        page_limit = self._page_size
        pending: list[_QueryPartition] = [
            _QueryPartition(expiry_from=low, expiry_to=high)
        ]
        while pending:
            partition = pending.pop()
            siblings = len(pending)

            def reserve_for_query(
                minimum: int, siblings: int = siblings
            ) -> None:
                budget.reserve(minimum + siblings + future_requests)

            params = [
                ("filter[underlying_symbol]", root),
                ("filter[exp_date_from]", partition.expiry_from.isoformat()),
                ("filter[exp_date_to]", partition.expiry_to.isoformat()),
                ("sort", "exp_date"),
            ]
            if partition.source_type is not None:
                params.append(("filter[type]", partition.source_type))
            strike_low = partition.strike_from
            if strike_low is not None:
                strike_high = partition.strike_to
                if strike_high is None:
                    raise ProviderError(
                        "EODHD partition strike bounds must be set together"
                    )
                # Strike bounds are always set together, and the
                # pre-bisection partition sends no strike filters at all:
                # no child ever re-queries an unchanged full strike range.
                params.extend(
                    [
                        ("filter[strike_from]", _strike_param(strike_low)),
                        ("filter[strike_to]", _strike_param(strike_high)),
                    ]
                )
            sent = dict(params)
            try:
                rows = self._paged_query(
                    path=path,
                    params=params,
                    budget=budget,
                    compact=compact,
                    row_validator=(
                        self._reference_row_validator(sent)
                        if is_contracts
                        else self._eod_row_validator(sent)
                    ),
                    probe_ceiling=True,
                    reserve_remaining=reserve_for_query,
                )
            except _QueryCeiling as ceiling:
                if partition.expiry_from != partition.expiry_to:
                    mid_day = partition.expiry_from + timedelta(
                        days=(partition.expiry_to - partition.expiry_from).days // 2
                    )
                    children = [
                        replace(partition, expiry_from=mid_day + timedelta(days=1)),
                        replace(partition, expiry_to=mid_day),
                    ]
                elif partition.source_type is None:
                    children = [
                        replace(partition, source_type="put"),
                        replace(partition, source_type="call"),
                    ]
                elif partition.strike_from is None:
                    # First strike bisection over the full OSI interval;
                    # both children carry both exact endpoints so neither
                    # re-queries an unchanged full strike range.
                    mid_strike = _STRIKE_MAX // 2
                    children = [
                        replace(
                            partition,
                            strike_from=mid_strike + 1,
                            strike_to=_STRIKE_MAX,
                        ),
                        replace(partition, strike_from=0, strike_to=mid_strike),
                    ]
                else:
                    strike_low = partition.strike_from
                    strike_high = partition.strike_to
                    if strike_low is None or strike_high is None:
                        raise ProviderError(indivisible_message) from ceiling
                    if strike_low == strike_high:
                        raise ProviderError(indivisible_message) from ceiling
                    mid_strike = strike_low + (strike_high - strike_low) // 2
                    children = [
                        replace(partition, strike_from=mid_strike + 1),
                        replace(partition, strike_to=mid_strike),
                    ]
                # Every parent source row must be re-read under the
                # disjoint children, so the leaf-page count implied by the
                # ceiling's proven row bound bounds the children's work
                # from below.
                needed = max(
                    len(children),
                    -(-ceiling.minimum_rows // page_limit),
                )
                budget.reserve(needed + siblings + future_requests)
                pending.extend(children)
                continue
            consume(rows)

    def _enumerate_references(
        self,
        root: str,
        low: date,
        high: date,
        budget: _RequestBudget,
        out: dict[str, _Reference],
        *,
        future_requests: int = 0,
    ) -> None:
        """Collect reference contracts with exp_date in inclusive [low, high]."""

        def absorb(rows: list[dict[str, Any]]) -> None:
            for row in rows:
                self._absorb_reference(row, out)

        self._walk_partitions(
            root,
            low,
            high,
            budget,
            resource="contracts",
            future_requests=future_requests,
            consume=absorb,
        )

    def _guard_grid_beyond_reference(
        self, root: str, reference_max: date, budget: _RequestBudget
    ) -> None:
        """Require zero raw grid rows beyond the reference maximum expiry."""
        params = [
            ("filter[underlying_symbol]", root),
            ("filter[exp_date_from]", (reference_max + timedelta(days=1)).isoformat()),
            ("sort", "exp_date"),
        ]
        raw = self._paged_query(
            path=_PATH_EOD,
            params=params,
            budget=budget,
            compact=True,
            row_validator=self._eod_row_validator(dict(params)),
            limit=1,
            single_page=True,
        )
        if raw:
            raise ProviderError("EODHD grid maximum exceeds reference maximum")

    def _absorb_reference(self, row: dict[str, Any], out: dict[str, _Reference]) -> None:
        reference = _reference_from_row(row)
        existing = out.get(reference.contract)
        if existing is not None:
            if existing != reference:
                raise ProviderError(
                    f"EODHD returned conflicting identity for contract "
                    f"{reference.contract!r}"
                )
            return
        out[reference.contract] = reference

    # -- per-contract history ---------------------------------------------

    def _fetch_contract_history(
        self, contract: str, budget: _RequestBudget
    ) -> list[dict[str, Any]]:
        """Fetch and exhaust one contract's EOD history rows."""
        params = [("filter[contract]", contract), ("sort", "-exp_date")]
        try:
            return self._paged_query(
                path=_PATH_EOD,
                params=params,
                budget=budget,
                compact=True,
                row_validator=self._eod_row_validator(dict(params)),
            )
        except _QueryCeiling as exc:
            raise ProviderError(
                f"EODHD contract history for {contract!r} exceeds the source "
                "query ceiling"
            ) from exc

    def _verify_contract_reference(
        self, contract: str, root: str, budget: _RequestBudget
    ) -> _Reference:
        params = [("filter[contract]", contract), ("sort", "exp_date")]
        rows = self._paged_query(
            path=_PATH_CONTRACTS,
            params=params,
            budget=budget,
            compact=False,
            row_validator=self._reference_row_validator(dict(params)),
        )
        if not rows:
            raise ProviderError(
                f"EODHD has no reference record for contract {contract!r}"
            )
        reference = _reference_from_row(rows[0])
        if reference.underlying != root:
            raise ProviderError(
                f"EODHD contract {contract!r} resolves to underlying "
                f"{reference.underlying!r}, not {root!r}"
            )
        return reference

    # -- normalization ------------------------------------------------------

    def _normalise(self, rows: list[dict[str, Any]]) -> pd.DataFrame:
        classifier = self._session_classifier()
        return _normalise(rows, classifier.is_session)

    def _session_classifier(self) -> _SessionClassifier:
        if self._classifier is None:
            self._classifier = _SessionClassifier()
        assert isinstance(self._classifier, _SessionClassifier)
        return self._classifier

    # -- ChainProvider API ---------------------------------------------------

    def fetch_history(
        self,
        root: str,
        start: date,
        end: date,
        *,
        contracts: Sequence[str] | None = None,
        max_requests: int | None = None,
    ) -> pd.DataFrame:
        normalized_root = self._normalize_root(root)
        self._validate_dates(start, end)
        selection = self._normalize_contracts(contracts)
        self._validate_max_requests(max_requests)
        effective_cap = (
            self._config.max_requests() if max_requests is None else max_requests
        )
        budget = _RequestBudget(effective_cap, self._pacing_s)
        started = datetime.now(timezone.utc)

        if selection is not None:
            return self._fetch_history_contracts(
                normalized_root, start, end, selection, effective_cap, budget, started
            )
        return self._fetch_history_root(
            normalized_root, start, end, effective_cap, budget, started
        )

    def _finish(
        self,
        frame: pd.DataFrame,
        *,
        root: str,
        start: date,
        end: date,
        scope: Literal["root", "contracts"],
        selected_contracts: tuple[str, ...] | None,
        reference_contract_count: int,
        requests_used: int,
        started: datetime,
        reason: str | None,
    ) -> pd.DataFrame:
        report = ChainFetchReport(
            root=root,
            start=start,
            end=end,
            scope=scope,
            selected_contracts=selected_contracts,
            reference_contract_count=reference_contract_count,
            extraction_complete=True,
            requests_used=requests_used,
            retrieved_at=started,
            reason=reason,
        )
        frame.attrs[CHAIN_REPORT_ATTR] = report.model_dump(mode="json")
        return frame

    def _fetch_history_root(
        self,
        root: str,
        start: date,
        end: date,
        cap: int,
        budget: _RequestBudget,
        started: datetime,
    ) -> pd.DataFrame:
        discovered_max, reason = self._discover_max_expiration(root, start, budget)
        if discovered_max is None:
            if reason == REASON_NO_CONTRACTS:
                # An empty reference inventory plus raw grid rows is a
                # source inconsistency, not silent no-data.
                params = [
                    ("filter[underlying_symbol]", root),
                    ("filter[exp_date_from]", start.isoformat()),
                    ("sort", "exp_date"),
                ]
                raw = self._paged_query(
                    path=_PATH_EOD,
                    params=params,
                    budget=budget,
                    compact=True,
                    row_validator=self._eod_row_validator(dict(params)),
                    limit=1,
                    single_page=True,
                )
                if raw:
                    raise ProviderError(
                        "EODHD grid has rows without reference inventory"
                    )
            if reason is None:
                raise ProviderError("EODHD no-data reason is missing")
            empty = empty_chain_frame()
            return self._finish(
                empty,
                root=root,
                start=start,
                end=end,
                scope="root",
                selected_contracts=None,
                reference_contract_count=0,
                requests_used=budget.used,
                started=started,
                reason=reason,
            )
        # Guard + reference first page + (grid guard, at least one grid GET)
        # are four proven further requests before any deeper work.
        budget.reserve(4)
        self._guard_max_expiration(root, discovered_max, budget)
        references: dict[str, _Reference] = {}
        self._enumerate_references(
            root, start, discovered_max, budget, references, future_requests=2
        )
        if not references:
            raise ProviderError(
                "EODHD reference enumeration returned no contracts for a "
                "nonempty maximum expiration"
            )
        enumerated_max = max(ref.expiry for ref in references.values())
        if enumerated_max != discovered_max:
            raise ProviderError("EODHD reference maximum is inconsistent")
        self._guard_grid_beyond_reference(root, enumerated_max, budget)
        parts: list[pd.DataFrame] = []

        def absorb_leaf(rows: list[dict[str, Any]]) -> None:
            # Each complete leaf is normalized immediately (source fields,
            # identity and duplicate semantics checked), sliced locally by
            # ID labels, and retained only when nonempty; the raw leaf rows
            # are released before the next query.
            leaf = self._normalise(rows)
            leaf = _slice_window(leaf, start, end)
            if not leaf.empty:
                parts.append(leaf)

        self._walk_partitions(
            root,
            start,
            discovered_max,
            budget,
            resource="eod",
            future_requests=0,
            consume=absorb_leaf,
        )
        if not parts:
            frame = empty_chain_frame()
        else:
            try:
                # Disjoint partition predicates make a shared (as_of,
                # contract) across leaves impossible; concat verifies that.
                frame = pd.concat(parts, verify_integrity=True)
            except ValueError as exc:
                raise ProviderError(
                    "EODHD grid partitions returned overlapping observation IDs"
                ) from exc
            frame = frame.sort_index()
        return self._finish(
            frame,
            root=root,
            start=start,
            end=end,
            scope="root",
            selected_contracts=None,
            reference_contract_count=len(references),
            requests_used=budget.used,
            started=started,
            reason=REASON_NO_ROWS if frame.empty else None,
        )

    def _fetch_history_contracts(
        self,
        root: str,
        start: date,
        end: date,
        selection: tuple[str, ...],
        cap: int,
        budget: _RequestBudget,
        started: datetime,
    ) -> pd.DataFrame:
        references: list[_Reference] = []
        skipped_expired: list[str] = []
        # Every selected contract needs at least one reference-verification
        # request, so a cap below the selection size must fail before any
        # HTTP rather than after partially verifying.
        budget.reserve(len(selection))
        for contract in selection:
            reference = self._verify_contract_reference(contract, root, budget)
            references.append(reference)
            if reference.expiry < start:
                skipped_expired.append(contract)
        rows: list[dict[str, Any]] = []
        reason: str | None = None
        if len(skipped_expired) == len(selection):
            reason = "expiry_before_window"
        else:
            active = [
                ref for ref in references if ref.expiry >= start
            ]
            budget.reserve(len(active))
            for reference in sorted(active, key=lambda ref: ref.contract):
                rows.extend(self._fetch_contract_history(reference.contract, budget))
        frame = self._normalise(rows)
        frame = _slice_window(frame, start, end)
        if reason is None and frame.empty:
            reason = REASON_NO_ROWS
        return self._finish(
            frame,
            root=root,
            start=start,
            end=end,
            scope="contracts",
            selected_contracts=selection,
            reference_contract_count=len(selection),
            requests_used=budget.used,
            started=started,
            reason=reason,
        )

    def fetch_chain(
        self, root: str, as_of: date, *, max_requests: int | None = None
    ) -> pd.DataFrame:
        return self.fetch_history(
            root, as_of, as_of, contracts=None, max_requests=max_requests
        )

    def get_instrument_bounds(
        self, contract: str, *, max_requests: int | None = None
    ) -> dict[str, date | None]:
        """Return exact source observation bounds for one contract.

        ``first_observation``/``last_observation`` come from exhausted EOD
        ids (``None``/``None`` when the contract has no EOD rows). These are
        source observation bounds — never IPO/listing/delisting dates — and
        must not be fed to ``MetadataStore.bootstrap_metadata``.
        """
        if not isinstance(contract, str):
            raise ValueError("contract must be a string")
        normalized = contract.strip().upper()
        try:
            parse_osi_contract(normalized)
        except ValueError as exc:
            raise ValueError(f"contract {contract!r} is not OSI-valid: {exc}") from exc
        self._validate_max_requests(max_requests)
        cap = self._config.max_requests() if max_requests is None else max_requests
        budget = _RequestBudget(cap, self._pacing_s)
        # Identity root: the contract's own OSI prefix is the caller-facing
        # root for a bounds probe; the reference lookup cross-checks it.
        prefix = parse_osi_contract(normalized)
        reference = self._verify_contract_reference(normalized, prefix.root, budget)
        rows = self._fetch_contract_history(reference.contract, budget)
        bounds: list[date] = []
        for row in rows:
            components = parse_eod_id(row["id"])
            bounds.append(components.as_of)
        if not bounds:
            return {"first_observation": None, "last_observation": None}
        return {"first_observation": min(bounds), "last_observation": max(bounds)}


def _reject_constant(name: str) -> float:
    raise ValueError(f"non-finite JSON constant {name!r} is not accepted")


def _strict_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _parse_iso_date(value: object) -> date:
    if not isinstance(value, str):
        raise ProviderError(f"EODHD date field {value!r} is not a string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ProviderError(f"EODHD date {value!r} is not ISO YYYY-MM-DD") from exc


def _strike_param(strike_integer: int) -> str:
    """Render a strike-integer endpoint as an exact decimal thousandths string."""
    return str(Decimal(strike_integer) / Decimal(1000))


def _check_filters(attributes: dict[str, Any], sent: dict[str, str]) -> None:
    underlying = attributes.get("underlying_symbol")
    if "filter[underlying_symbol]" in sent:
        if underlying != sent["filter[underlying_symbol]"]:
            raise ProviderError(
                "EODHD response violates the sent underlying_symbol filter"
            )
    contract = attributes.get("contract")
    if "filter[contract]" in sent and contract != sent["filter[contract]"]:
        raise ProviderError("EODHD response violates the sent contract filter")
    source_type = attributes.get("type")
    if "filter[type]" in sent and source_type != sent["filter[type]"]:
        raise ProviderError("EODHD response violates the sent type filter")
    if "filter[exp_date_from]" in sent:
        exp = _parse_iso_date(attributes.get("exp_date"))
        if exp < _parse_iso_date(sent["filter[exp_date_from]"]):
            raise ProviderError("EODHD response violates the sent exp_date_from filter")
    if "filter[exp_date_to]" in sent:
        exp = _parse_iso_date(attributes.get("exp_date"))
        if exp > _parse_iso_date(sent["filter[exp_date_to]"]):
            raise ProviderError("EODHD response violates the sent exp_date_to filter")
    if "filter[strike_from]" in sent or "filter[strike_to]" in sent:
        thousandths = _strike_thousandths(attributes)
        low = sent.get("filter[strike_from]")
        high = sent.get("filter[strike_to]")
        if low is not None and thousandths < int(Decimal(low) * 1000):
            raise ProviderError("EODHD response violates the sent strike_from filter")
        if high is not None and thousandths > int(Decimal(high) * 1000):
            raise ProviderError("EODHD response violates the sent strike_to filter")


def _strike_thousandths(attributes: dict[str, Any]) -> int:
    value = attributes.get("strike")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderError("EODHD strike is not a number")
    scaled = float(value) * 1000.0
    nearest = round(scaled)
    if not math.isfinite(scaled) or abs(scaled - nearest) > 1e-6:
        raise ProviderError(f"EODHD strike {value!r} is not exact thousandths")
    return int(nearest)


def _check_type(attributes: dict[str, Any]) -> None:
    source_type = attributes.get("type")
    if source_type not in _RIGHT_MAP:
        raise ProviderError(f"EODHD type {source_type!r} is not call/put")


def _check_exp_date(attributes: dict[str, Any]) -> None:
    _parse_iso_date(attributes.get("exp_date"))


def _check_strike(attributes: dict[str, Any]) -> None:
    _strike_thousandths(attributes)


def _reference_from_row(row: dict[str, Any]) -> _Reference:
    attributes = row["attributes"]
    contract = attributes["contract"]
    components: OSIComponents = parse_osi_contract(contract)
    expiry = _parse_iso_date(attributes["exp_date"])
    if expiry != components.expiry:
        raise ProviderError(
            f"EODHD exp_date {expiry} disagrees with contract {contract!r}"
        )
    right = _RIGHT_MAP[attributes["type"]]
    if right != components.right:
        raise ProviderError(
            f"EODHD type {attributes['type']!r} disagrees with contract {contract!r}"
        )
    thousandths = _strike_thousandths(attributes)
    if thousandths != round(components.strike * 1000):
        raise ProviderError(
            f"EODHD strike {attributes['strike']!r} disagrees with contract {contract!r}"
        )
    expiration_type = attributes.get("expiration_type")
    if expiration_type is not None and not isinstance(expiration_type, str):
        raise ProviderError("EODHD expiration_type is not a string or null")
    currency = attributes.get("currency")
    if currency is not None and not isinstance(currency, str):
        raise ProviderError("EODHD currency is not a string or null")
    underlying = attributes.get("underlying_symbol")
    if not isinstance(underlying, str):
        raise ProviderError("EODHD underlying_symbol is not a string")
    return _Reference(
        contract=contract,
        underlying=underlying,
        osi_root=components.root,
        expiry=expiry,
        expiration_type=expiration_type,
        right=right,
        strike_thousandths=thousandths,
        currency=currency,
    )


def _slice_window(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if frame.empty:
        return frame
    as_of = frame.index.get_level_values("as_of")
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    mask = (as_of >= start_ts) & (as_of < end_ts)
    return frame[mask]


def _normalise(
    rows: list[dict[str, Any]],
    session_classifier: Callable[[date], bool],
) -> pd.DataFrame:
    """Convert EOD source rows to the canonical chain DataFrame.

    Observation labels are parsed from the EOD id only; ``tradetime`` and
    the current quote fields on ``/contracts`` rows never influence the
    as-of date. Duplicate ids collapse only when every source field
    matches; conflicting duplicates raise. Quote-quality flags are computed
    and retained — no rows are dropped, clamped, or substituted.
    """
    if not rows:
        return empty_chain_frame()

    seen: dict[tuple[pd.Timestamp, str], dict[str, Any]] = {}
    for row in rows:
        row_id = row["id"]
        attributes = row["attributes"]
        parsed_id = parse_eod_id(row_id)
        contract = attributes.get("contract")
        if contract != parsed_id.contract:
            raise ProviderError(
                f"EODHD id {row_id!r} contract prefix does not match source "
                f"contract {contract!r}"
            )
        key = (pd.Timestamp(parsed_id.as_of, tz="UTC"), parsed_id.contract)
        existing = seen.get(key)
        if existing is not None:
            if existing != row:
                raise ProviderError(
                    f"EODHD returned conflicting duplicate rows for "
                    f"({parsed_id.as_of}, {parsed_id.contract})"
                )
            continue
        seen[key] = row

    records: list[dict[str, Any]] = []
    index_as_of: list[pd.Timestamp] = []
    index_contract: list[str] = []
    for (as_of, contract), row in sorted(seen.items(), key=lambda item: (item[0][0], item[0][1])):
        attributes = row["attributes"]
        components = parse_osi_contract(contract)
        root_value = attributes.get("underlying_symbol")
        if not isinstance(root_value, str):
            raise ProviderError("EODHD underlying_symbol is not a string")
        expiry = _parse_iso_date(attributes.get("exp_date"))
        right = _RIGHT_MAP[attributes["type"]]
        if expiry != components.expiry or right != components.right:
            raise ProviderError(
                f"EODHD identity fields disagree with contract {contract!r}"
            )
        if _strike_thousandths(attributes) != round(components.strike * 1000):
            raise ProviderError(
                f"EODHD strike disagrees with contract {contract!r}"
            )
        expiration_type = attributes.get("expiration_type")
        if expiration_type is not None and not isinstance(expiration_type, str):
            raise ProviderError("EODHD expiration_type is not a string or null")
        currency = attributes.get("currency")
        if currency is not None and not isinstance(currency, str):
            raise ProviderError("EODHD currency is not a string or null")

        record: dict[str, Any] = {
            "root": root_value,
            "osi_root": components.root,
            "expiry": expiry.isoformat(),
            "right": right,
            "expiration_type": expiration_type,
            "currency": currency,
            "strike": components.strike,
            "multiplier": float("nan"),
            "is_standard_contract": standard_contract_heuristic(
                components.root, root_value, expiration_type
            ),
        }
        for source_field, column in _FLOAT_FIELD_MAP:
            record[column] = _optional_number(attributes.get(source_field), source_field)
        bid_raw = _optional_string(attributes.get("bid_date"), "bid_date")
        ask_raw = _optional_string(attributes.get("ask_date"), "ask_date")
        tradetime = _optional_string(attributes.get("tradetime"), "tradetime")
        record["bid_timestamp_raw"] = bid_raw
        record["ask_timestamp_raw"] = ask_raw
        record["tradetime"] = tradetime
        record["bid_timestamp_utc"] = _parse_aware_utc(bid_raw)
        record["ask_timestamp_utc"] = _parse_aware_utc(ask_raw)
        # The session label must come from THIS row's id-derived as_of, not
        # a parsed id left over from the dedupe pass above.
        record["has_non_session_label"] = not session_classifier(as_of.date())
        index_as_of.append(as_of)
        index_contract.append(contract)
        records.append(record)

    frame = pd.DataFrame(records, index=pd.MultiIndex.from_arrays(
        [pd.DatetimeIndex(index_as_of), pd.Index(index_contract, dtype=object)],
        names=CHAIN_INDEX_NAMES,
    ))
    for column in ("bid_timestamp_utc", "ask_timestamp_utc"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    flags = compute_quote_flags(
        frame["bid"], frame["ask"], frame["bid_timestamp_utc"], frame["ask_timestamp_utc"]
    )
    for name, values in flags.items():
        frame[name] = values.astype(bool)
    frame = frame[list(CHAIN_COLUMNS)].sort_index()
    return frame


def _optional_number(value: object, field: str) -> float:
    """Coerce a JSON numeric source field; missing/null → NaN."""
    if value is None:
        return float("nan")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderError(f"EODHD field {field!r} is not a number or null")
    number = float(value)
    if not math.isfinite(number):
        raise ProviderError(f"EODHD field {field!r} is not finite")
    return number


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderError(f"EODHD field {field!r} is not a string or null")
    return value


def _parse_aware_utc(raw: str | None) -> pd.Timestamp:
    """Parse an explicitly offset-aware source timestamp to UTC.

    Naive source strings (e.g. ``2025-03-20 20:14:59``) stay NaT here —
    their raw value is retained in ``*_timestamp_raw`` and the source naive
    timezone remains unverified. Only ``Z``/offset forms normalize.
    """
    if raw is None:
        return pd.Timestamp("NaT")
    try:
        parsed = datetime.fromisoformat(raw.replace(" ", "T"))
    except ValueError:
        return pd.Timestamp("NaT")
    if parsed.tzinfo is None:
        return pd.Timestamp("NaT")
    return pd.Timestamp(parsed.astimezone(timezone.utc))


# Re-exported for orchestration convenience.
__all__ = ["EodhdOptionsProvider"]

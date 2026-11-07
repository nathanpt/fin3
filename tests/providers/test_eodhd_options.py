"""Tests for EodhdOptionsProvider against a simulated documented source.

The transport seam is the provider's own ``_get_json`` (mirroring the
Massive provider test approach); a small server simulator serves pages of
synthetic contracts/values matching the documented envelope — never real
paid prices. The source offset ceiling is simulated by patching the module
constant at the transport boundary, not by generating 11,000 rows.
"""

from __future__ import annotations

import io
import json
import socket
import urllib.error
import urllib.parse
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

import pandas as pd
import pytest

from fin3.config.settings import EodhdOptionsConfig
from fin3.exceptions import (
    ConfigurationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    RequestLimitExceededError,
)
from fin3.options_schemas import CHAIN_REPORT_ATTR
from fin3.providers.eodhd_options import EodhdOptionsProvider

BASE = "https://eodhd.com/api/mp/unicornbay/options"
SECRET = "SECRET-TEST-KEY-0001"


def contract_attrs(
    contract: str,
    *,
    underlying: str = "SHY",
    expiration_type: str = "weekly",
    currency: str | None = "USD",
) -> dict[str, Any]:
    import re

    match = re.match(r"^([A-Z0-9.]{1,6})([0-9]{6})([CP])([0-9]{8})$", contract)
    assert match is not None, contract
    root, yymmdd, right, strike_digits = match.groups()
    exp_date = f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    return {
        "contract": contract,
        "underlying_symbol": underlying,
        "exp_date": exp_date,
        "expiration_type": expiration_type,
        "type": "call" if right == "C" else "put",
        "strike": int(Decimal(strike_digits) / Decimal(1000)),
        "exchange": "AMEX",
        "currency": currency,
    }


def eod_row(
    contract: str,
    as_of: str,
    *,
    underlying: str = "SHY",
    bid: float | None = 0.5,
    ask: float | None = 0.6,
    bid_date: str | None = None,
    ask_date: str | None = None,
    volume: float = 12,
    open_interest: float = 25,
    volatility: float = 0.15,
    tradetime: str | None = "",
    **extra: Any,
) -> dict[str, Any]:
    if bid_date is None:
        bid_date = f"{as_of}T21:14:59.000000Z"
    if ask_date is None:
        ask_date = f"{as_of} 21:14:59"
    if tradetime == "":
        tradetime = as_of
    attrs = contract_attrs(contract, underlying=underlying)
    attrs.update(
        {
            "bid": bid,
            "bid_date": bid_date,
            "bid_size": 10,
            "ask": ask,
            "ask_date": ask_date,
            "ask_size": 10,
            "volume": volume,
            "open_interest": open_interest,
            "volatility": volatility,
            "tradetime": tradetime,
            "open": 0.4,
            "high": 0.7,
            "low": 0.35,
            "last": 0.55,
            "midpoint": 0.55,
            "theoretical": 0.55,
            "delta": 0.4,
            "gamma": 0.01,
            "theta": -0.02,
            "vega": 0.03,
            "rho": 0.01,
        }
    )
    attrs.update(extra)
    return {"id": f"{contract}-{as_of}", "type": "options-eod", "attributes": attrs}


def ref_row(contract: str, **attr_overrides: Any) -> dict[str, Any]:
    attrs = contract_attrs(contract)
    for key, value in attr_overrides.items():
        attrs["underlying_symbol" if key == "underlying" else key] = value
    return {"id": contract, "type": "options-contracts", "attributes": attrs}


class SourceSimulator:
    """Serves a synthetic universe per the documented envelope/paging."""

    def __init__(
        self,
        refs: list[dict[str, Any]],
        eod: dict[str, list[dict[str, Any]]],
        *,
        base: str = BASE,
        unknown_underlying_404: bool = False,
    ) -> None:
        self.refs = refs
        self.eod = eod
        self.base = base
        self.unknown_underlying_404 = unknown_underlying_404
        self.calls: list[str] = []

    def handler(self) -> Callable[..., dict[str, Any]]:
        def handle(url: str, *_args: Any) -> dict[str, Any]:
            self.calls.append(url)
            parsed = urllib.parse.urlsplit(url)
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

            def one(key: str) -> str | None:
                values = qs.get(key)
                return values[0] if values else None

            if self.unknown_underlying_404 and one("filter[underlying_symbol]") == "MISS":
                raise urllib.error.HTTPError(
                    url, 404, "Not Found", None,
                    io.BytesIO(b"Underlying Symbol Not Found."),
                )
            offset = int(one("page[offset]") or 0)
            limit = int(one("page[limit]") or 0)
            sort = one("sort") or "exp_date"
            exp_from = one("filter[exp_date_from]")
            exp_to = one("filter[exp_date_to]")
            source_type = one("filter[type]")
            contract = one("filter[contract]")
            strike_from = one("filter[strike_from]")
            strike_to = one("filter[strike_to]")
            underlying = one("filter[underlying_symbol]")

            if parsed.path.endswith("/eod"):
                rows: list[dict[str, Any]] = []
                for candidate_contract, history in self.eod.items():
                    if contract is not None and candidate_contract != contract:
                        continue
                    rows.extend(history)
            else:
                rows = list(self.refs)
            rows = [
                row
                for row in rows
                if self._matches(
                    row["attributes"], underlying, exp_from, exp_to,
                    source_type, contract, strike_from, strike_to,
                )
            ]
            reverse = sort == "-exp_date"
            rows = sorted(
                rows,
                key=lambda row: (row["attributes"]["exp_date"], row["attributes"]["contract"]),
                reverse=reverse,
            )
            page = rows[offset : offset + limit]
            next_offset: int | None = None
            if len(rows) > offset + limit:
                next_offset = offset + limit
            next_url: str | None = None
            if next_offset is not None:
                endpoint = parsed.path[len(urllib.parse.urlsplit(self.base).path):]
                pairs = [
                    (key, values[0])
                    for key, values in qs.items()
                    if key.startswith("filter[") or key in ("sort",)
                ]
                pairs.extend(
                    [
                        ("page[offset]", str(next_offset)),
                        ("page[limit]", str(limit)),
                    ]
                )
                next_url = f"{self.base}{endpoint}?{urllib.parse.urlencode(pairs)}"
            return {
                "meta": {"offset": offset, "limit": limit, "fields": []},
                "data": page,
                "links": {"next": next_url},
            }

        return handle

    @staticmethod
    def _matches(
        attrs: dict[str, Any],
        underlying: str | None,
        exp_from: str | None,
        exp_to: str | None,
        source_type: str | None,
        contract: str | None,
        strike_from: str | None,
        strike_to: str | None,
    ) -> bool:
        if underlying is not None and attrs["underlying_symbol"] != underlying:
            return False
        if exp_from is not None and attrs["exp_date"] < exp_from:
            return False
        if exp_to is not None and attrs["exp_date"] > exp_to:
            return False
        if source_type is not None and attrs["type"] != source_type:
            return False
        if contract is not None and attrs["contract"] != contract:
            return False
        if strike_from is not None or strike_to is not None:
            thousandths = int(round(float(attrs["strike"]) * 1000))
            if strike_from is not None and thousandths < int(
                Decimal(strike_from) * 1000
            ):
                return False
            if strike_to is not None and thousandths > int(
                Decimal(strike_to) * 1000
            ):
                return False
        return True


class _Resp:
    """Minimal urlopen context-manager response carrying a JSON payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._bytes = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._bytes


def make_provider(
    monkeypatch: pytest.MonkeyPatch,
    simulator: SourceSimulator,
    *,
    page_size: int = 2,
    cap: int = 200,
    pacing: float = 0.0,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> EodhdOptionsProvider:
    """Build a provider whose real HTTP/budget path serves *simulator* pages.

    The seam is ``urllib.request.urlopen``: the provider's own ``_get_json``
    runs, so the request budget, pacing, and envelope validation are all
    exercised.
    """
    import fin3.providers.eodhd_options as module

    config = EodhdOptionsConfig(
        api_key=SECRET,
        page_size=page_size,
        per_run_max_requests=cap,
        request_pacing_s=pacing,
    )
    provider = EodhdOptionsProvider(config)
    handle = simulator.handler()

    def responder(request: Any, timeout: float) -> _Resp:
        return _Resp(handle(str(request.full_url)))

    monkeypatch.setattr(module.urllib.request, "urlopen", responder)
    if clock is not None or sleep is not None:
        original_init = module._RequestBudget.__init__
        original_clock = module.time.monotonic
        original_sleep = module.time.sleep

        def patched_init(
            self: Any, max_requests: int, pacing_s: float, **kwargs: Any
        ) -> None:
            kwargs.setdefault("clock", clock or original_clock)
            kwargs.setdefault("sleep", sleep or original_sleep)
            original_init(self, max_requests, pacing_s, **kwargs)

        monkeypatch.setattr(module, "_RequestBudget", type(
            "_PatchedBudget", (module._RequestBudget,), {"__init__": patched_init}
        ))
    return provider


def wrap_transport(
    monkeypatch: pytest.MonkeyPatch,
    simulator: SourceSimulator,
    mutator: Callable[[dict[str, Any], str], dict[str, Any]],
) -> None:
    """Serve simulator pages with *mutator* applied to each payload."""
    import fin3.providers.eodhd_options as module

    handle = simulator.handler()

    def responder(request: Any, timeout: float) -> _Resp:
        url = str(request.full_url)
        return _Resp(mutator(handle(url), url))

    monkeypatch.setattr(module.urllib.request, "urlopen", responder)


def report_of(frame: pd.DataFrame) -> dict[str, Any]:
    assert CHAIN_REPORT_ATTR in frame.attrs
    return frame.attrs[CHAIN_REPORT_ATTR]


START = date(2026, 11, 6)
END = date(2026, 11, 6)


def standard_universe() -> tuple[SourceSimulator, list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """SHY with two expirations: C91/P96 on 2026-11-20, C92 on 2026-11-27."""
    refs = [
        ref_row("SHY261120C00091000"),
        ref_row("SHY261120P00096000"),
        ref_row("SHY261127C00092000", expiration_type="monthly"),
    ]
    eod = {
        "SHY261120C00091000": [
            eod_row("SHY261120C00091000", "2026-11-05", volume=5, tradetime="2026-11-05"),
            eod_row("SHY261120C00091000", "2026-11-06"),
            eod_row("SHY261120C00091000", "2026-11-07", volume=7, tradetime="2026-11-07"),
        ],
        "SHY261120P00096000": [
            eod_row("SHY261120P00096000", "2026-11-06", bid=0.0, ask=0.01,
                    volume=0, volatility=2.0228, tradetime="2025-03-18"),
        ],
        "SHY261127C00092000": [
            eod_row("SHY261127C00092000", "2026-11-06"),
        ],
    }
    return SourceSimulator(refs, eod), refs, eod


class TestFetchHistoryRoot:
    def test_two_pages_assemble_with_exact_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()
        provider = make_provider(monkeypatch, simulator)
        frame = provider.fetch_history("shy ", START, END)  # strip+upper check
        # as_of 2026-11-05 row is sliced out; 11-07 row too (tradetime inside
        # window but id outside — local slice by ID label).
        assert list(frame.index.get_level_values("contract")) == [
            "SHY261120C00091000", "SHY261120P00096000", "SHY261127C00092000",
        ]
        row = frame.loc[(pd.Timestamp("2026-11-06", tz="UTC"), "SHY261120C00091000")]
        assert row["root"] == "SHY"
        assert row["osi_root"] == "SHY"
        assert row["expiry"] == "2026-11-20"
        assert row["right"] == "C"
        assert row["strike"] == 91.0
        assert pd.isna(row["multiplier"])
        assert row["bid"] == 0.5 and row["ask"] == 0.6
        assert row["volume"] == 12 and row["open_interest"] == 25
        assert row["implied_volatility"] == 0.15
        assert row["bid_timestamp_utc"] == pd.Timestamp("2026-11-06T21:14:59Z")
        assert pd.isna(row["ask_timestamp_utc"])  # naive ask stays NaT
        assert row["ask_timestamp_raw"] == "2026-11-06 21:14:59"
        assert bool(row["is_standard_contract"])
        assert not bool(row["has_zero_bid"])
        # Z-suffixed bid normalized; naive ask string stays NaT → unresolved.
        assert bool(row["has_unresolved_quote_time"])
        report = report_of(frame)
        assert report["scope"] == "root"
        assert report["reference_contract_count"] == 3
        assert report["extraction_complete"] is True
        assert report["reason"] is None
        assert report["requests_used"] == len(simulator.calls)

    def test_volume_zero_does_not_suppress_quotes_and_flags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()
        provider = make_provider(monkeypatch, simulator)
        frame = provider.fetch_history("SHY", START, END)
        row = frame.loc[(pd.Timestamp("2026-11-06", tz="UTC"), "SHY261120P00096000")]
        assert row["volume"] == 0
        assert row["bid"] == 0.0 and row["ask"] == 0.01  # retained, not dropped
        assert bool(row["has_zero_bid"])
        assert not bool(row["has_crossed_quote"])
        assert row["implied_volatility"] == 2.0228  # no IV ceiling
        assert row["tradetime"] == "2025-03-18"  # never used as as_of
        assert frame.index.get_level_values("as_of")[
            list(frame.index.get_level_values("contract")).index("SHY261120P00096000")
        ] == pd.Timestamp("2026-11-06", tz="UTC")

    def test_adjusted_osi_roots_remain_distinct(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        refs = [
            ref_row("SPY261218C00675000", underlying="SPY", expiration_type="monthly"),
            ref_row("XYZ261218C00010000", underlying="SPY", expiration_type="monthly"),
        ]
        eod = {
            "SPY261218C00675000": [eod_row("SPY261218C00675000", "2026-11-06",
                                           underlying="SPY", bid_date=None,
                                           ask_date=None, tradetime=None)],
            "XYZ261218C00010000": [eod_row("XYZ261218C00010000", "2026-11-06",
                                           underlying="SPY")],
        }
        simulator = SourceSimulator(refs, eod)
        provider = make_provider(monkeypatch, simulator)
        frame = provider.fetch_history("SPY", date(2026, 11, 6), date(2026, 11, 6))
        roots = set(frame["root"])
        osi_roots = set(frame["osi_root"])
        assert roots == {"SPY"}
        assert osi_roots == {"SPY", "XYZ"}
        adjusted = frame.loc[
            (pd.Timestamp("2026-11-06", tz="UTC"), "XYZ261218C00010000")
        ]
        assert not bool(adjusted["is_standard_contract"])

    def test_holiday_label_flagged_and_retained(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 2026-01-01 is an XNYS holiday: the row stays on that date and is
        # flagged via the session proxy.
        refs = [ref_row("SHY260102C00091000")]
        eod = {"SHY260102C00091000": [eod_row("SHY260102C00091000", "2026-01-01")]}
        simulator = SourceSimulator(refs, eod)
        provider = make_provider(monkeypatch, simulator)
        frame = provider.fetch_history(
            "SHY", date(2025, 12, 30), date(2026, 1, 5)
        )
        row = frame.loc[(pd.Timestamp("2026-01-01", tz="UTC"), "SHY260102C00091000")]
        assert bool(row["has_non_session_label"])

    def test_same_date_tradetime_not_required(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        refs = [ref_row("SHY261120C00091000")]
        eod = {
            "SHY261120C00091000": [
                eod_row("SHY261120C00091000", "2026-11-06", tradetime=None,
                        bid_date="2026-11-06 08:00:00", ask_date=None,
                        ask=None),
            ]
        }
        simulator = SourceSimulator(refs, eod)
        provider = make_provider(monkeypatch, simulator)
        frame = provider.fetch_history("SHY", START, END)
        assert len(frame) == 1
        assert bool(frame["has_unresolved_quote_time"].iloc[0])
        assert pd.isna(frame["bid_timestamp_utc"].iloc[0])
        assert frame["bid_timestamp_raw"].iloc[0] == "2026-11-06 08:00:00"
        assert frame["tradetime"].iloc[0] is None

    def test_unknown_underlying_classified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import fin3.providers.eodhd_options as module

        def responder(request: Any, timeout: float) -> Any:
            raise urllib.error.HTTPError(
                str(request.full_url), 404, "Not Found", None,
                io.BytesIO(b"Underlying Symbol Not Found."),
            )

        monkeypatch.setattr(module.urllib.request, "urlopen", responder)
        provider = EodhdOptionsProvider(
            EodhdOptionsConfig(api_key=SECRET, request_pacing_s=0.0)
        )
        frame = provider.fetch_history("MISS", START, END)
        assert frame.empty
        assert report_of(frame)["reason"] == "unknown_underlying"

    def test_empty_reference_is_no_contracts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator = SourceSimulator([], {})
        provider = make_provider(monkeypatch, simulator)
        frame = provider.fetch_history("SHY", START, END)
        assert frame.empty
        assert report_of(frame)["reason"] == "source_returned_no_contracts"

    def test_over_bound_row_is_inconsistent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()

        def with_over_bound(payload: dict[str, Any], url: str) -> dict[str, Any]:
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            if qs.get("filter[exp_date_from]", [""])[0] == "2026-11-28":
                return {
                    "meta": {"offset": 0, "limit": 1, "fields": []},
                    "data": [ref_row("SHY261204C00091000")],
                    "links": {"next": None},
                }
            return payload

        provider = make_provider(monkeypatch, simulator)
        wrap_transport(monkeypatch, simulator, with_over_bound)
        with pytest.raises(ProviderError, match="reference maximum is inconsistent"):
            provider.fetch_history("SHY", START, END)

    def test_unexpected_empty_enumeration_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()

        def drop_enumeration(payload: dict[str, Any], url: str) -> dict[str, Any]:
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            keys = qs.keys()
            if "filter[exp_date_to]" in keys and int(
                qs.get("page[limit]", ["0"])[0]
            ) > 1:
                return {
                    "meta": {
                        "offset": int(qs["page[offset]"][0]),
                        "limit": int(qs["page[limit]"][0]),
                        "fields": [],
                    },
                    "data": [],
                    "links": {"next": None},
                }
            return payload

        provider = make_provider(monkeypatch, simulator)
        wrap_transport(monkeypatch, simulator, drop_enumeration)
        with pytest.raises(ProviderError, match="enumeration returned no contracts"):
            provider.fetch_history("SHY", START, END)


class TestSaturationPartition:
    @staticmethod
    def saturated_universe() -> SourceSimulator:
        """Three same-expiration calls (strikes 91/92/93) plus one put (96)."""
        refs = [
            ref_row("SHY261120C00091000"),
            ref_row("SHY261120C00092000"),
            ref_row("SHY261120C00093000"),
            ref_row("SHY261120P00096000"),
        ]
        eod = {
            contract: [eod_row(contract, "2026-11-06")]
            for contract in (
                "SHY261120C00091000", "SHY261120C00092000",
                "SHY261120C00093000", "SHY261120P00096000",
            )
        }
        return SourceSimulator(refs, eod)

    def test_ceiling_splits_expiration_then_right_then_strike(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import fin3.providers.eodhd_options as module

        simulator = self.saturated_universe()
        monkeypatch.setattr(module, "_OFFSET_CEILING", 1)
        provider = make_provider(monkeypatch, simulator)
        frame = provider.fetch_history("SHY", date(2026, 11, 6), date(2026, 11, 6))
        assert set(frame.index.get_level_values("contract")) == {
            "SHY261120C00091000", "SHY261120C00092000",
            "SHY261120C00093000", "SHY261120P00096000",
        }
        # The cascade must have sent both strike endpoints on partitioned
        # call queries, including a bounded high-side leaf.
        strike_queries = [
            url for url in simulator.calls if "filter%5Bstrike_from%5D" in url
        ]
        assert strike_queries, "expected strike-partitioned queries"
        for url in strike_queries:
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            assert "filter[strike_from]" in qs
            assert "filter[strike_to]" in qs
            assert qs["filter[type]"] == ["call"]
        report = report_of(frame)
        assert report["reference_contract_count"] == 4

    def test_unsplittable_partition_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import fin3.providers.eodhd_options as module

        simulator = self.saturated_universe()
        monkeypatch.setattr(module, "_OFFSET_CEILING", 1)
        provider = make_provider(monkeypatch, simulator)
        original = simulator.handler()

        def saturated(url: str, *_args: Any) -> dict[str, Any]:
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            call_at_exp = (
                qs.get("filter[type]", [None])[0] == "call"
                and qs.get("filter[exp_date_from]") == qs.get("filter[exp_date_to]")
                and "filter[strike_from]" not in qs
            )
            left_strike_branch = (
                "filter[strike_from]" in qs
                and qs["filter[strike_from]"] == ["0"]
            )
            if not (call_at_exp or left_strike_branch):
                return original(url)
            # Malformed-source simulation: the query claims a full page with
            # a further continuation. The synthetic row must satisfy the
            # sent filters, so strike-partition leaves derive the row's
            # strike from the strike_to endpoint itself.
            if left_strike_branch:
                thousandths = int(
                    Decimal(qs["filter[strike_to]"][0]) * 1000
                )
                synthetic = ref_row(f"SHY261120C{thousandths:08d}")
            else:
                synthetic = ref_row("SHY261120C00091000")
            pairs = [
                (k, v[0]) for k, v in qs.items()
                if k.startswith("filter[") or k == "sort"
            ]
            offset = int(qs["page[offset]"][0])
            limit = int(qs["page[limit]"][0])
            return {
                "meta": {"offset": offset, "limit": limit, "fields": []},
                "data": [synthetic] * limit,
                "links": {
                    "next": (
                        f"{BASE}/contracts?"
                        + urllib.parse.urlencode(
                            pairs + [
                                ("page[offset]", str(offset + limit)),
                                ("page[limit]", str(limit)),
                            ]
                        )
                    )
                },
            }

        import fin3.providers.eodhd_options as _mod

        def responder(request: Any, timeout: float) -> Any:
            return _Resp(saturated(str(request.full_url)))

        monkeypatch.setattr(_mod.urllib.request, "urlopen", responder)
        with pytest.raises(ProviderError, match="partition cannot be exhausted"):
            provider.fetch_history("SHY", date(2026, 11, 20), date(2026, 11, 20))

    def test_over_ceiling_contract_history_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import fin3.providers.eodhd_options as module

        simulator, _, _ = standard_universe()
        # A 6-row contract history: two full pages, the second page's
        # continuation crossing the patched ceiling.
        simulator.eod["SHY261120C00091000"] = [
            eod_row("SHY261120C00091000", f"2026-11-{day:02d}")
            for day in (2, 3, 4, 5, 6, 7)
        ]
        monkeypatch.setattr(module, "_OFFSET_CEILING", 2)
        provider = make_provider(monkeypatch, simulator)
        with pytest.raises(ProviderError, match="exceeds the source query ceiling"):
            provider.fetch_history("SHY", date(2026, 11, 2), date(2026, 11, 7))


class TestPaginationRules:
    def test_short_final_page_and_null_continuation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()
        provider = make_provider(monkeypatch, simulator)
        frame = provider.fetch_history("SHY", START, END)
        # Enumeration page 0 full (2 rows), page 1 short (1 row, null next).
        assert report_of(frame)["reference_contract_count"] == 3

    def test_nonadvancing_offset_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()

        def stuck_page(payload: dict[str, Any], url: str) -> dict[str, Any]:
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            if (
                "filter[exp_date_to]" in qs
                and qs["page[offset]"][0] == "0"
                and int(qs["page[limit]"][0]) > 1
            ):
                assert payload["links"]["next"] is not None
                payload["links"]["next"] = payload["links"]["next"].replace(
                    "page%5Boffset%5D=2", "page%5Boffset%5D=0"
                )
            return payload

        provider = make_provider(monkeypatch, simulator)
        wrap_transport(monkeypatch, simulator, stuck_page)
        with pytest.raises(ProviderError, match="does not advance"):
            provider.fetch_history("SHY", START, END)

    def test_changed_filter_continuation_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()

        def mutated(payload: dict[str, Any], url: str) -> dict[str, Any]:
            next_link = payload["links"]["next"]
            if next_link is not None:
                payload["links"]["next"] = next_link.replace(
                    "sort=exp_date", "sort=-exp_date"
                )
            return payload

        provider = make_provider(monkeypatch, simulator)
        wrap_transport(monkeypatch, simulator, mutated)
        with pytest.raises(ProviderError, match="changed sent filter"):
            provider.fetch_history("SHY", START, END)

    def test_changed_origin_continuation_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()

        def foreign(payload: dict[str, Any], url: str) -> dict[str, Any]:
            next_link = payload["links"]["next"]
            if next_link is not None:
                payload["links"]["next"] = next_link.replace(
                    "https://eodhd.com", "https://evil.example.com"
                )
            return payload

        provider = make_provider(monkeypatch, simulator)
        wrap_transport(monkeypatch, simulator, foreign)
        with pytest.raises(ProviderError, match="changed origin"):
            provider.fetch_history("SHY", START, END)

    def test_wrong_meta_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        simulator, _, _ = standard_universe()

        def wrong_meta(payload: dict[str, Any], url: str) -> dict[str, Any]:
            payload["meta"]["offset"] = 99
            return payload

        provider = make_provider(monkeypatch, simulator)
        wrap_transport(monkeypatch, simulator, wrong_meta)
        with pytest.raises(ProviderError, match="meta does not match"):
            provider.fetch_history("SHY", START, END)

    def test_malformed_row_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        simulator, _, _ = standard_universe()

        def malformed(payload: dict[str, Any], url: str) -> dict[str, Any]:
            if payload["data"]:
                payload["data"][0] = {"id": 123, "attributes": {}}
            return payload

        provider = make_provider(monkeypatch, simulator)
        wrap_transport(monkeypatch, simulator, malformed)
        with pytest.raises(ProviderError, match="id/attributes"):
            provider.fetch_history("SHY", START, END)

    def test_identical_duplicate_collapses_conflicting_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()
        provider = make_provider(monkeypatch, simulator)
        frame = provider.fetch_history("SHY", START, END)
        assert len(frame) == 3  # no duplicates

        duplicated = {
            "SHY261120C00091000": [
                eod_row("SHY261120C00091000", "2026-11-06"),
                eod_row("SHY261120C00091000", "2026-11-06"),
            ],
            "SHY261120P00096000": [eod_row("SHY261120P00096000", "2026-11-06")],
            "SHY261127C00092000": [eod_row("SHY261127C00092000", "2026-11-06")],
        }
        conflicting = {
            "SHY261120C00091000": [
                eod_row("SHY261120C00091000", "2026-11-06"),
                eod_row("SHY261120C00091000", "2026-11-06", bid=0.9),
            ],
            "SHY261120P00096000": [eod_row("SHY261120P00096000", "2026-11-06")],
            "SHY261127C00092000": [eod_row("SHY261127C00092000", "2026-11-06")],
        }
        provider = make_provider(monkeypatch, SourceSimulator(simulator.refs, duplicated))
        frame = provider.fetch_history("SHY", START, END)
        assert len(frame) == 3

        provider = make_provider(monkeypatch, SourceSimulator(simulator.refs, conflicting))
        with pytest.raises(ProviderError, match="conflicting duplicate"):
            provider.fetch_history("SHY", START, END)


class TestRequestBudget:
    def test_cap_exhaustion_includes_discovery_and_pages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()
        provider = make_provider(monkeypatch, simulator, cap=3)
        with pytest.raises(RequestLimitExceededError) as excinfo:
            provider.fetch_history("SHY", START, END)
        # discovery + guard + enumeration page 0 (2 rows seen); the two
        # seen rows each imply a future history request, so the lower
        # bound fires at 3 + 2 = 5 > 3 instead of burning the cap on
        # further enumeration pages.
        assert "needs at least 5 requests; cap 3" in str(excinfo.value)
        assert excinfo.value.requests_used == 5
        assert excinfo.value.max_requests == 3
        assert len(simulator.calls) == 3  # discovery + guard + enum page 0

    def test_lower_bound_preflight_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()
        provider = make_provider(monkeypatch, simulator, cap=5)
        with pytest.raises(RequestLimitExceededError) as excinfo:
            provider.fetch_history("SHY", START, END)
        # discovery + guard + two enumeration pages precede the reserve(3)
        # for the three EOD histories: 4 + 3 = 7 > 5.
        assert "needs at least 7 requests; cap 5" in str(excinfo.value)
        assert excinfo.value.requests_used == 7

    def test_wide_root_fails_fast_with_lower_bound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: finlab options-feed-backfill-problem (2026-11-07).

        A root whose very first enumeration page already implies more
        history requests than the whole cap allows must fail at the first
        page with the lower bound, not burn the entire cap walking
        reference pages (AAPL one-day shape).
        """
        refs = [
            ref_row(f"SHY261120{right}{strike:08d}")
            for right in "CP"
            for strike in range(50, 90)
        ]
        simulator = SourceSimulator(refs, {})
        provider = make_provider(monkeypatch, simulator, page_size=10, cap=5)
        with pytest.raises(RequestLimitExceededError) as excinfo:
            provider.fetch_history("SHY", START, END)
        # discovery + guard + page 0 (10 rows seen): 3 + 10 = 13 > 5.
        assert "needs at least 13 requests; cap 5" in str(excinfo.value)
        assert excinfo.value.requests_used == 13
        assert len(simulator.calls) == 3

    def test_budget_error_not_ceiling_during_partition_recursion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: finlab options-feed-backfill-problem (2026-11-07).

        A ceiling hit inside the partition cascade must never surface as
        the internal ceiling exception. When budget exhaustion happens in
        a nested enumeration call that is *handling* such a ceiling, the
        terminal shows the ceiling as chained traceback context (which can
        read like "nested retries"); the raised class is the budget error.
        """
        import fin3.providers.eodhd_options as module

        # Three expirations x call/put: with the ceiling at one page, the
        # partition cascade needs >10 requests, so the budget exhausts
        # mid-recursion (before any per-contract history).
        refs = [
            ref_row("SHY261120C00091000"),
            ref_row("SHY261120P00096000"),
            ref_row("SHY261127C00092000"),
            ref_row("SHY261127P00097000"),
            ref_row("SHY270116C00098000"),
            ref_row("SHY270116P00099000"),
        ]
        simulator = SourceSimulator(refs, {})
        monkeypatch.setattr(module, "_OFFSET_CEILING", 1)
        provider = make_provider(monkeypatch, simulator, cap=10)
        with pytest.raises(RequestLimitExceededError) as excinfo:
            provider.fetch_history("SHY", START, END)
        assert type(excinfo.value) is RequestLimitExceededError
        assert len(simulator.calls) <= 10

    def test_contract_scope_reserves_selection_before_http(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()
        provider = make_provider(monkeypatch, simulator, cap=2)
        selection = [
            "SHY261120C00091000",
            "SHY261120P00096000",
            "SHY261127C00092000",
        ]
        with pytest.raises(RequestLimitExceededError) as excinfo:
            provider.fetch_history("SHY", START, END, contracts=selection)
        assert "needs at least 3 requests; cap 2" in str(excinfo.value)
        assert len(simulator.calls) == 0

    def test_pacing_uses_monotonic_clock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()
        now = [100.0]

        def clock() -> float:
            return now[0]

        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            now[0] += seconds

        provider = make_provider(
            monkeypatch, simulator, pacing=0.15, clock=clock, sleep=sleep
        )
        provider.fetch_history("SHY", START, END)
        assert len(simulator.calls) >= 2
        assert sleeps, "expected pacing sleep between requests"
        assert all(s > 0 for s in sleeps)
        # Starts are spaced by at least the pacing interval.
        assert sleeps[0] == pytest.approx(0.15)


class TestErrorClassification:
    def patch_urlopen(self, monkeypatch: pytest.MonkeyPatch, responder: Any) -> None:
        import fin3.providers.eodhd_options as module

        monkeypatch.setattr(module.urllib.request, "urlopen", responder)

    def make_provider_raw(self) -> EodhdOptionsProvider:
        return EodhdOptionsProvider(
            EodhdOptionsConfig(api_key=SECRET, request_pacing_s=0.0)
        )

    @staticmethod
    def response(payload: dict[str, Any]) -> Any:
        class Resp:
            def __enter__(self) -> "Resp":
                return self

            def __exit__(self, *args: Any) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(payload).encode()

        return Resp()

    def test_http_401_typed_and_secret_free(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def responder(request: Any, timeout: float) -> Any:
            raise urllib.error.HTTPError(
                str(request.full_url), 401, "Unauthorized", None, io.BytesIO(b"nope")
            )

        self.patch_urlopen(monkeypatch, responder)
        provider = self.make_provider_raw()
        with pytest.raises(ProviderError, match="HTTP 401") as excinfo:
            provider.fetch_history("SHY", START, END)
        blob = repr(excinfo.value) + repr(excinfo.value.__cause__)
        assert SECRET not in blob

    def test_http_403_typed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def responder(request: Any, timeout: float) -> Any:
            raise urllib.error.HTTPError(
                str(request.full_url), 403, "Forbidden", None, io.BytesIO(b"")
            )

        self.patch_urlopen(monkeypatch, responder)
        with pytest.raises(ProviderError, match="HTTP 403"):
            self.make_provider_raw().fetch_history("SHY", START, END)

    def test_http_422_typed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def responder(request: Any, timeout: float) -> Any:
            raise urllib.error.HTTPError(
                str(request.full_url), 422, "Unprocessable", None,
                io.BytesIO(b'{"errors": {"page.limit": ["too big"]}}'),
            )

        self.patch_urlopen(monkeypatch, responder)
        with pytest.raises(ProviderError, match="HTTP 422"):
            self.make_provider_raw().fetch_history("SHY", START, END)

    def test_http_429_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def responder(request: Any, timeout: float) -> Any:
            raise urllib.error.HTTPError(
                str(request.full_url), 429, "Too Many Requests", None, io.BytesIO(b"")
            )

        self.patch_urlopen(monkeypatch, responder)
        with pytest.raises(ProviderRateLimitError):
            self.make_provider_raw().fetch_history("SHY", START, END)

    def test_non_documented_404_is_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def responder(request: Any, timeout: float) -> Any:
            raise urllib.error.HTTPError(
                str(request.full_url), 404, "Not Found", None, io.BytesIO(b"other")
            )

        self.patch_urlopen(monkeypatch, responder)
        with pytest.raises(ProviderError, match="HTTP 404"):
            self.make_provider_raw().fetch_history("SHY", START, END)

    def test_timeout_typed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def responder(request: Any, timeout: float) -> Any:
            raise urllib.error.URLError(socket.timeout("timed out"))

        self.patch_urlopen(monkeypatch, responder)
        with pytest.raises(ProviderTimeoutError):
            self.make_provider_raw().fetch_history("SHY", START, END)

    def test_malformed_payload_typed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Resp:
            def __enter__(self) -> "Resp":
                return self

            def __exit__(self, *args: Any) -> None:
                return None

            def read(self) -> bytes:
                return b"<html>not json</html>"

        def responder(request: Any, timeout: float) -> Any:
            return Resp()

        self.patch_urlopen(monkeypatch, responder)
        with pytest.raises(ProviderError, match="invalid JSON"):
            self.make_provider_raw().fetch_history("SHY", START, END)

    def test_non_object_payload_typed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.patch_urlopen(monkeypatch, lambda request, timeout: self.response([1, 2]))
        with pytest.raises(ProviderError, match="not an object"):
            self.make_provider_raw().fetch_history("SHY", START, END)

    def test_missing_key_rejected_before_http(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def responder(request: Any, timeout: float) -> Any:
            raise AssertionError("no request may be made without a key")

        self.patch_urlopen(monkeypatch, responder)
        provider = EodhdOptionsProvider(EodhdOptionsConfig(api_key=None))
        with pytest.raises(ConfigurationError, match="EODHD_API_KEY is required"):
            provider.fetch_history("SHY", START, END)
        with pytest.raises(ConfigurationError, match="EODHD_API_KEY is required"):
            provider.get_instrument_bounds("SHY261120C00091000")


class TestContractScope:
    def test_selection_with_skip_and_expiry_before_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()
        provider = make_provider(monkeypatch, simulator)
        # Window 11-06: both contracts observed (SHY261120C00091000 and
        # SHY261127C00092000); the expired-before-window skip is checked
        # with a later window below.
        frame = provider.fetch_history(
            "SHY", START, END,
            contracts=["SHY261120C00091000", "SHY261127C00092000"],
        )
        report = report_of(frame)
        assert report["scope"] == "contracts"
        assert tuple(report["selected_contracts"]) == (
            "SHY261120C00091000", "SHY261127C00092000",
        )
        assert report["reference_contract_count"] == 2
        assert set(frame.index.get_level_values("contract")) == {
            "SHY261120C00091000", "SHY261127C00092000",
        }

        # Window after 2026-11-20: SHY261120C00091000 is skipped (expired
        # before start), the other is fetched but has no rows in-window.
        frame = provider.fetch_history(
            "SHY", date(2026, 11, 24), date(2026, 11, 24),
            contracts=["SHY261120C00091000", "SHY261127C00092000"],
        )
        assert frame.empty
        assert report_of(frame)["reason"] == "source_returned_no_rows"

        frame = provider.fetch_history(
            "SHY", date(2026, 12, 1), date(2026, 12, 2),
            contracts=["SHY261120C00091000"],
        )
        assert frame.empty
        assert report_of(frame)["reason"] == "expiry_before_window"

    def test_unknown_contract_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        simulator, _, _ = standard_universe()
        provider = make_provider(monkeypatch, simulator)
        with pytest.raises(ProviderError, match="no reference record"):
            provider.fetch_history(
                "SHY", START, END, contracts=["ZZZ261120C00091000"]
            )

    def test_wrong_underlying_root_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()
        provider = make_provider(monkeypatch, simulator)
        with pytest.raises(ProviderError, match="resolves to underlying"):
            provider.fetch_history("SPY", START, END, contracts=["SHY261120C00091000"])


class TestBounds:
    def test_bounds_from_exhausted_ids_not_tradetime(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()
        provider = make_provider(monkeypatch, simulator, page_size=2)
        bounds = provider.get_instrument_bounds("SHY261120C00091000")
        assert bounds == {
            "first_observation": date(2026, 11, 5),
            "last_observation": date(2026, 11, 7),
        }

    def test_bounds_absent_history(self, monkeypatch: pytest.MonkeyPatch) -> None:
        simulator, _, _ = standard_universe()
        simulator.eod["SHY261127C00092000"] = []
        provider = make_provider(monkeypatch, simulator)
        assert provider.get_instrument_bounds("SHY261127C00092000") == {
            "first_observation": None,
            "last_observation": None,
        }

    def test_bounds_input_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        simulator, _, _ = standard_universe()
        provider = make_provider(monkeypatch, simulator)
        with pytest.raises(ValueError, match="OSI-valid"):
            provider.get_instrument_bounds("NOT-OSI")
        with pytest.raises(ValueError, match="positive integer"):
            provider.get_instrument_bounds("SHY261120C00091000", max_requests=True)


class TestInputValidation:
    def test_rejects_bad_inputs_before_http(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        simulator, _, _ = standard_universe()
        provider = make_provider(monkeypatch, simulator)
        with pytest.raises(ValueError, match="nonempty"):
            provider.fetch_history("   ", START, END)
        with pytest.raises(ValueError, match="datetime"):
            provider.fetch_history(
                "SHY", datetime(2026, 11, 6, tzinfo=timezone.utc), END  # type: ignore[arg-type]
            )
        with pytest.raises(ValueError, match="after end"):
            provider.fetch_history("SHY", date(2026, 12, 1), START)
        with pytest.raises(ValueError, match="nonempty"):
            provider.fetch_history("SHY", START, END, contracts=[])
        with pytest.raises(ValueError, match="OSI-valid"):
            provider.fetch_history("SHY", START, END, contracts=["nope"])
        with pytest.raises(ValueError, match="positive integer"):
            provider.fetch_history("SHY", START, END, max_requests=0)
        with pytest.raises(ValueError, match="positive integer"):
            provider.fetch_history("SHY", START, END, max_requests=True)
        with pytest.raises(ValueError, match="positive integer"):
            provider.fetch_history("SHY", START, END, max_requests=2.5)
        assert simulator.calls == []

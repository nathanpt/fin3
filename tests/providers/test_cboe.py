"""Tests for CboeProvider with a stubbed urllib transport.

The real provider and real ``_normalise`` run; only the HTTP boundary is
stubbed (``urllib.request.urlopen``), so parsing, caching, slicing, and
typed-error behavior are exercised exactly as production sees them.
"""

from __future__ import annotations

import io
import socket
import urllib.error
from datetime import datetime, timezone
from typing import Callable
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from fin3.config.settings import CboeConfig
from fin3.exceptions import (
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from fin3.providers.cboe import CboeProvider, _normalise
from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution, empty_ohlcv


def _csv(*rows: tuple[str, float, float, float, float]) -> bytes:
    """Build a Cboe-shaped CSV: DATE,OPEN,HIGH,LOW,CLOSE."""
    body = "\n".join(f"{d},{o},{h},{low},{c}" for d, o, h, low, c in rows)
    return (f"DATE,OPEN,HIGH,LOW,CLOSE\n{body}\n").encode()


NOV_2023_CSV = _csv(
    ("11/01/2023", 16.5, 17.2, 16.1, 16.87),
    ("11/02/2023", 15.9, 15.7, 15.2, 15.66),
    ("11/03/2023", 15.1, 15.0, 14.6, 14.91),
    ("11/06/2023", 15.0, 14.9, 14.5, 14.89),
    ("11/07/2023", 14.9, 14.8, 14.4, 14.81),
)


class _Clock:
    """Controlled ``time.monotonic`` replacement; never sleeps."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def _transport(
    payload: bytes | Exception, calls: list[str] | None = None
) -> Callable[..., io.BytesIO]:
    """Build a urlopen side effect serving *payload* once per call."""

    def handler(request: object, timeout: object) -> io.BytesIO:
        if calls is not None:
            calls.append(request.full_url)  # type: ignore[attr-defined]
        if isinstance(payload, Exception):
            raise payload
        return io.BytesIO(payload)

    return handler


def _fetch(
    provider: CboeProvider,
    start: str = "2023-11-01",
    end: str = "2023-11-07T23:59:59",
    symbol: str = "VIX",
    resolution: Resolution = Resolution.ONE_DAY,
    asset_type: AssetType | None = AssetType.INDEX_US,
) -> pd.DataFrame:
    return provider.fetch(
        symbol,
        datetime.fromisoformat(start).replace(tzinfo=timezone.utc),
        datetime.fromisoformat(end).replace(tzinfo=timezone.utc),
        resolution,
        asset_type=asset_type,
    )


class TestFetchEncoding:
    def test_close_only_encoding_and_values(self) -> None:
        provider = CboeProvider(CboeConfig())
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(NOV_2023_CSV),
        ):
            df = _fetch(provider)
        assert list(df.columns) == list(OHLCV_COLUMNS)
        assert isinstance(df.index, pd.DatetimeIndex)
        assert str(df.index.tz) == "UTC"
        assert df.index.name is None
        assert df["close"].tolist() == [16.87, 15.66, 14.91, 14.89, 14.81]
        # Close-only encoding: published close in every price field, real
        # zero volume, float64 everywhere.
        for col in ("open", "high", "low", "close"):
            assert (df[col] == df["close"]).all()
            assert df[col].dtype == np.dtype("float64")
        assert (df["volume"] == 0.0).all()
        assert df["volume"].dtype == np.dtype("float64")
        assert df.index[0] == pd.Timestamp("2023-11-01", tz="UTC")

    def test_date_slice_is_inclusive_and_keeps_midnight_labels(self) -> None:
        provider = CboeProvider(CboeConfig())
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(NOV_2023_CSV),
        ):
            df = _fetch(provider, start="2023-11-02", end="2023-11-06T23:59:59")
        assert [t.date().isoformat() for t in df.index] == [
            "2023-11-02",
            "2023-11-03",
            "2023-11-06",
        ]
        assert df["close"].tolist() == [15.66, 14.91, 14.89]

    def test_range_outside_history_returns_empty(self) -> None:
        provider = CboeProvider(CboeConfig())
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(NOV_2023_CSV),
        ):
            df = _fetch(provider, start="1990-01-01", end="1990-12-31")
        pd.testing.assert_frame_equal(df, empty_ohlcv())

    def test_none_asset_type_is_accepted(self) -> None:
        """MetadataStore discovery calls fetch without an asset type."""
        provider = CboeProvider(CboeConfig())
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(NOV_2023_CSV),
        ):
            df = _fetch(provider, asset_type=None)
        assert len(df) == 5

    def test_extra_kwargs_are_tolerated(self) -> None:
        provider = CboeProvider(CboeConfig())
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(NOV_2023_CSV),
        ):
            df = provider.fetch(
                "VIX",
                datetime(2023, 11, 1, tzinfo=timezone.utc),
                datetime(2023, 11, 2, tzinfo=timezone.utc),
                Resolution.ONE_DAY,
                asset_type=AssetType.INDEX_US,
                some_future_kwarg="ignored",
            )
        assert len(df) == 2


class TestRequestRejection:
    """Symbol/resolution/asset/range validation happens before any HTTP."""

    def _rejects(
        self,
        symbol: str = "VIX",
        resolution: Resolution = Resolution.ONE_DAY,
        asset_type: AssetType | None = AssetType.INDEX_US,
        start: str = "2023-11-01",
        end: str = "2023-11-07",
        match: str = "",
    ) -> None:
        provider = CboeProvider(CboeConfig())

        def explode(request: object, timeout: object) -> None:
            raise AssertionError("HTTP must not be attempted for invalid targets")

        with patch(
            "fin3.providers.cboe.urllib.request.urlopen", side_effect=explode
        ), pytest.raises(ProviderError, match=match):
            _fetch(
                provider,
                start=start,
                end=end,
                symbol=symbol,
                resolution=resolution,
                asset_type=asset_type,
            )

    def test_unknown_caret_symbol_rejected(self) -> None:
        self._rejects(symbol="^VIX", match="Unsupported symbol")

    def test_lowercase_and_other_symbols_rejected(self) -> None:
        self._rejects(symbol="vix", match="Unsupported symbol")
        self._rejects(symbol="VIX9D", match="Unsupported symbol")

    def test_non_daily_resolution_rejected(self) -> None:
        self._rejects(resolution=Resolution.ONE_HOUR, match="only 1d is supported")

    def test_wrong_asset_type_rejected(self) -> None:
        self._rejects(asset_type=AssetType.EQUITY_US, match="expected index_us")
        self._rejects(asset_type=AssetType.CRYPTO, match="expected index_us")

    def test_inverted_range_rejected(self) -> None:
        self._rejects(start="2023-11-07", end="2023-11-01", match="must not be after")


class TestTransportErrors:
    def test_http_429_is_rate_limit(self) -> None:
        provider = CboeProvider(CboeConfig())
        error = urllib.error.HTTPError(
            "url", 429, "Too Many Requests", None, io.BytesIO(b"")
        )
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(error),
        ), pytest.raises(ProviderRateLimitError):
            _fetch(provider)

    def test_http_404_carries_status_and_symbol(self) -> None:
        provider = CboeProvider(CboeConfig())
        error = urllib.error.HTTPError(
            "url", 404, "Not Found", None, io.BytesIO(b"gone")
        )
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(error),
        ), pytest.raises(ProviderError, match=r"HTTP 404 for VIX: Not Found"):
            _fetch(provider)

    def test_url_wrapped_socket_timeout_is_timeout_error(self) -> None:
        provider = CboeProvider(CboeConfig())
        error = urllib.error.URLError(socket.timeout("timed out"))
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(error),
        ), pytest.raises(ProviderTimeoutError):
            _fetch(provider)

    def test_direct_socket_timeout_is_timeout_error(self) -> None:
        provider = CboeProvider(CboeConfig())
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(TimeoutError("read timed out")),
        ), pytest.raises(ProviderTimeoutError):
            _fetch(provider)

    def test_other_url_error_is_provider_error(self) -> None:
        provider = CboeProvider(CboeConfig())
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(urllib.error.URLError("connection refused")),
        ), pytest.raises(ProviderError, match="connection refused"):
            _fetch(provider)


class TestHistoryCache:
    def test_one_download_shared_across_bounds_and_ranges(self) -> None:
        calls: list[str] = []
        provider = CboeProvider(CboeConfig())
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(NOV_2023_CSV, calls),
        ):
            bounds = provider.get_instrument_bounds("VIX")
            first = _fetch(provider, start="2023-11-02", end="2023-11-03")
            second = _fetch(provider, start="2023-11-06", end="2023-11-07")
        assert bounds == {
            "ipo_date": datetime(2023, 11, 1, tzinfo=timezone.utc),
            "delist_date": None,
        }
        assert len(first) == 2 and len(second) == 2
        assert len(calls) == 1
        assert calls[0].endswith("/VIX_History.csv")

    def test_request_sends_fin3_user_agent(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: object, timeout: object) -> io.BytesIO:
            seen.update(dict(request.headers))  # type: ignore[attr-defined]
            return io.BytesIO(NOV_2023_CSV)

        provider = CboeProvider(CboeConfig())
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen", side_effect=handler
        ):
            _fetch(provider)
        assert seen.get("User-agent") == "fin3"

    def test_ttl_expiry_triggers_reload(self) -> None:
        calls: list[str] = []
        clock = _Clock()
        provider = CboeProvider(CboeConfig(cache_ttl_seconds=300.0))
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(NOV_2023_CSV, calls),
        ), patch("fin3.providers.cboe.time.monotonic", clock):
            _fetch(provider)
            assert len(calls) == 1
            clock.now += 299.0
            _fetch(provider)
            assert len(calls) == 1  # still fresh
            clock.now += 2.0  # past expiry
            _fetch(provider)
            assert len(calls) == 2

    def test_failed_reload_never_serves_stale_data(self) -> None:
        calls: list[str] = []
        clock = _Clock()
        provider = CboeProvider(CboeConfig(cache_ttl_seconds=300.0))
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(NOV_2023_CSV, calls),
        ), patch("fin3.providers.cboe.time.monotonic", clock):
            _fetch(provider)
            clock.now += 301.0
            with patch(
                "fin3.providers.cboe.urllib.request.urlopen",
                side_effect=_transport(urllib.error.URLError("source down")),
            ), pytest.raises(ProviderError, match="source down"):
                # The expired snapshot must not be served as a fallback.
                _fetch(provider)
            # Recovery: the next reload attempt succeeds.
            with patch(
                "fin3.providers.cboe.urllib.request.urlopen",
                side_effect=_transport(NOV_2023_CSV, calls),
            ):
                df = _fetch(provider)
            assert len(df) == 5

    def test_returned_slices_are_mutation_isolated(self) -> None:
        provider = CboeProvider(CboeConfig())
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(NOV_2023_CSV),
        ):
            df = _fetch(provider)
            df.iloc[0, df.columns.get_loc("close")] = 999.0
            again = _fetch(provider)
        assert again["close"].iloc[0] == 16.87

    def test_empty_history_is_cached_not_refetched(self) -> None:
        calls: list[str] = []
        provider = CboeProvider(CboeConfig())
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(b"DATE,CLOSE\n", calls),
        ):
            first = _fetch(provider)
            second = _fetch(provider)
        pd.testing.assert_frame_equal(first, empty_ohlcv())
        pd.testing.assert_frame_equal(second, empty_ohlcv())
        assert len(calls) == 1


class TestBoundsAndCost:
    def test_empty_valid_source_bounds_are_none(self) -> None:
        provider = CboeProvider(CboeConfig())
        with patch(
            "fin3.providers.cboe.urllib.request.urlopen",
            side_effect=_transport(b"DATE,CLOSE\n"),
        ):
            bounds = provider.get_instrument_bounds("VIX3M")
        assert bounds == {"ipo_date": None, "delist_date": None}

    def test_bounds_reject_unknown_symbols(self) -> None:
        provider = CboeProvider(CboeConfig())
        with pytest.raises(ProviderError, match="Unsupported symbol"):
            provider.get_instrument_bounds("SPX")

    def test_estimate_cost_is_zero_without_http(self) -> None:
        provider = CboeProvider(CboeConfig())

        def explode(request: object, timeout: object) -> None:
            raise AssertionError("estimate_cost must not touch the network")

        with patch(
            "fin3.providers.cboe.urllib.request.urlopen", side_effect=explode
        ):
            cost = provider.estimate_cost(
                "VIX",
                datetime(2023, 11, 1, tzinfo=timezone.utc),
                datetime(2023, 11, 7, tzinfo=timezone.utc),
                Resolution.ONE_DAY,
                asset_type=AssetType.INDEX_US,
            )
        assert cost == 0.0

    def test_estimate_cost_validates_target(self) -> None:
        provider = CboeProvider(CboeConfig())
        with pytest.raises(ProviderError, match="Unsupported symbol"):
            provider.estimate_cost(
                "^VIX",
                datetime(2023, 11, 1, tzinfo=timezone.utc),
                datetime(2023, 11, 7, tzinfo=timezone.utc),
                Resolution.ONE_DAY,
            )


class TestNormalise:
    def test_zero_row_correctly_headed_csv_is_empty(self) -> None:
        df = _normalise(b"DATE,CLOSE\n")
        pd.testing.assert_frame_equal(df, empty_ohlcv())

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("html", b"<html><body>error</body></html>"),
            ("zero-byte", b""),
            ("whitespace", b"   \n"),
            ("missing-close-header", b"DATE,CLOSEX\n01/02/2024,20.0\n"),
            ("missing-date-header", b"WHEN,CLOSE\n01/02/2024,20.0\n"),
            ("bad-date-format", b"DATE,CLOSE\n2024-01-02,20.0\n"),
            ("bad-number", b"DATE,CLOSE\n01/02/2024,n/a\n"),
            ("nan-close", b"DATE,CLOSE\n01/02/2024,\n"),
            ("nonpositive-close", b"DATE,CLOSE\n01/02/2024,0.0\n"),
            ("negative-close", b"DATE,CLOSE\n01/02/2024,-5.0\n"),
            ("duplicate-dates", b"DATE,CLOSE\n01/02/2024,20.0\n01/02/2024,21.0\n"),
            ("out-of-order", b"DATE,CLOSE\n01/03/2024,21.0\n01/02/2024,20.0\n"),
        ],
    )
    def test_malformed_payloads_raise_provider_error(
        self, label: str, payload: bytes
    ) -> None:
        with pytest.raises(ProviderError):
            _normalise(payload)

    def test_source_open_high_low_are_ignored(self) -> None:
        """47 historical VIX rows violate OHLC inequalities; fin3 neither
        promises nor repairs index OHLC — only DATE/CLOSE are consumed."""
        payload = _csv(("01/02/2024", 50.0, 10.0, 99.0, 20.0))  # h<o, l>h: broken
        df = _normalise(payload)
        assert df["close"].tolist() == [20.0]
        assert (df[["open", "high", "low"]] == 20.0).all().all()

    def test_two_digit_and_single_digit_month_day_parse(self) -> None:
        df = _normalise(_csv(("1/2/2024", 19.0, 21.0, 19.0, 20.0)))
        assert df.index[0] == pd.Timestamp("2024-01-02", tz="UTC")

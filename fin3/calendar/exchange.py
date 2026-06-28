"""Calendar strategy protocol and implementations."""

from __future__ import annotations

from typing import Protocol

import exchange_calendars
import pandas as pd

from fin3.schemas import Resolution


class CalendarStrategy(Protocol):
    """Protocol for trading calendar grid generation.

    Implementations generate a DatetimeIndex of valid bar timestamps within
    the trading session for a given date range and resolution. Used to
    reindex OHLCV data so every expected bar has a row.
    """

    def generate_grid(
        self, start: pd.Timestamp, end: pd.Timestamp, resolution: Resolution
    ) -> pd.DatetimeIndex:
        """Generate a grid of valid bar timestamps.

        Parameters
        ----------
        start : pd.Timestamp
            Start of the range (UTC).
        end : pd.Timestamp
            End of the range (UTC).
        resolution : Resolution
            Bar resolution (e.g. ``1m``, ``1h``).

        Returns
        -------
        pd.DatetimeIndex
            Sorted UTC timestamps for every valid bar in the range.
        """
        ...


class ExchangeCalendarStrategy:
    """Generates master grid using exchange_calendars for a given MIC.

    Produces aligned intraday timestamps for exchanges with defined trading
    sessions, holidays, and early closes. Used for equities (NYSE) and
    futures (CME).
    """

    def __init__(self, mic: str) -> None:
        """Initialise with a Market Identifier Code.

        Parameters
        ----------
        mic : str
            ISO 10383 Market Identifier Code (e.g. ``XNYS``, ``XNAS``, ``CME``).
        """
        self._calendar = exchange_calendars.get_calendar(mic)

    def generate_grid(
        self, start: pd.Timestamp, end: pd.Timestamp, resolution: Resolution
    ) -> pd.DatetimeIndex:
        """Generate an exchange-aligned bar grid.

        Builds per-session bar sequences using the exchange's open/close
        times, excluding holidays and early closes. Intraday sub-day grids
        are trimmed to the requested range when start/end carry intraday time.

        Parameters
        ----------
        start : pd.Timestamp
            Start of the range (UTC).
        end : pd.Timestamp
            End of the range (UTC).
        resolution : Resolution
            Bar resolution (e.g. ``1m`` for minute bars).

        Returns
        -------
        pd.DatetimeIndex
            Sorted UTC timestamps for every valid bar in the range.
        """
        naive_start = start.tz_localize(None) if start.tz is not None else start
        naive_end = end.tz_localize(None) if end.tz is not None else end
        # sessions_in_range requires date-only inputs (midnight)
        date_start = naive_start.normalize()
        date_end = naive_end.normalize()
        sessions = self._calendar.sessions_in_range(date_start, date_end)
        freq = resolution.timedelta_alias

        all_bars: list[pd.DatetimeIndex] = []
        for session in sessions:
            session_ts = pd.Timestamp(session)
            open_time, close_time = self._calendar.session_open_close(session_ts)
            bars = pd.date_range(open_time, close_time, freq=freq)
            if len(bars) > 0 and bars[-1] > close_time:
                bars = bars[:-1]
            all_bars.append(bars)

        if not all_bars:
            return pd.DatetimeIndex([], tz="UTC")

        combined = pd.DatetimeIndex(pd.concat([pd.Series(idx) for idx in all_bars]))
        if combined.tz is None:
            combined = combined.tz_localize("UTC")
        # Trim to the requested range when start/end carry intraday time
        utc_start = start if start.tz is not None else start.tz_localize("UTC")
        utc_end = end if end.tz is not None else end.tz_localize("UTC")
        if utc_start != utc_start.normalize() or utc_end != utc_end.normalize():
            combined = combined[(combined >= utc_start) & (combined <= utc_end)]
        return combined


class ContinuousCalendarStrategy:
    """Generates master grid as a continuous date_range (no holidays). Used for crypto.

    Produces uninterrupted bar timestamps across the full date range at the
    specified resolution. No gaps for weekends, holidays, or exchange closures.
    """

    def generate_grid(
        self, start: pd.Timestamp, end: pd.Timestamp, resolution: Resolution
    ) -> pd.DatetimeIndex:
        """Generate a continuous bar grid with no calendar gaps.

        Parameters
        ----------
        start : pd.Timestamp
            Start of the range (UTC).
        end : pd.Timestamp
            End of the range (UTC).
        resolution : Resolution
            Bar resolution (e.g. ``1h`` for hourly bars).

        Returns
        -------
        pd.DatetimeIndex
            Sorted UTC timestamps at the requested resolution across the range.
        """
        freq = resolution.timedelta_alias
        return pd.date_range(start, end, freq=freq, tz="UTC")

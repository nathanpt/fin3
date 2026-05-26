"""Calendar strategy protocol and implementations."""

from __future__ import annotations

from typing import Protocol

import exchange_calendars
import pandas as pd

from fin3.schemas import Resolution


class CalendarStrategy(Protocol):
    def generate_grid(
        self, start: pd.Timestamp, end: pd.Timestamp, resolution: Resolution
    ) -> pd.DatetimeIndex: ...


class ExchangeCalendarStrategy:
    """Generates master grid using exchange_calendars for a given MIC."""

    def __init__(self, mic: str) -> None:
        self._calendar = exchange_calendars.get_calendar(mic)

    def generate_grid(
        self, start: pd.Timestamp, end: pd.Timestamp, resolution: Resolution
    ) -> pd.DatetimeIndex:
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
    """Generates master grid as a continuous date_range (no holidays). Used for crypto."""

    def generate_grid(
        self, start: pd.Timestamp, end: pd.Timestamp, resolution: Resolution
    ) -> pd.DatetimeIndex:
        freq = resolution.timedelta_alias
        return pd.date_range(start, end, freq=freq, tz="UTC")

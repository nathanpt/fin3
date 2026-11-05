"""Shared quality-gate tests (feat-021).

Covers the single rule registry (``fin3.utils.rules``) end to end:

* provider golden fixtures: real normalization output and ``_prepare_ohlcv``
  artifacts compared against hand-authored expected frames, plus single-field
  mutation coverage of both write stages through the public validators,
* an exhaustive registry-to-case check: every rule needs a passing and a
  failing behavioral case in every scope it declares,
* write/audit parity and deliberately grid-mismatched audit cases,
* deterministic statistical outlier boundaries (seeds, minimum sample,
  zero-MAD, gap handling, threshold direction).

Golden data is hand-authored synthetic data; expected frames are literal and
independent of normalizer execution.
"""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Callable, Literal

import numpy as np
import pandas as pd
import pytest
from structlog.testing import capture_logs

from fin3.core import _prepare_ohlcv
from fin3.exceptions import DataValidationError, SchemaValidationError
from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution, empty_ohlcv
from fin3.utils.integrity import check_integrity
from fin3.utils.rules import RULES, Scope, _statistical_outlier_mask
from fin3.utils.validation import validate_raw_provider_data, validate_storage_artifact
from tests.conftest import make_ohlcv

ONE_MINUTE = Resolution.ONE_MINUTE
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "quality"
PROVIDERS = ("databento", "binance", "massive", "yahoo", "cboe")

SpecFrame = dict
Expectation = tuple[type[BaseException], str] | Literal["accept", "accept_partial"]

_FRAME_KEYS = {"index", "columns", "data"}
_DESCRIPTOR_KEYS = {"index", "index_name", "timezone", "columns", "data"}
_TOP_LEVEL_KEYS = {"resolution", "raw", "normalized", "artifact"}


# ---------------------------------------------------------------------------
# Golden fixture loading — strict stdlib JSON, no network, no SDK clients.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _load_spec(provider: str) -> dict:
    if provider not in PROVIDERS:
        pytest.fail(f"unknown quality provider fixture: {provider}")
    path = FIXTURE_DIR / f"{provider}.json"
    if not path.exists():
        pytest.fail(f"missing quality fixture: {path}")
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        pytest.fail(f"malformed quality fixture {path}: {exc}")
    if set(spec) != _TOP_LEVEL_KEYS:
        pytest.fail(f"{path}: unexpected top-level keys {sorted(spec)}")
    expected_resolution = "1d" if provider == "cboe" else "1m"
    if spec["resolution"] != expected_resolution:
        pytest.fail(
            f"{path}: expected resolution {expected_resolution!r}, "
            f"got {spec['resolution']!r}"
        )
    return spec


def _expected_frame(spec_frame: SpecFrame, origin: str) -> pd.DataFrame:
    if set(spec_frame) != _FRAME_KEYS:
        pytest.fail(f"{origin}: expected frame keys {sorted(_FRAME_KEYS)}, got {sorted(spec_frame)}")
    index = pd.DatetimeIndex(pd.to_datetime(spec_frame["index"], utc=True), name=None)
    data = [[np.nan if value is None else value for value in row] for row in spec_frame["data"]]
    return pd.DataFrame(data, columns=spec_frame["columns"], index=index)


def _raw_input(provider: str, spec: dict) -> object:
    """Decode ``raw`` into the exact shape the real normalizer seam accepts."""
    raw = spec["raw"]
    if provider in ("databento", "yahoo"):
        if set(raw) != _DESCRIPTOR_KEYS:
            pytest.fail(f"{provider}: raw descriptor keys {sorted(raw)}")
        index = pd.DatetimeIndex(pd.to_datetime(raw["index"]))
        if raw["timezone"] is not None:
            index = index.tz_localize(raw["timezone"])
        return pd.DataFrame(
            raw["data"], columns=raw["columns"], index=index.rename(raw["index_name"])
        )
    if provider == "binance":
        return [list(row) for row in raw]
    if provider == "massive":
        return [dict(row) for row in raw]
    if provider == "cboe":
        if not isinstance(raw, str):
            pytest.fail(f"{provider}: raw must be the CSV document as a string")
        return raw.encode("utf-8")
    pytest.fail(f"no raw decoder for provider {provider}")


_NORMALISER_MODULES = {
    "databento": "fin3.providers.databento",
    "binance": "fin3.providers.binance",
    "massive": "fin3.providers.massive",
    "yahoo": "fin3.providers.yfinance",
    "cboe": "fin3.providers.cboe",
}


def _normalise(provider: str, raw: object) -> pd.DataFrame:
    module = import_module(_NORMALISER_MODULES[provider])
    return module._normalise(raw)


def _context(provider: str) -> tuple[Resolution, AssetType | None]:
    """Per-provider validation context from the fixture's own resolution.

    Cboe index data validates at ``1d`` with the INDEX_US volume
    interpretation; every other provider keeps the traded-bar defaults.
    """
    resolution = Resolution(_load_spec(provider)["resolution"])
    return resolution, (AssetType.INDEX_US if provider == "cboe" else None)


@lru_cache(maxsize=None)
def _normalized_frame() -> pd.DataFrame:
    spec = _load_spec("databento")
    return _normalise("databento", _raw_input("databento", spec))


@lru_cache(maxsize=None)
def _artifact_frame() -> pd.DataFrame:
    spec = _load_spec("databento")
    grid = _expected_frame(spec["artifact"], "artifact").index
    return _prepare_ohlcv(_normalized_frame(), grid, ONE_MINUTE)


@lru_cache(maxsize=None)
def _expected_grid() -> pd.DatetimeIndex:
    return _expected_frame(_load_spec("databento")["artifact"], "artifact").index


# ---------------------------------------------------------------------------
# Single-field mutations on golden frames
# ---------------------------------------------------------------------------


def _mutate(frame: pd.DataFrame, mutation: str) -> pd.DataFrame:
    """Return a fresh mutated copy; never touch the input frame."""
    df = frame.copy()
    col = df.columns.get_loc
    if mutation == "low_negative":
        df.iloc[0, col("low")] = -1.0
    elif mutation == "volume_negative":
        df.iloc[0, col("volume")] = -1.0
    elif mutation == "volume_nan":
        df.iloc[0, col("volume")] = np.nan
    elif mutation == "high_below_low":
        df.iloc[0, col("high")] = df.iloc[0, col("low")] - 0.25
    elif mutation == "open_nan":
        df.iloc[0, col("open")] = np.nan
    elif mutation == "volume_zero":
        df.iloc[0, col("volume")] = 0.0
    elif mutation == "volume_positive":
        df.iloc[0, col("volume")] = 1.0
    elif mutation == "shift_second_back":
        stamps = df.index.to_list()
        stamps[1] = stamps[1] - pd.Timedelta(seconds=30)
        df.index = pd.DatetimeIndex(stamps)
    elif mutation == "drop_all_columns":
        df = df.drop(columns=list(df.columns))
    elif mutation.startswith("drop_"):
        df = df.drop(columns=[mutation.removeprefix("drop_")])
    elif mutation == "duplicate_last_row":
        df = pd.concat([df, df.iloc[[-1]]])
    elif mutation == "swap_first_two":
        df = df.iloc[[1, 0] + list(range(2, len(df)))]
    elif mutation == "null_row_close_populated":
        df.iloc[2, col("close")] = 100.0
    else:
        raise AssertionError(f"unknown mutation {mutation}")
    return df


@dataclass(frozen=True)
class Mutation:
    """One golden mutation and the exact outcome of each write stage.

    ``partial_row`` is the mutated frame position expected to carry the
    Stage-1 ``partial_ohlcv`` warning when a stage expects ``accept_partial``
    (index position into the mutated frame; default first row).
    """

    target: Literal["normalized", "artifact"]
    stage1: Expectation
    stage2: Expectation
    partial_row: int = 0


MUTATIONS: dict[str, Mutation] = {
    "low_negative": Mutation(
        "normalized", (DataValidationError, "negative_price"), (DataValidationError, "negative_price")
    ),
    "volume_negative": Mutation(
        "normalized",
        (DataValidationError, "negative_volume"),
        (DataValidationError, "negative_volume"),
    ),
    "volume_nan": Mutation(
        "normalized", (SchemaValidationError, "nan_volume"), (DataValidationError, "nan_volume")
    ),
    "high_below_low": Mutation(
        "normalized",
        (SchemaValidationError, "ohlcv_violation"),
        (SchemaValidationError, "ohlcv_violation"),
    ),
    "open_nan": Mutation("normalized", "accept_partial", (DataValidationError, "nan_semantics")),
    "volume_zero": Mutation("normalized", "accept", (DataValidationError, "nan_semantics")),
    "shift_second_back": Mutation(
        "normalized",
        (SchemaValidationError, "resolution_mismatch"),
        (SchemaValidationError, "resolution_mismatch"),
    ),
    "drop_open": Mutation(
        "normalized", (SchemaValidationError, "missing_column"), (DataValidationError, "missing_column")
    ),
    "drop_high": Mutation(
        "normalized", (SchemaValidationError, "missing_column"), (DataValidationError, "missing_column")
    ),
    "drop_low": Mutation(
        "normalized", (SchemaValidationError, "missing_column"), (DataValidationError, "missing_column")
    ),
    "drop_close": Mutation(
        "normalized", (SchemaValidationError, "missing_column"), (DataValidationError, "missing_column")
    ),
    "drop_volume": Mutation(
        "normalized", (SchemaValidationError, "missing_column"), (DataValidationError, "missing_column")
    ),
    "drop_all_columns": Mutation(
        "normalized", (SchemaValidationError, "missing_column"), (DataValidationError, "missing_column")
    ),
    "duplicate_last_row": Mutation(
        "normalized", (SchemaValidationError, "duplicate"), (SchemaValidationError, "duplicate")
    ),
    "swap_first_two": Mutation(
        "normalized", (SchemaValidationError, "non_monotonic"), (SchemaValidationError, "non_monotonic")
    ),
    "null_row_close_populated": Mutation(
        "artifact", "accept", (DataValidationError, "nan_semantics")
    ),
}


def _provider_mutations(provider: str) -> dict[str, Mutation]:
    """Provider-scoped mutation cases.

    Existing providers keep the exact shared cases. Cboe index rows already
    carry volume 0 everywhere, so ``volume_zero`` is tautological and omitted;
    ``volume_positive`` becomes the index-specific violation (a nonzero
    volume on an index row is impossible), and ``null_row_close_populated``
    is a Stage-1 partial observation warning because the context detects
    some-but-not-all OHLC regardless of volume.
    """
    if provider != "cboe":
        return MUTATIONS
    cases = {key: value for key, value in MUTATIONS.items() if key != "volume_zero"}
    cases["volume_positive"] = Mutation(
        "normalized", "accept", (DataValidationError, "nan_semantics")
    )
    cases["null_row_close_populated"] = Mutation(
        "artifact",
        "accept_partial",
        (DataValidationError, "nan_semantics"),
        partial_row=2,
    )
    return cases


_MUTATION_CASES = [
    (provider, mutation_id)
    for provider in PROVIDERS
    for mutation_id in sorted(_provider_mutations(provider))
]


def _assert_stage(
    validator: Callable[..., None],
    df: pd.DataFrame,
    expectation: Expectation,
    *,
    resolution: Resolution = ONE_MINUTE,
    asset_type: AssetType | None = None,
    partial_ts: pd.Timestamp | None = None,
) -> None:
    if expectation == "accept":
        validator(df, resolution, asset_type=asset_type)
    elif expectation == "accept_partial":
        with capture_logs() as cap:
            validator(df, resolution, asset_type=asset_type)
        events = [e for e in cap if e["event"] == "validation.partial_ohlcv"]
        assert len(events) == 1
        assert events[0]["timestamp"] == (partial_ts if partial_ts is not None else df.index[0])
    else:
        exc_type, rule = expectation
        with pytest.raises(exc_type) as excinfo:
            validator(df, resolution, asset_type=asset_type)
        assert type(excinfo.value) is exc_type
        assert excinfo.value.rule == rule


# ---------------------------------------------------------------------------
# Golden normalization + mutations
# ---------------------------------------------------------------------------


class TestGoldenNormalization:
    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_normalized_matches_expected_frame(self, provider: str) -> None:
        spec = _load_spec(provider)
        expected = _expected_frame(spec["normalized"], "normalized")
        normalized = _normalise(provider, _raw_input(provider, spec))
        assert str(normalized.index.tz) == "UTC"
        assert list(normalized.columns) == list(OHLCV_COLUMNS)
        pd.testing.assert_frame_equal(
            normalized, expected, check_exact=True, check_dtype=False, check_freq=False
        )

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_prepared_artifact_matches_expected_frame(self, provider: str) -> None:
        spec = _load_spec(provider)
        expected_artifact = _expected_frame(spec["artifact"], "artifact")
        normalized = _normalise(provider, _raw_input(provider, spec))
        resolution, asset_type = _context(provider)
        artifact = _prepare_ohlcv(
            normalized,
            expected_artifact.index,
            resolution,
            asset_type=asset_type,
        )
        pd.testing.assert_frame_equal(
            artifact, expected_artifact, check_exact=True, check_dtype=False, check_freq=False
        )

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_canonical_artifact_passes_both_stages(self, provider: str) -> None:
        """The existing null row is a passing boundary, not a violation."""
        spec = _load_spec(provider)
        normalized = _normalise(provider, _raw_input(provider, spec))
        resolution, asset_type = _context(provider)
        artifact = _prepare_ohlcv(
            normalized,
            _expected_frame(spec["artifact"], "artifact").index,
            resolution,
            asset_type=asset_type,
        )
        validate_raw_provider_data(artifact, resolution, asset_type=asset_type)
        validate_storage_artifact(artifact, resolution, asset_type=asset_type)

    @pytest.mark.parametrize(("provider", "mutation_id"), _MUTATION_CASES)
    def test_golden_mutation(self, provider: str, mutation_id: str) -> None:
        mutation = _provider_mutations(provider)[mutation_id]
        spec = _load_spec(provider)
        normalized = _normalise(provider, _raw_input(provider, spec))
        resolution, asset_type = _context(provider)
        if mutation.target == "normalized":
            frame = normalized
        else:
            frame = _prepare_ohlcv(
                normalized,
                _expected_frame(spec["artifact"], "artifact").index,
                resolution,
                asset_type=asset_type,
            )
        mutated = _mutate(frame, mutation_id)
        _assert_stage(
            validate_raw_provider_data,
            mutated,
            mutation.stage1,
            resolution=resolution,
            asset_type=asset_type,
            partial_ts=mutated.index[mutation.partial_row],
        )
        _assert_stage(
            validate_storage_artifact,
            mutated,
            mutation.stage2,
            resolution=resolution,
            asset_type=asset_type,
        )


# ---------------------------------------------------------------------------
# Statistical frame helpers (production helper only; no test-side math)
# ---------------------------------------------------------------------------


def _closes_from_returns(returns: np.ndarray) -> np.ndarray:
    return 100.0 * np.exp(np.concatenate([[0.0], np.cumsum(returns)]))


def _close_volume_frame(
    closes: np.ndarray, start: str = "2024-01-01"
) -> pd.DataFrame:
    index = pd.date_range(start, periods=len(closes), freq="1min", tz="UTC")
    return pd.DataFrame(
        {"close": closes, "volume": np.full(len(closes), 10.0)}, index=index
    )


def _mask_for_returns(returns: np.ndarray) -> pd.Series:
    return _statistical_outlier_mask(_close_volume_frame(_closes_from_returns(returns)), ONE_MINUTE)


def _flagged_positions(mask: pd.Series) -> set[int]:
    return set(np.flatnonzero(mask.to_numpy()).tolist())


def _spike_frame() -> pd.DataFrame:
    closes = _closes_from_returns(np.random.default_rng(0).normal(0.0, 0.001, 239))
    closes[120] *= 10.0
    index = pd.date_range("2024-01-01", periods=len(closes), freq="1min", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
            "volume": 10.0,
        },
        index=index,
    )


def _constant_frame(periods: int) -> pd.DataFrame:
    return _close_volume_frame(np.full(periods, 100.0))


# ---------------------------------------------------------------------------
# Registry-to-case coverage: one case object per (rule, scope, kind).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityCase:
    case_id: str
    rule: str
    scope: Scope
    kind: Literal["valid", "invalid"]
    run: Callable[[], None]


_QUALITY_CASES: list[QualityCase] = []


def _add(
    rule: str,
    scope: Scope,
    kind: Literal["valid", "invalid"],
    run: Callable[[], None],
    *,
    label: str = "",
) -> None:
    case_id = f"{rule}[{scope}/{kind}]"
    if label:
        case_id = f"{case_id}:{label}"
    _QUALITY_CASES.append(QualityCase(case_id, rule, scope, kind, run))


def _stage_invalid(
    validator: Callable[..., None],
    exc_type: type[BaseException],
    rule: str,
    frame_factory: Callable[[], pd.DataFrame],
) -> Callable[[], None]:
    def run() -> None:
        frame = frame_factory()
        with pytest.raises(exc_type) as excinfo:
            validator(frame, ONE_MINUTE)
        assert type(excinfo.value) is exc_type
        assert excinfo.value.rule == rule

    return run


def _stage_accepts(
    validator: Callable[..., None], frame_factory: Callable[[], pd.DataFrame]
) -> Callable[[], None]:
    def run() -> None:
        validator(frame_factory(), ONE_MINUTE)

    return run


def _audit_report(frame: pd.DataFrame, grid: pd.DatetimeIndex | None = None):
    return check_integrity(
        frame,
        frame.index.unique().sort_values() if grid is None else grid,
        ONE_MINUTE,
    )


def _audit_without(rule: str) -> Callable[[], None]:
    def run() -> None:
        report = _audit_report(_artifact_frame(), _expected_grid())
        assert rule not in report.summary

    return run


def _audit_invalid(
    rule: str,
    mutation_id: str,
    expected_ts: Callable[[pd.DataFrame], pd.Timestamp | None],
    *,
    severity: Literal["error", "warning"] = "error",
) -> Callable[[], None]:
    def run() -> None:
        frame = _mutate(_artifact_frame(), mutation_id)
        report = _audit_report(frame)
        issues = [i for i in report.issues if i.category == rule]
        assert issues, f"{rule} missing from audit: {report.summary}"
        assert issues[0].severity == severity
        assert issues[0].timestamp == expected_ts(frame)

    return run


def _partial_ohlcv_valid() -> None:
    with capture_logs() as cap:
        validate_raw_provider_data(_normalized_frame(), ONE_MINUTE)
    assert not [e for e in cap if e["event"] == "validation.partial_ohlcv"]


def _partial_ohlcv_invalid() -> None:
    frame = _mutate(_normalized_frame(), "open_nan")
    with capture_logs() as cap:
        validate_raw_provider_data(frame, ONE_MINUTE)
    events = [e for e in cap if e["event"] == "validation.partial_ohlcv"]
    assert len(events) == 1
    assert events[0]["timestamp"] == frame.index[0]


def _nan_semantics_zero_volume_branch() -> None:
    frame = _mutate(_artifact_frame(), "volume_zero")
    report = _audit_report(frame)
    issues = [i for i in report.issues if i.category == "nan_semantics"]
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].timestamp == frame.index[0]
    assert "volume=0" in issues[0].detail


def _nan_semantics_positive_nan_branch() -> None:
    frame = _mutate(_artifact_frame(), "open_nan")
    report = _audit_report(frame)
    issues = [i for i in report.issues if i.category == "nan_semantics"]
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].timestamp == frame.index[0]
    assert "volume>0" in issues[0].detail
    # partial_ohlcv is a Stage-1 advisory, never an audit category.
    assert "partial_ohlcv" not in report.summary


def _nan_semantics_both_branches() -> None:
    frame = _artifact_frame().copy()
    frame.iloc[0, frame.columns.get_loc("open")] = np.nan  # branch a: volume>0 with NaN OHLC
    frame.iloc[1, frame.columns.get_loc("volume")] = 0.0  # branch b: volume=0 with OHLC
    report = _audit_report(frame)
    issues = [i for i in report.issues if i.category == "nan_semantics"]
    assert len(issues) == 2
    assert report.summary["nan_semantics"] == 2
    assert issues[0].timestamp == frame.index[0]
    assert issues[1].timestamp == frame.index[1]


def _ohlcv_violation_detail_has_values() -> None:
    frame = _mutate(_artifact_frame(), "high_below_low")
    report = _audit_report(frame)
    issue = next(i for i in report.issues if i.category == "ohlcv_violation")
    assert issue.timestamp == frame.index[0]
    for token in ("o=", "h=", "l=", "c="):
        assert token in issue.detail


def _missing_bar_invalid() -> None:
    report = check_integrity(_normalized_frame(), _expected_grid(), ONE_MINUTE)
    issues = [i for i in report.issues if i.category == "missing_bar"]
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].timestamp is None
    assert "1 bar(s)" in issues[0].detail


def _missing_bar_valid() -> None:
    report = check_integrity(_normalized_frame(), _normalized_frame().index, ONE_MINUTE)
    assert "missing_bar" not in report.summary


def _extra_bar_invalid() -> None:
    frame = _artifact_frame()
    extra = frame.iloc[[-1]].copy()
    extra.index = [frame.index[-1] + pd.Timedelta(minutes=1)]
    report = check_integrity(pd.concat([frame, extra]), _expected_grid(), ONE_MINUTE)
    issues = [i for i in report.issues if i.category == "extra_bar"]
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].timestamp is None


def _extra_bar_valid() -> None:
    report = _audit_report(_artifact_frame(), _expected_grid())
    assert "extra_bar" not in report.summary


def _statistical_outlier_audit_invalid() -> None:
    frame = _spike_frame()
    report = _audit_report(frame, frame.index)
    issues = [i for i in report.issues if i.category == "statistical_outlier"]
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].timestamp == frame.index[120]


def _statistical_outlier_audit_valid() -> None:
    report = _audit_report(_constant_frame(150), _constant_frame(150).index)
    assert "statistical_outlier" not in report.summary


# Build the case catalog. Coverage is asserted exhaustively in
# TestRegistryCoverage; the tables below hold the independent expectations.

_INVALID_WRITE: dict[str, tuple[str, type[BaseException], type[BaseException]]] = {
    # rule: (mutation, stage1 exception, stage2 exception)
    "duplicate": ("duplicate_last_row", SchemaValidationError, SchemaValidationError),
    "non_monotonic": ("swap_first_two", SchemaValidationError, SchemaValidationError),
    "resolution_mismatch": ("shift_second_back", SchemaValidationError, SchemaValidationError),
    "missing_column": ("drop_volume", SchemaValidationError, DataValidationError),
    "nan_volume": ("volume_nan", SchemaValidationError, DataValidationError),
    "negative_price": ("low_negative", DataValidationError, DataValidationError),
    "negative_volume": ("volume_negative", DataValidationError, DataValidationError),
    "ohlcv_violation": ("high_below_low", SchemaValidationError, SchemaValidationError),
}
_WRITE_RULES = list(_INVALID_WRITE)

_INVALID_AUDIT: dict[str, tuple[str, Callable[[pd.DataFrame], pd.Timestamp]]] = {
    "duplicate": ("duplicate_last_row", lambda f: f.index[-1]),
    "non_monotonic": ("swap_first_two", lambda f: None),
    "resolution_mismatch": ("shift_second_back", lambda f: None),
    "missing_column": ("drop_volume", lambda f: None),
    "nan_volume": ("volume_nan", lambda f: f.index[0]),
    "negative_price": ("low_negative", lambda f: f.index[0]),
    "negative_volume": ("volume_negative", lambda f: f.index[0]),
    "ohlcv_violation": ("high_below_low", lambda f: f.index[0]),
}

for _rule, (_mutation, _exc1, _exc2) in _INVALID_WRITE.items():
    _add(
        _rule,
        "stage1",
        "invalid",
        _stage_invalid(
            validate_raw_provider_data,
            _exc1,
            _rule,
            (lambda m: lambda: _mutate(_normalized_frame(), m))(_mutation),
        ),
    )
    _add(
        _rule,
        "stage2",
        "invalid",
        _stage_invalid(
            validate_storage_artifact,
            _exc2,
            _rule,
            (lambda m: lambda: _mutate(_artifact_frame(), m))(_mutation),
        ),
    )
    _first = _INVALID_AUDIT[_rule][1]
    _add(_rule, "audit", "invalid", _audit_invalid(_rule, _mutation, _first))
    _add(_rule, "audit", "valid", _audit_without(_rule))
    _add(_rule, "stage1", "valid", _stage_accepts(validate_raw_provider_data, _normalized_frame))
    _add(_rule, "stage2", "valid", _stage_accepts(validate_storage_artifact, _artifact_frame))

_add("nan_semantics", "stage2", "valid", _stage_accepts(validate_storage_artifact, _artifact_frame))
_add("nan_semantics", "stage2", "invalid",
     _stage_invalid(validate_storage_artifact, DataValidationError, "nan_semantics",
                    lambda: _mutate(_artifact_frame(), "open_nan")))
_add("nan_semantics", "stage2", "invalid",
     _stage_invalid(validate_storage_artifact, DataValidationError, "nan_semantics",
                    lambda: _mutate(_artifact_frame(), "volume_zero")),
     label="branch-b")
_add("nan_semantics", "audit", "valid", _audit_without("nan_semantics"))
_add("nan_semantics", "audit", "invalid", _nan_semantics_positive_nan_branch)
_add("nan_semantics", "audit", "invalid", _nan_semantics_zero_volume_branch)
_add("nan_semantics", "audit", "invalid", _nan_semantics_both_branches)
_add("ohlcv_violation", "audit", "invalid", _ohlcv_violation_detail_has_values)

_add("partial_ohlcv", "stage1", "valid", _partial_ohlcv_valid)
_add("partial_ohlcv", "stage1", "invalid", _partial_ohlcv_invalid)

_add("missing_bar", "audit", "invalid", _missing_bar_invalid)
_add("missing_bar", "audit", "valid", _missing_bar_valid)
_add("extra_bar", "audit", "invalid", _extra_bar_invalid)
_add("extra_bar", "audit", "valid", _extra_bar_valid)
_add("statistical_outlier", "audit", "invalid", _statistical_outlier_audit_invalid)
_add("statistical_outlier", "audit", "valid", _statistical_outlier_audit_valid)


class TestRegistryCoverage:
    def test_every_rule_has_valid_and_invalid_case_in_every_scope(self) -> None:
        covered = {(case.rule, case.scope, case.kind) for case in _QUALITY_CASES}
        missing = [
            f"{rule.name}/{scope}/{kind}"
            for rule in RULES
            for scope in sorted(rule.scopes)
            for kind in ("valid", "invalid")
            if (rule.name, scope, kind) not in covered
        ]
        assert missing == []

    def test_cases_reference_registered_rules_and_scopes_only(self) -> None:
        known = {(rule.name, scope) for rule in RULES for scope in rule.scopes}
        unknown = [
            (case.rule, case.scope) for case in _QUALITY_CASES if (case.rule, case.scope) not in known
        ]
        assert unknown == []


@pytest.mark.parametrize("case", _QUALITY_CASES, ids=lambda case: case.case_id)
def test_quality_case(case: QualityCase) -> None:
    case.run()


# ---------------------------------------------------------------------------
# Write/audit parity and deliberately grid-mismatched audit cases
# ---------------------------------------------------------------------------


def _empty_frame() -> pd.DataFrame:
    return empty_ohlcv()


def _zero_column_artifact() -> pd.DataFrame:
    frame = _artifact_frame()
    return frame.drop(columns=list(frame.columns))


def _one_row_frame() -> pd.DataFrame:
    return _artifact_frame().iloc[[0]]


def _flat_equality_frame() -> pd.DataFrame:
    frame = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
    for col in ("open", "high", "low", "close"):
        frame.iloc[2, frame.columns.get_loc(col)] = 100.0
    return frame


def _zero_price_frame() -> pd.DataFrame:
    frame = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
    for col in ("open", "high", "low", "close"):
        frame.iloc[:, frame.columns.get_loc(col)] = 0.0
    return frame


_PARITY_CASES: list[tuple[str, Callable[[], pd.DataFrame]]] = [
    ("clean_artifact", _artifact_frame),
    ("zero_row", _empty_frame),
    ("zero_column_with_timestamps", _zero_column_artifact),
    ("one_row", _one_row_frame),
    ("flat_equality", _flat_equality_frame),
    ("zero_price_no_sign", _zero_price_frame),
    ("spike_artifact", _spike_frame),
]
_PARITY_CASES.extend(
    (f"artifact_{mutation_id}", (lambda mid: lambda: _mutate(_artifact_frame(), mid))(mutation_id))
    for mutation_id in sorted(MUTATIONS)
)


class TestWriteAuditParity:
    @pytest.mark.parametrize("name,factory", _PARITY_CASES, ids=[name for name, _ in _PARITY_CASES])
    def test_stage2_raises_iff_audit_has_error_finding(
        self, name: str, factory: Callable[[], pd.DataFrame]
    ) -> None:
        frame = factory()
        report = _audit_report(frame)
        audit_has_error = any(issue.severity == "error" for issue in report.issues)
        try:
            validate_storage_artifact(frame, ONE_MINUTE)
            raised = False
        except DataValidationError:
            raised = True
        assert raised == audit_has_error

    def test_stage2_passes_while_audit_reports_missing_bars(self) -> None:
        """Stage 2 has no expected-grid argument: sparse data is legal to
        write, while the audit still reports the calendar gap."""
        frame = _normalized_frame()
        validate_storage_artifact(frame, ONE_MINUTE)
        report = check_integrity(frame, _expected_grid(), ONE_MINUTE)
        assert report.summary == {"missing_bar": 1}

    def test_stage2_passes_while_audit_reports_extra_bar_warning(self) -> None:
        frame = _artifact_frame()
        extra = frame.iloc[[-1]].copy()
        extra.index = [frame.index[-1] + pd.Timedelta(minutes=1)]
        off_grid = pd.concat([frame, extra])
        validate_storage_artifact(off_grid, ONE_MINUTE)
        report = check_integrity(off_grid, _expected_grid(), ONE_MINUTE)
        assert report.summary == {"extra_bar": 1}
        assert report.issues[0].severity == "warning"


class TestCombinedCorruption:
    def test_nan_semantics_two_findings_survive_validator(self) -> None:
        frame = _artifact_frame().copy()
        frame.iloc[0, frame.columns.get_loc("open")] = np.nan
        frame.iloc[1, frame.columns.get_loc("volume")] = 0.0
        with pytest.raises(DataValidationError) as excinfo:
            validate_storage_artifact(frame, ONE_MINUTE)
        assert excinfo.value.rule == "nan_semantics"

    def test_duplicate_with_bad_ohlcv_audit_collects_validator_fails_first(self) -> None:
        frame = _mutate(_artifact_frame(), "high_below_low")
        frame = _mutate(frame, "duplicate_last_row")
        report = _audit_report(frame)
        categories = {issue.category for issue in report.issues}
        assert {"duplicate", "ohlcv_violation"} <= categories
        duplicate = next(i for i in report.issues if i.category == "duplicate")
        assert duplicate.timestamp == frame.index[-1]
        with pytest.raises(SchemaValidationError) as excinfo:
            validate_storage_artifact(frame, ONE_MINUTE)
        assert excinfo.value.rule == "duplicate"


class TestInputImmutability:
    def test_consumers_do_not_mutate_inputs(self) -> None:
        frames = [
            _normalized_frame(),
            _artifact_frame(),
            _mutate(_artifact_frame(), "low_negative"),
            _mutate(_artifact_frame(), "open_nan"),
            _mutate(_artifact_frame(), "duplicate_last_row"),
            _spike_frame(),
        ]
        snapshots = [frame.copy(deep=True) for frame in frames]
        for frame in frames:
            with suppress(Exception):
                validate_raw_provider_data(frame, ONE_MINUTE)
            with suppress(Exception):
                validate_storage_artifact(frame, ONE_MINUTE)
            _audit_report(frame)
        for frame, snapshot in zip(frames, snapshots):
            pd.testing.assert_frame_equal(frame, snapshot)


# ---------------------------------------------------------------------------
# Statistical outlier boundaries (production helper only; no test-side math)
# ---------------------------------------------------------------------------


class TestStatisticalOutliers:
    @pytest.mark.parametrize("seed", range(16))
    def test_false_positive_budget_within_declared_models(self, seed: int) -> None:
        gaussian = _mask_for_returns(np.random.default_rng(seed).normal(0.0, 0.001, 10000))
        assert int(gaussian.sum()) <= 10
        student_t = _mask_for_returns(np.random.default_rng(seed).standard_t(5, 10000) * 0.001)
        assert int(student_t.sum()) <= 10

    def test_single_10x_close_mutation_flags_both_ending_rows(self) -> None:
        closes = _closes_from_returns(np.random.default_rng(0).normal(0.0, 0.001, 10000))
        closes[5000] *= 10.0
        mask = _statistical_outlier_mask(_close_volume_frame(closes), ONE_MINUTE)
        assert _flagged_positions(mask) == {5000, 5001}

    def test_minimum_sample_boundary_99_vs_100_returns(self) -> None:
        closes99 = _closes_from_returns(np.random.default_rng(0).normal(0.0, 0.001, 99))
        closes99[50] *= 10.0
        below = _statistical_outlier_mask(_close_volume_frame(closes99), ONE_MINUTE)
        assert not below.any()

        closes100 = _closes_from_returns(np.random.default_rng(0).normal(0.0, 0.001, 100))
        closes100[50] *= 10.0
        at_minimum = _statistical_outlier_mask(_close_volume_frame(closes100), ONE_MINUTE)
        assert _flagged_positions(at_minimum) == {50, 51}

    def test_audit_warning_and_stage2_pass_on_spike_artifact(self) -> None:
        frame = _spike_frame()
        validate_storage_artifact(frame, ONE_MINUTE)  # advisory: must not raise
        report = _audit_report(frame, frame.index)
        assert report.summary == {"statistical_outlier": 1}
        issue = report.issues[0]
        assert issue.severity == "warning"
        assert issue.timestamp == frame.index[120]

    def test_zero_mad_constant_series_yields_nothing(self) -> None:
        mask = _statistical_outlier_mask(_constant_frame(150), ONE_MINUTE)
        assert not mask.any()

    def test_null_bars_are_not_bridged(self) -> None:
        closes = _closes_from_returns(np.random.default_rng(1).normal(0.0, 0.001, 121))
        closes[62:] *= 10.0  # jump exactly at the gap crossing
        frame = _close_volume_frame(closes)
        frame.iloc[61, frame.columns.get_loc("close")] = np.nan
        frame.iloc[61, frame.columns.get_loc("volume")] = 0.0
        mask = _statistical_outlier_mask(frame, ONE_MINUTE)
        assert not mask.any()

    def test_zero_close_pair_excluded(self) -> None:
        closes = _closes_from_returns(np.random.default_rng(2).normal(0.0, 0.001, 149))
        closes[75] = 0.0
        mask = _statistical_outlier_mask(_close_volume_frame(closes), ONE_MINUTE)
        assert not mask.any()

    def test_nonfinite_close_and_volume_excluded(self) -> None:
        closes = _closes_from_returns(np.random.default_rng(3).normal(0.0, 0.001, 149))
        closes[75] = np.nan
        frame = _close_volume_frame(closes)
        frame.iloc[100, frame.columns.get_loc("volume")] = np.nan
        mask = _statistical_outlier_mask(frame, ONE_MINUTE)
        assert not mask.any()

    def test_unordered_and_duplicate_index_suppressed(self) -> None:
        frame = _close_volume_frame(
            _closes_from_returns(np.random.default_rng(4).normal(0.0, 0.001, 149))
        )
        assert not _statistical_outlier_mask(frame.iloc[::-1], ONE_MINUTE).any()
        duplicated = pd.concat([frame, frame.iloc[[-1]]])
        assert not _statistical_outlier_mask(duplicated, ONE_MINUTE).any()

    def test_jump_across_oversized_time_gap_not_flagged(self) -> None:
        closes = _closes_from_returns(np.random.default_rng(5).normal(0.0, 0.001, 149))
        closes[76:] *= 10.0  # jump exactly at the gap crossing
        frame = _close_volume_frame(closes)
        frame = frame.drop(frame.index[75])
        mask = _statistical_outlier_mask(frame, ONE_MINUTE)
        assert not mask.any()

    @pytest.mark.parametrize(
        ("last_return", "expected"), [(0.0118, set()), (0.0119, {100})]
    )
    def test_threshold_direction_pinned(
        self, last_return: float, expected: set[int]
    ) -> None:
        returns = np.array([-0.001] * 49 + [0.0, 0.0] + [0.001] * 48 + [last_return])
        mask = _mask_for_returns(returns)
        assert _flagged_positions(mask) == expected


# ---------------------------------------------------------------------------
# INDEX_US context: populated zero-volume rows are observations
# ---------------------------------------------------------------------------


def _index_artifact() -> pd.DataFrame:
    spec = _load_spec("cboe")
    grid = _expected_frame(spec["artifact"], "artifact").index
    return _prepare_ohlcv(
        _normalise("cboe", _raw_input("cboe", spec)),
        grid,
        Resolution.ONE_DAY,
        asset_type=AssetType.INDEX_US,
    )


class TestIndexContext:
    """INDEX_US keeps every rule active with the index volume interpretation.

    Existing traded-bar cases above deliberately run with ``asset_type=None``
    and are unchanged; this class pins the index branch of the same registry.
    """

    def test_artifact_passes_both_stages(self) -> None:
        frame = _index_artifact()
        validate_raw_provider_data(
            frame, Resolution.ONE_DAY, asset_type=AssetType.INDEX_US
        )
        validate_storage_artifact(
            frame, Resolution.ONE_DAY, asset_type=AssetType.INDEX_US
        )

    def test_volume_positive_rejected_stage2(self) -> None:
        frame = _index_artifact()
        frame.iloc[0, frame.columns.get_loc("volume")] = 1.0
        validate_raw_provider_data(
            frame, Resolution.ONE_DAY, asset_type=AssetType.INDEX_US
        )
        with pytest.raises(DataValidationError) as excinfo:
            validate_storage_artifact(
                frame, Resolution.ONE_DAY, asset_type=AssetType.INDEX_US
            )
        assert excinfo.value.rule == "nan_semantics"

    def test_missing_close_warns_stage1_and_rejects_stage2(self) -> None:
        """A missing close is a partial observation for index data — found
        without volume, and a Stage-2 corruption error."""
        frame = _index_artifact()
        frame.iloc[1, frame.columns.get_loc("close")] = np.nan
        with capture_logs() as cap:
            validate_raw_provider_data(
                frame, Resolution.ONE_DAY, asset_type=AssetType.INDEX_US
            )
        events = [e for e in cap if e["event"] == "validation.partial_ohlcv"]
        assert len(events) == 1
        assert events[0]["timestamp"] == frame.index[1]
        with pytest.raises(DataValidationError) as excinfo:
            validate_storage_artifact(
                frame, Resolution.ONE_DAY, asset_type=AssetType.INDEX_US
            )
        assert excinfo.value.rule == "nan_semantics"

    def test_ohlcv_violation_checked_despite_zero_volume(self) -> None:
        frame = _index_artifact()
        frame.iloc[0, frame.columns.get_loc("high")] = 1.0  # high < close
        with pytest.raises(SchemaValidationError) as excinfo:
            validate_storage_artifact(
                frame, Resolution.ONE_DAY, asset_type=AssetType.INDEX_US
            )
        assert excinfo.value.rule == "ohlcv_violation"

    def test_daily_zero_volume_spike_flags_audit_outlier(self) -> None:
        """≥100 eligible zero-volume daily returns make the statistical rule
        active for index data; advisory warnings never block the write."""
        rng = np.random.default_rng(7)
        closes = 20.0 * np.exp(
            np.concatenate([[0.0], np.cumsum(rng.normal(0.0, 0.001, 150))])
        )
        closes[80] *= 10.0
        index = pd.date_range("2023-01-02", periods=len(closes), freq="1D", tz="UTC")
        frame = pd.DataFrame(
            {
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": np.zeros(len(closes)),
            },
            index=index,
        )
        validate_storage_artifact(
            frame, Resolution.ONE_DAY, asset_type=AssetType.INDEX_US
        )
        report = check_integrity(
            frame, frame.index, Resolution.ONE_DAY, asset_type=AssetType.INDEX_US
        )
        issues = [i for i in report.issues if i.category == "statistical_outlier"]
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].timestamp == frame.index[80]

    def test_inserted_null_is_not_bridged_by_outlier_rule(self) -> None:
        rng = np.random.default_rng(7)
        closes = 20.0 * np.exp(
            np.concatenate([[0.0], np.cumsum(rng.normal(0.0, 0.001, 150))])
        )
        closes[80] *= 10.0
        index = pd.date_range("2023-01-02", periods=len(closes), freq="1D", tz="UTC")
        frame = pd.DataFrame(
            {
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": np.zeros(len(closes)),
            },
            index=index,
        )
        frame.iloc[80, frame.columns.get_loc("close")] = np.nan
        report = check_integrity(
            frame, frame.index, Resolution.ONE_DAY, asset_type=AssetType.INDEX_US
        )
        assert "statistical_outlier" not in report.summary

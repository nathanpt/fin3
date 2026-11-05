"""Two-stage validation pipeline.

Stage 1 (validate_raw_provider_data): structural checks on raw provider output.
Stage 2 (validate_storage_artifact): strict checks on the padded storage artifact.

Both stages evaluate the shared rule registry (``fin3.utils.rules``) in its
fixed order and raise on the first error-severity finding; warning findings
are logged and never raise.
"""

from __future__ import annotations

import pandas as pd
import structlog

from fin3.exceptions import DataValidationError, SchemaValidationError
from fin3.schemas import AssetType, Resolution
from fin3.utils.rules import RuleContext, Scope, evaluate_rules

logger = structlog.get_logger(__name__)

_PARTIAL_OHLCV_MSG = "volume>0 but some OHLC are NaN"

# Exact exception type per rule per stage. Warnings never raise.
_STAGE1_ERROR_TYPES: dict[str, type[DataValidationError]] = {
    "duplicate": SchemaValidationError,
    "non_monotonic": SchemaValidationError,
    "resolution_mismatch": SchemaValidationError,
    "missing_column": SchemaValidationError,
    "nan_volume": SchemaValidationError,
    "negative_price": DataValidationError,
    "negative_volume": DataValidationError,
    "ohlcv_violation": SchemaValidationError,
}

_STAGE2_ERROR_TYPES: dict[str, type[DataValidationError]] = {
    "duplicate": SchemaValidationError,
    "non_monotonic": SchemaValidationError,
    "resolution_mismatch": SchemaValidationError,
    "missing_column": DataValidationError,
    "nan_volume": DataValidationError,
    "negative_price": DataValidationError,
    "negative_volume": DataValidationError,
    "nan_semantics": DataValidationError,
    "ohlcv_violation": SchemaValidationError,
}


def _validate(
    df: pd.DataFrame,
    resolution: Resolution,
    scope: Scope,
    error_types: dict[str, type[DataValidationError]],
    *,
    asset_type: AssetType | None = None,
) -> None:
    """Evaluate the registry for *scope*, raising on the first error."""
    context = RuleContext(resolution=resolution, scope=scope, asset_type=asset_type)
    for rule, finding in evaluate_rules(df, context):
        if rule.severity == "error":
            error = error_types[rule.name](f"[{scope}] {rule.name}: {finding.detail}")
            error.rule = rule.name
            raise error
        if rule.name == "partial_ohlcv":
            logger.warning(
                "validation.partial_ohlcv",
                timestamp=finding.timestamp,
                msg=_PARTIAL_OHLCV_MSG,
            )


def validate_raw_provider_data(
    df: pd.DataFrame,
    resolution: Resolution,
    *,
    asset_type: AssetType | None = None,
) -> None:
    """Stage 1: structural validation on raw provider data (pre-reindex).

    Accepts partial/sparse data and populated zero-volume bars. Rejects
    corrupt structural issues. Prices and volume must be non-negative.
    ``asset_type`` selects the volume interpretation (index rows are judged
    by close presence, not traded volume).
    """
    _validate(df, resolution, "stage1", _STAGE1_ERROR_TYPES, asset_type=asset_type)


def validate_storage_artifact(
    df: pd.DataFrame,
    resolution: Resolution,
    *,
    asset_type: AssetType | None = None,
) -> None:
    """Stage 2: strict validation on the fully-padded storage artifact (post-reindex).

    The artifact must conform to the NaN semantics: for traded bars, volume=0
    implies all OHLC NaN and volume>0 implies no NaN in any OHLCV column; for
    index rows (``asset_type=AssetType.INDEX_US``), volume must be exactly 0
    with either all or no OHLC populated. Prices and volume must be
    non-negative.
    """
    _validate(df, resolution, "stage2", _STAGE2_ERROR_TYPES, asset_type=asset_type)

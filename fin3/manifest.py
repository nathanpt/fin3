"""Declarative dataset manifest: strict loading, resolution, and hashing.

A manifest declares *what* data must exist (asset type, provider, resolution,
calendar, symbols, date window, update policy). :func:`load_manifest` parses a
YAML/JSON declaration into immutable :class:`ResolvedDataset` records with
canonical physical library names, resolved symbol sets, and a stable
``declaration_hash`` that changes only when the *effective* declaration
changes (resolved symbols, bounds, provider, policy — not comments, key
order, whitespace, or the resolved value of ``end: now``).
"""

from __future__ import annotations

import glob as _globmod
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fin3.exceptions import ConfigurationError
from fin3.schemas import AssetType, Resolution, library_name

__all__ = [
    "UpdatePolicy",
    "SymbolSelectorSpec",
    "ManifestDeclaration",
    "ResolvedDataset",
    "ResolvedManifest",
    "load_manifest",
]

MANIFEST_VERSION = 1
"""The only accepted declaration version."""

_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_DATE_ONLY = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
_WILDCARDS = "*?[]"
_END_OF_DAY_TIME = dt_time(23, 59, 59, 999999, tzinfo=timezone.utc)

_DEFAULT_CALENDAR: dict[AssetType, str] = {
    AssetType.EQUITY_US: "XNYS",
    AssetType.FUTURES: "CME",
    AssetType.CRYPTO: "24x7",
    AssetType.INDEX_US: "XNYS",
}


class UpdatePolicy(str, Enum):
    """How an applying command reconciles stored data with the declaration."""

    INCREMENTAL = "incremental"
    FULL_REFRESH = "full-refresh"
    APPEND_ONLY = "append-only"


# ---------------------------------------------------------------------------
# Timestamp coercion
# ---------------------------------------------------------------------------


def _coerce_timestamp(value: object, *, is_end: bool) -> str:
    """Normalise a declared timestamp to a canonical UTC string.

    Accepts timezone-aware RFC3339 strings, date-only ``YYYY-MM-DD``, and the
    YAML-native ``datetime``/``date`` objects those documents produce.
    Date-only starts are midnight UTC; date-only ends are that date at
    ``23:59:59.999999`` UTC. Naive timestamps are rejected.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                f"naive timestamp {value.isoformat()!r} is not allowed; "
                "use an RFC3339 timestamp with an explicit offset"
            )
        return value.astimezone(timezone.utc).isoformat()

    if isinstance(value, date):
        d = value
    elif isinstance(value, str):
        text = value.strip()
        if _DATE_ONLY.match(text):
            try:
                d = date.fromisoformat(text)
            except ValueError as exc:
                raise ValueError(f"invalid date {text!r}") from exc
        else:
            try:
                dt = datetime.fromisoformat(text)
            except ValueError as exc:
                raise ValueError(f"invalid timestamp {text!r}") from exc
            return _coerce_timestamp(dt, is_end=is_end)
    else:
        raise ValueError(
            f"expected an RFC3339 timestamp or YYYY-MM-DD date, got {value!r}"
        )

    if is_end:
        return datetime.combine(d, _END_OF_DAY_TIME).isoformat()
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).isoformat()


def _parse_canonical(value: str) -> datetime:
    """Parse a canonical timestamp produced by :func:`_coerce_timestamp`."""
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# Pydantic declaration models
# ---------------------------------------------------------------------------


class SymbolSelectorSpec(BaseModel):
    """Symbol selection: either form expands the same way.

    ``symbols: [AAPL, "@lists/tech.txt"]`` becomes
    ``{include: [...], exclude: []}``.
    """

    model_config = ConfigDict(extra="forbid")

    include: list[str]
    exclude: list[str] = []

    @field_validator("include")
    @classmethod
    def _include_nonempty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("symbols include list must be nonempty")
        return v

    @field_validator("include", "exclude", mode="before")
    @classmethod
    def _string_items(cls, v: object) -> object:
        if isinstance(v, list):
            for item in v:
                if not isinstance(item, str):
                    raise ValueError(f"symbol selectors must be strings, got {item!r}")
        return v


class LibraryEntry(BaseModel):
    """One requested dataset inside ``libraries``."""

    model_config = ConfigDict(extra="forbid")

    asset_type: AssetType
    provider: str
    resolution: Resolution
    calendar: str | None = None
    symbols: SymbolSelectorSpec
    start: str
    end: str
    update_policy: UpdatePolicy = UpdatePolicy.INCREMENTAL

    @field_validator("provider")
    @classmethod
    def _provider_shape(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("provider must be a nonempty string")
        return v

    @field_validator("start", mode="before")
    @classmethod
    def _coerce_start(cls, v: object) -> str:
        try:
            return _coerce_timestamp(v, is_end=False)
        except ValueError as exc:
            raise ValueError(f"start: {exc}") from exc

    @field_validator("end", mode="before")
    @classmethod
    def _coerce_end(cls, v: object) -> str:
        if isinstance(v, str) and v.strip() == "now":
            return "now"
        try:
            return _coerce_timestamp(v, is_end=True)
        except ValueError as exc:
            raise ValueError(f"end: {exc}") from exc

    @field_validator("symbols", mode="before")
    @classmethod
    def _accept_bare_list(cls, v: object) -> object:
        if isinstance(v, list):
            return {"include": v, "exclude": []}
        return v

    @model_validator(mode="after")
    def _resolve_calendar(self) -> "LibraryEntry":
        if (
            self.asset_type is AssetType.INDEX_US
            and self.resolution is not Resolution.ONE_DAY
        ):
            raise ValueError("index_us supports only resolution 1d")
        expected = _DEFAULT_CALENDAR[self.asset_type]
        if self.calendar is None:
            self.calendar = expected
        elif self.calendar != expected:
            raise ValueError(
                f"calendar {self.calendar!r} does not match asset_type "
                f"{self.asset_type.value!r} (expected {expected!r}); "
                "calendar overrides are not supported"
            )
        return self


class ManifestDeclaration(BaseModel):
    """Strict document shape: exactly ``version``, ``id``, ``libraries``."""

    model_config = ConfigDict(extra="forbid")

    version: int  # strictly validated below (bool/float/str coercions rejected)
    id: str
    libraries: dict[str, LibraryEntry] = Field(min_length=1)

    @field_validator("version", mode="before")
    @classmethod
    def _strict_version(cls, v: object) -> object:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(
                f"version must be the integer {MANIFEST_VERSION}, got {v!r}"
            )
        if v != MANIFEST_VERSION:
            raise ValueError(
                f"unsupported manifest version {v} (only {MANIFEST_VERSION} is accepted)"
            )
        return v

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not _ID_PATTERN.fullmatch(v):
            raise ValueError(f"manifest id {v!r} must match [A-Za-z0-9][A-Za-z0-9_.-]*")
        return v

    @field_validator("libraries")
    @classmethod
    def _library_keys(cls, v: dict[str, LibraryEntry]) -> dict[str, LibraryEntry]:
        for key in v:
            if not _ID_PATTERN.fullmatch(key):
                raise ValueError(
                    f"library key {key!r} must match [A-Za-z0-9][A-Za-z0-9_.-]*"
                )
        return v


# ---------------------------------------------------------------------------
# Symbol selection resolution
# ---------------------------------------------------------------------------


def _validate_literal_symbol(selector: str, context: str) -> str:
    """Validate a literal symbol selector, preserving case."""
    symbol = selector.strip()
    if not symbol:
        raise ConfigurationError(f"{context}: empty symbol selector")
    if any(c in symbol for c in ","):
        raise ConfigurationError(
            f"{context}: literal symbol {selector!r} must not contain commas"
        )
    if any(c in symbol for c in _WILDCARDS):
        raise ConfigurationError(
            f"{context}: literal symbol {selector!r} must not contain "
            f"wildcard characters ({_WILDCARDS}); ticker globbing is not supported"
        )
    if not any(c.isalnum() for c in symbol):
        raise ConfigurationError(
            f"{context}: symbol {selector!r} must contain at least one letter or digit"
        )
    return symbol


def _expand_file_reference(selector: str, base_dir: Path, context: str) -> list[str]:
    """Expand one ``@path`` / ``@glob`` reference into parsed symbols.

    Relative paths resolve against the manifest's directory. Globs are
    nonrecursive (``*``, ``?``, ``[]``; ``**`` is rejected). Matches are
    sorted; at least one regular readable file is required. Symbols inside
    files may not themselves be ``@`` references.
    """
    pattern = selector[1:]
    if "**" in pattern:
        raise ConfigurationError(
            f"{context}: recursive '**' globs are not supported in {selector!r}"
        )
    if not pattern.strip():
        raise ConfigurationError(f"{context}: empty @ reference")

    expanded = Path(pattern)
    search_root = base_dir
    if expanded.is_absolute():
        root = expanded.root
        pattern = str(expanded).removeprefix(root)
        search_root = Path(root)
    try:
        matches = sorted(
            _globmod.glob(pattern, root_dir=str(search_root), recursive=False)
        )
    except (OSError, ValueError) as exc:
        raise ConfigurationError(
            f"{context}: cannot evaluate glob {selector!r}: {exc}"
        ) from exc

    # glob() returns paths relative to *search_root*; resolve them there —
    # not against the process cwd.
    files: list[Path] = []
    for match_str in matches:
        candidate = Path(match_str)
        if not candidate.is_absolute():
            candidate = search_root / candidate
        if candidate.is_file():
            files.append(candidate)
    if not files:
        raise ConfigurationError(
            f"{context}: @ reference {selector!r} matched no readable files "
            f"(searched relative to {search_root})"
        )

    symbols: list[str] = []
    for match in files:
        file_path = Path(match)
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigurationError(
                f"{context}: cannot read symbol file {file_path}: {exc}"
            ) from exc
        from fin3.utils.symbols import parse_symbols

        parsed = parse_symbols(text)
        if not parsed:
            raise ConfigurationError(
                f"{context}: symbol file {file_path} contains no symbols"
            )
        for symbol in parsed:
            if symbol.startswith("@"):
                raise ConfigurationError(
                    f"{context}: nested @ reference {symbol!r} inside "
                    f"{file_path} is not supported"
                )
            symbols.append(_validate_literal_symbol(symbol, f"{context} ({file_path})"))
    return symbols


def _expand_selectors(selectors: list[str], base_dir: Path, context: str) -> list[str]:
    """Expand a selector list into literal symbols (order preserved, pre-dedup)."""
    out: list[str] = []
    for selector in selectors:
        if selector.startswith("@"):
            out.extend(_expand_file_reference(selector, base_dir, context))
        else:
            out.append(_validate_literal_symbol(selector, context))
    return out


def _resolve_symbols(
    spec: SymbolSelectorSpec, base_dir: Path, dataset_id: str
) -> list[str]:
    """Resolve a dataset's selector spec into its final sorted unique symbols."""
    included = _expand_selectors(
        spec.include, base_dir, f"libraries.{dataset_id}.symbols.include"
    )
    excluded = _expand_selectors(
        spec.exclude, base_dir, f"libraries.{dataset_id}.symbols.exclude"
    )
    resolved = sorted(set(included) - set(excluded))
    if not resolved:
        raise ConfigurationError(
            f"libraries.{dataset_id}.symbols: selection resolves to an empty "
            "symbol set after exclusions"
        )
    return resolved


# ---------------------------------------------------------------------------
# Strict document parsing
# ---------------------------------------------------------------------------


class _StrictSafeLoader(yaml.SafeLoader):  # type: ignore[misc]  # pyyaml ships no type stubs
    """``SafeLoader`` that rejects duplicate mapping keys."""


def _construct_mapping(
    loader: _StrictSafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    if not isinstance(node, yaml.MappingNode):
        raise yaml.constructor.ConstructorError(
            None,
            None,
            "expected a mapping node, but found another node",
            node.start_mark,
        )
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            hash(key)
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found unhashable key {key!r}",
                key_node.start_mark,
            ) from exc
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _json_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _read_document(path: Path) -> tuple[object, str]:
    """Read and parse the manifest file. Returns (document, format)."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigurationError(f"Cannot read manifest {path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"Manifest {path} is not valid UTF-8: {exc}") from exc

    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        try:
            doc = yaml.load(text, Loader=_StrictSafeLoader)
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"Invalid YAML in manifest {path}: {exc}") from exc
        return doc, "yaml"
    if suffix == ".json":
        try:
            doc = json.loads(text, object_pairs_hook=_json_pairs_hook)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ConfigurationError(f"Invalid JSON in manifest {path}: {exc}") from exc
        return doc, "json"
    raise ConfigurationError(
        f"Unsupported manifest extension {suffix!r} for {path} "
        "(expected .yaml, .yml, or .json)"
    )


# ---------------------------------------------------------------------------
# Resolution + hashing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedDataset:
    """One fully resolved dataset request."""

    dataset_id: str
    library: str
    asset_type: AssetType
    provider: str
    resolution: Resolution
    calendar: str
    symbols: tuple[str, ...]
    start: datetime
    end: datetime
    declared_end: datetime | None
    end_is_now: bool
    update_policy: UpdatePolicy


@dataclass(frozen=True)
class ResolvedManifest:
    """A loaded, fully resolved manifest ready for inspection or application."""

    id: str
    datasets: tuple[ResolvedDataset, ...]
    declaration_hash: str
    resolved_at: datetime


def _canonical_declaration(
    manifest_id: str, datasets: list[ResolvedDataset]
) -> dict[str, Any]:
    """Build the hash input: normalized fields only, ``now`` kept literal."""
    libraries: dict[str, Any] = {}
    for ds in datasets:
        libraries[ds.dataset_id] = {
            "asset_type": ds.asset_type.value,
            "provider": ds.provider,
            "resolution": ds.resolution.value,
            "calendar": ds.calendar,
            "symbols": sorted(ds.symbols),
            "start": _coerce_timestamp(ds.start, is_end=False),
            "end": "now"
            if ds.end_is_now
            else _coerce_timestamp(ds.declared_end, is_end=True),
            "update_policy": ds.update_policy.value,
        }
    return {"version": MANIFEST_VERSION, "id": manifest_id, "libraries": libraries}


def declaration_hash(manifest_id: str, datasets: list[ResolvedDataset]) -> str:
    """SHA-256 of the normalized JSON declaration (sorted keys, compact)."""
    payload = json.dumps(
        _canonical_declaration(manifest_id, datasets),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_manifest(path: str | Path, *, now: datetime | None = None) -> ResolvedManifest:
    """Load, validate, and fully resolve a manifest declaration.

    Parameters
    ----------
    path : str or Path
        Manifest file (``.yaml``/``.yml``/``.json``). Relative ``@`` symbol
        references resolve against this file's directory, not the process cwd.
    now : datetime or None
        Fixed aware UTC clock for ``end: now`` resolution; a single value is
        captured for the entire run (defaults to the current time).

    Returns
    -------
    ResolvedManifest

    Raises
    ------
    ConfigurationError
        On any structural, semantic, selector, or bound problem. No partially
        loaded manifest is ever returned.
    """
    manifest_path = Path(path).resolve()
    base_dir = manifest_path.parent
    clock = now if now is not None else datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ConfigurationError("manifest clock 'now' must be timezone-aware")
    resolved_at = clock.astimezone(timezone.utc)

    doc, _fmt = _read_document(manifest_path)
    if not isinstance(doc, dict):
        raise ConfigurationError(
            f"Manifest {manifest_path} must contain a mapping object at the root, "
            f"got {type(doc).__name__}"
        )

    try:
        decl = ManifestDeclaration.model_validate(doc)
    except Exception as exc:  # pydantic.ValidationError and any hook ValueError
        raise ConfigurationError(f"Invalid manifest {manifest_path}: {exc}") from exc

    datasets: list[ResolvedDataset] = []
    seen_targets: dict[tuple[str, str], str] = {}
    for dataset_id in sorted(decl.libraries):
        entry = decl.libraries[dataset_id]
        start_ts = _parse_canonical(entry.start)
        end_is_now = entry.end == "now"
        declared_end = None if end_is_now else _parse_canonical(entry.end)
        end_ts = resolved_at if end_is_now else declared_end
        assert end_ts is not None  # narrowed for type checking

        if start_ts >= end_ts:
            raise ConfigurationError(
                f"libraries.{dataset_id}: start ({start_ts.isoformat()}) must be "
                f"before resolved end ({end_ts.isoformat()})"
            )
        if not end_is_now and end_ts > resolved_at:
            raise ConfigurationError(
                f"libraries.{dataset_id}: end ({end_ts.isoformat()}) is in the "
                f"future relative to the resolved clock ({resolved_at.isoformat()})"
            )

        symbols = _resolve_symbols(entry.symbols, base_dir, dataset_id)
        library = library_name(entry.asset_type, entry.resolution, entry.provider)

        for symbol in symbols:
            target = (library, symbol)
            if target in seen_targets:
                raise ConfigurationError(
                    f"libraries.{seen_targets[target]} and libraries.{dataset_id} "
                    f"both resolve to {symbol!r} in physical library {library!r}; "
                    "contradictory policies for one target are not allowed"
                )
            seen_targets[target] = dataset_id

        datasets.append(
            ResolvedDataset(
                dataset_id=dataset_id,
                library=library,
                asset_type=entry.asset_type,
                provider=entry.provider,
                resolution=entry.resolution,
                calendar=entry.calendar or _DEFAULT_CALENDAR[entry.asset_type],
                symbols=tuple(symbols),
                start=start_ts,
                end=end_ts,
                declared_end=declared_end,
                end_is_now=end_is_now,
                update_policy=entry.update_policy,
            )
        )

    return ResolvedManifest(
        id=decl.id,
        datasets=tuple(datasets),
        declaration_hash=declaration_hash(decl.id, datasets),
        resolved_at=resolved_at,
    )

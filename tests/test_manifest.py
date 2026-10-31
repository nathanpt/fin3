"""Tests for the declarative manifest loader (fin3.manifest)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fin3.exceptions import ConfigurationError
from fin3.manifest import UpdatePolicy, load_manifest
from fin3.schemas import AssetType, Resolution

NOW = datetime(2024, 2, 1, 12, 0, 0, tzinfo=timezone.utc)

VALID_YAML = """
version: 1
id: research-bars
libraries:
  crypto-hourly:
    asset_type: crypto
    provider: binance
    resolution: 1h
    calendar: 24x7
    symbols:
      include: [BTC-USD, ETH-USD, '@symbols/watchlist-*.txt']
      exclude: [DOGE-USD]
    start: '2024-01-01T00:00:00Z'
    end: now
    update_policy: incremental
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _watchlist_file(tmp_path: Path) -> None:
    """VALID_YAML references @symbols/watchlist-*.txt; provide it."""
    symbols = tmp_path / "symbols"
    symbols.mkdir(exist_ok=True)
    (symbols / "watchlist-a.txt").write_text("SOL-USD\n", encoding="utf-8")


def _manifest_doc() -> dict:
    """JSON form equivalent to VALID_YAML (same resolved declaration)."""
    return {
        "version": 1,
        "id": "research-bars",
        "libraries": {
            "crypto-hourly": {
                "asset_type": "crypto",
                "provider": "binance",
                "resolution": "1h",
                "calendar": "24x7",
                "symbols": {
                    "include": ["BTC-USD", "ETH-USD", "@symbols/watchlist-*.txt"],
                    "exclude": ["DOGE-USD"],
                },
                "start": "2024-01-01T00:00:00Z",
                "end": "now",
                "update_policy": "incremental",
            }
        },
    }


class TestEquivalence:
    def test_yaml_and_json_produce_identical_resolution(self, tmp_path: Path) -> None:
        yaml_path = _write(tmp_path, "m.yaml", VALID_YAML)
        json_path = _write(tmp_path, "m.json", json.dumps(_manifest_doc()))

        from_yaml = load_manifest(yaml_path, now=NOW)
        from_json = load_manifest(json_path, now=NOW)

        assert from_yaml.id == from_json.id == "research-bars"
        assert from_yaml.declaration_hash == from_json.declaration_hash
        a, b = from_yaml.datasets[0], from_json.datasets[0]
        assert a.library == b.library == "crypto-1h-binance"
        assert a.symbols == b.symbols == ("BTC-USD", "ETH-USD", "SOL-USD")
        assert a.start == b.start
        assert a.end == b.end
        assert a.update_policy is UpdatePolicy.INCREMENTAL

    def test_date_only_end_resolves_to_end_of_day(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "m.yaml",
            """
version: 1
id: m
libraries:
  ds:
    asset_type: crypto
    provider: p
    resolution: 1h
    symbols: [BTC-USD]
    start: '2024-01-01'
    end: '2024-01-02'
""",
        )
        ds = load_manifest(path, now=NOW).datasets[0]
        assert ds.start == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert ds.end == datetime(2024, 1, 2, 23, 59, 59, 999999, tzinfo=timezone.utc)
        assert ds.declared_end == ds.end
        assert ds.end_is_now is False

    def test_offset_timestamp_is_normalized_to_utc(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "m.yaml",
            """
version: 1
id: m
libraries:
  ds:
    asset_type: crypto
    provider: p
    resolution: 1h
    symbols: [BTC-USD]
    start: '2024-01-01T05:30:00+05:00'
    end: '2024-01-02T02:00:00+05:00'
""",
        )
        ds = load_manifest(path, now=NOW).datasets[0]
        assert ds.start == datetime(2024, 1, 1, 0, 30, tzinfo=timezone.utc)
        assert ds.end == datetime(2024, 1, 1, 21, 0, 0, tzinfo=timezone.utc)


class TestStrictness:
    def test_duplicate_yaml_keys_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "m.yaml",
            "version: 1\nid: m\nid: m2\n"
            "libraries: {a: {asset_type: crypto, provider: p, resolution: 1h, "
            "symbols: [BTC-USD], start: 2024-01-01, end: 2024-01-02}}\n",
        )
        with pytest.raises(ConfigurationError, match="duplicate key"):
            load_manifest(path, now=NOW)

    def test_duplicate_json_keys_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "m.json",
            '{"version": 1, "id": "m", "id": "m2", "libraries": null}',
        )
        with pytest.raises(ConfigurationError, match="duplicate object key"):
            load_manifest(path, now=NOW)

    @pytest.mark.parametrize("version", ["1", 1.0, True, 2, None])
    def test_version_coercions_rejected(self, tmp_path: Path, version: object) -> None:
        doc = _manifest_doc()
        doc["version"] = version  # type: ignore[assignment]
        path = _write(tmp_path, "m.json", json.dumps(doc))
        with pytest.raises(ConfigurationError, match="version"):
            load_manifest(path, now=NOW)

    def test_unknown_root_field_rejected(self, tmp_path: Path) -> None:
        doc = _manifest_doc()
        doc["fallbacks"] = ["binance"]  # provider routing stays feat-016
        path = _write(tmp_path, "m.json", json.dumps(doc))
        with pytest.raises(ConfigurationError, match="fallbacks"):
            load_manifest(path, now=NOW)

    def test_unknown_entry_field_rejected(self, tmp_path: Path) -> None:
        doc = _manifest_doc()
        doc["libraries"]["crypto-hourly"]["secret_key"] = "nope"
        path = _write(tmp_path, "m.json", json.dumps(doc))
        with pytest.raises(ConfigurationError, match="secret_key"):
            load_manifest(path, now=NOW)

    def test_empty_libraries_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "m.json",
            '{"version": 1, "id": "broken", "libraries": {}}',
        )
        with pytest.raises(ConfigurationError, match="libraries"):
            load_manifest(path, now=NOW)

    def test_bad_id_rejected(self, tmp_path: Path) -> None:
        doc = _manifest_doc()
        doc["id"] = "-leading-dash"
        path = _write(tmp_path, "m.json", json.dumps(doc))
        with pytest.raises(ConfigurationError, match="id"):
            load_manifest(path, now=NOW)

    def test_naive_timestamp_rejected(self, tmp_path: Path) -> None:
        doc = _manifest_doc()
        doc["libraries"]["crypto-hourly"]["start"] = "2024-01-01T00:00:00"
        path = _write(tmp_path, "m.json", json.dumps(doc))
        with pytest.raises(ConfigurationError, match="naive"):
            load_manifest(path, now=NOW)

    def test_future_explicit_end_rejected(self, tmp_path: Path) -> None:
        doc = _manifest_doc()
        doc["libraries"]["crypto-hourly"]["end"] = "2999-01-01T00:00:00Z"
        path = _write(tmp_path, "m.json", json.dumps(doc))
        with pytest.raises(ConfigurationError, match="future"):
            load_manifest(path, now=NOW)

    def test_start_after_end_rejected(self, tmp_path: Path) -> None:
        doc = _manifest_doc()
        doc["libraries"]["crypto-hourly"]["start"] = "2024-02-02T00:00:00Z"
        path = _write(tmp_path, "m.json", json.dumps(doc))
        with pytest.raises(ConfigurationError, match="must be"):
            load_manifest(path, now=NOW)

    def test_unsupported_extension_rejected(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "m.toml", "version = 1")
        with pytest.raises(ConfigurationError, match="extension"):
            load_manifest(path, now=NOW)

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="Cannot read"):
            load_manifest(tmp_path / "absent.yaml", now=NOW)

    def test_invalid_utf8_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "m.yaml"
        path.write_bytes(b"version: 1\nid: \xff\xfe\n")
        with pytest.raises(ConfigurationError, match="UTF-8"):
            load_manifest(path, now=NOW)

    def test_custom_yaml_tag_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path, "m.yaml", "version: !!python/object/apply:os.system ['x']\n"
        )
        with pytest.raises(ConfigurationError, match="YAML"):
            load_manifest(path, now=NOW)

    def test_non_object_root_rejected(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "m.json", "[1, 2, 3]")
        with pytest.raises(ConfigurationError, match="mapping"):
            load_manifest(path, now=NOW)

    def test_calendar_mismatch_rejected(self, tmp_path: Path) -> None:
        doc = _manifest_doc()
        doc["libraries"]["crypto-hourly"]["calendar"] = "XNYS"
        path = _write(tmp_path, "m.json", json.dumps(doc))
        with pytest.raises(ConfigurationError, match="calendar"):
            load_manifest(path, now=NOW)

    def test_calendar_defaults_derived_from_asset_type(self, tmp_path: Path) -> None:
        doc = _manifest_doc()
        del doc["libraries"]["crypto-hourly"]["calendar"]
        doc["libraries"]["crypto-hourly"]["symbols"] = ["BTC-USD"]
        path = _write(tmp_path, "m.json", json.dumps(doc))
        ds = load_manifest(path, now=NOW).datasets[0]
        assert ds.calendar == "24x7"

    def test_collider_targets_across_datasets_rejected(self, tmp_path: Path) -> None:
        entry = {
            "asset_type": "crypto",
            "provider": "binance",
            "resolution": "1h",
            "symbols": ["BTC-USD"],
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-02T00:00:00Z",
        }
        other = dict(entry, start="2024-01-03T00:00:00Z", end="2024-01-04T00:00:00Z")
        doc = {
            "version": 1,
            "id": "m",
            "libraries": {"a": entry, "b": other},
        }
        path = _write(tmp_path, "m.json", json.dumps(doc))
        with pytest.raises(ConfigurationError, match="both resolve"):
            load_manifest(path, now=NOW)


class TestSelectors:
    def test_file_reference_resolves_relative_to_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "symbols").mkdir(exist_ok=True)
        (tmp_path / "symbols" / "list.txt").write_text(
            "# exchange list\nBRK B\nES.n.0\n\nBTC-USD, ETH-USD\n",
            encoding="utf-8",
        )
        path = _write(
            tmp_path,
            "m.yaml",
            """
version: 1
id: m
libraries:
  ds:
    asset_type: crypto
    provider: p
    resolution: 1h
    symbols: ['@symbols/list.txt']
    start: '2024-01-01'
    end: '2024-01-02'
""",
        )
        # Run from a different cwd to prove resolution is manifest-relative.
        import os

        cwd = os.getcwd()
        os.chdir("/")
        try:
            ds = load_manifest(path, now=NOW).datasets[0]
        finally:
            os.chdir(cwd)
        assert ds.symbols == ("BRK B", "BTC-USD", "ES.n.0", "ETH-USD")

    def test_include_exclude_dedup_and_subtraction(self, tmp_path: Path) -> None:
        (tmp_path / "symbols").mkdir(exist_ok=True)
        (tmp_path / "symbols" / "more.txt").write_text(
            "SOL-USD\nADA-USD\n", encoding="utf-8"
        )
        path = _write(
            tmp_path,
            "m.yaml",
            """
version: 1
id: m
libraries:
  ds:
    asset_type: crypto
    provider: p
    resolution: 1h
    symbols:
      include: [BTC-USD, ETH-USD, BTC-USD, '@symbols/more.txt']
      exclude: [ada-usd]
    start: '2024-01-01'
    end: '2024-01-02'
""",
        )
        ds = load_manifest(path, now=NOW).datasets[0]
        # Case-sensitive exclusion: ada-usd does not remove ADA-USD.
        assert ds.symbols == ("ADA-USD", "BTC-USD", "ETH-USD", "SOL-USD")

    def test_glob_sorted_and_multi_file(self, tmp_path: Path) -> None:
        (tmp_path / "symbols").mkdir(exist_ok=True)
        (tmp_path / "symbols" / "b.txt").write_text("ETH-USD\n", encoding="utf-8")
        (tmp_path / "symbols" / "a.txt").write_text("BTC-USD\n", encoding="utf-8")
        path = _write(
            tmp_path,
            "m.yaml",
            """
version: 1
id: m
libraries:
  ds:
    asset_type: crypto
    provider: p
    resolution: 1h
    symbols: ['@symbols/[ab].txt']
    start: '2024-01-01'
    end: '2024-01-02'
""",
        )
        ds = load_manifest(path, now=NOW).datasets[0]
        assert ds.symbols == ("BTC-USD", "ETH-USD")

    def test_unmatched_glob_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "m.yaml",
            """
version: 1
id: m
libraries:
  ds:
    asset_type: crypto
    provider: p
    resolution: 1h
    symbols: ['@symbols/nothing-*.txt']
    start: '2024-01-01'
    end: '2024-01-02'
""",
        )
        with pytest.raises(ConfigurationError, match="matched no readable files"):
            load_manifest(path, now=NOW)

    def test_recursive_glob_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "m.yaml",
            """
version: 1
id: m
libraries:
  ds:
    asset_type: crypto
    provider: p
    resolution: 1h
    symbols: ['@symbols/**/x.txt']
    start: '2024-01-01'
    end: '2024-01-02'
""",
        )
        with pytest.raises(ConfigurationError, match=r"\*\*"):
            load_manifest(path, now=NOW)

    def test_nested_reference_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "symbols").mkdir(exist_ok=True)
        (tmp_path / "symbols" / "outer.txt").write_text(
            "@symbols/inner.txt\n", encoding="utf-8"
        )
        path = _write(
            tmp_path,
            "m.yaml",
            """
version: 1
id: m
libraries:
  ds:
    asset_type: crypto
    provider: p
    resolution: 1h
    symbols: ['@symbols/outer.txt']
    start: '2024-01-01'
    end: '2024-01-02'
""",
        )
        with pytest.raises(ConfigurationError, match="nested @ reference"):
            load_manifest(path, now=NOW)

    def test_literal_with_wildcard_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "m.yaml",
            """
version: 1
id: m
libraries:
  ds:
    asset_type: crypto
    provider: p
    resolution: 1h
    symbols: ['BTC*']
    start: '2024-01-01'
    end: '2024-01-02'
""",
        )
        with pytest.raises(ConfigurationError, match="wildcard"):
            load_manifest(path, now=NOW)

    def test_literal_with_comma_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "m.yaml",
            """
version: 1
id: m
libraries:
  ds:
    asset_type: crypto
    provider: p
    resolution: 1h
    symbols: ['BRK,B']
    start: '2024-01-01'
    end: '2024-01-02'
""",
        )
        with pytest.raises(ConfigurationError, match="comma"):
            load_manifest(path, now=NOW)

    def test_excluding_everything_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "m.yaml",
            """
version: 1
id: m
libraries:
  ds:
    asset_type: crypto
    provider: p
    resolution: 1h
    symbols: {include: [BTC-USD], exclude: [BTC-USD]}
    start: '2024-01-01'
    end: '2024-01-02'
""",
        )
        with pytest.raises(ConfigurationError, match="empty"):
            load_manifest(path, now=NOW)


class TestOneClock:
    def test_now_resolves_to_single_captured_clock(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "m.yaml",
            """
version: 1
id: m
libraries:
  ds:
    asset_type: crypto
    provider: p
    resolution: 1h
    symbols: [BTC-USD]
    start: '2024-01-01'
    end: now
""",
        )
        resolved = load_manifest(path, now=NOW)
        ds = resolved.datasets[0]
        assert ds.end_is_now is True
        assert ds.declared_end is None
        assert ds.end == resolved.resolved_at == NOW

    def test_fixed_end_hash_stable_when_clock_advances(self, tmp_path: Path) -> None:
        doc = _manifest_doc()
        path = _write(tmp_path, "m.json", json.dumps(doc))
        early = load_manifest(path, now=datetime(2024, 2, 1, tzinfo=timezone.utc))
        late = load_manifest(path, now=datetime(2025, 6, 1, tzinfo=timezone.utc))
        assert early.declaration_hash == late.declaration_hash

    def test_now_hash_stable_when_clock_advances(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "m.yaml", VALID_YAML)
        early = load_manifest(path, now=datetime(2024, 2, 1, tzinfo=timezone.utc))
        late = load_manifest(path, now=datetime(2025, 6, 1, tzinfo=timezone.utc))
        assert early.declaration_hash == late.declaration_hash
        assert early.datasets[0].end < late.datasets[0].end


class TestHashBehavior:
    def test_formatting_only_edits_keep_hash(self, tmp_path: Path) -> None:
        first = _write(tmp_path, "a.yaml", VALID_YAML)
        reformatted = VALID_YAML.replace(
            "  crypto-hourly:", "  crypto-hourly:   # hourly crypto bars"
        ).replace("'2024-01-01T00:00:00Z'", "2024-01-01")
        second = _write(tmp_path, "b.yaml", reformatted)

        h1 = load_manifest(first, now=NOW).declaration_hash
        h2 = load_manifest(second, now=NOW).declaration_hash
        assert h1 == h2

    def test_resolved_symbol_change_changes_hash(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "m.yaml", VALID_YAML)
        before = load_manifest(path, now=NOW).declaration_hash
        (tmp_path / "symbols" / "watchlist-a.txt").write_text(
            "SOL-USD\nXRP-USD\n", encoding="utf-8"
        )
        after = load_manifest(path, now=NOW).declaration_hash
        assert before != after

    def test_symbol_order_does_not_change_hash(self, tmp_path: Path) -> None:
        doc = _manifest_doc()
        doc["libraries"]["crypto-hourly"]["symbols"] = {
            "include": ["BTC-USD", "ETH-USD"]
        }
        path1 = _write(tmp_path, "a.json", json.dumps(doc))
        reordered = json.loads(json.dumps(doc))
        reordered["libraries"]["crypto-hourly"]["symbols"] = {
            "include": ["ETH-USD", "BTC-USD"]
        }
        path2 = _write(tmp_path, "b.json", json.dumps(reordered))
        assert (
            load_manifest(path1, now=NOW).declaration_hash
            == load_manifest(path2, now=NOW).declaration_hash
        )

    def test_policy_change_changes_hash(self, tmp_path: Path) -> None:
        doc = _manifest_doc()
        first = _write(tmp_path, "a.json", json.dumps(doc))
        doc["libraries"]["crypto-hourly"]["update_policy"] = "full-refresh"
        second = _write(tmp_path, "b.json", json.dumps(doc))
        assert (
            load_manifest(first, now=NOW).declaration_hash
            != load_manifest(second, now=NOW).declaration_hash
        )


def test_resolved_fields_expose_canonical_identities(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "m.yaml",
        """
version: 1
id: m
libraries:
  eq:
    asset_type: equity_us
    provider: databento
    resolution: 1m
    symbols: ['BRK B', 'ES.n.0']
    start: '2024-01-01'
    end: '2024-01-05'
""",
    )
    manifest = load_manifest(path, now=NOW)
    ds = manifest.datasets[0]
    assert ds.library == "equities-1m-databento"
    assert ds.asset_type is AssetType.EQUITY_US
    assert ds.resolution is Resolution.ONE_MINUTE
    assert ds.calendar == "XNYS"
    assert ds.symbols == ("BRK B", "ES.n.0")  # case preserved

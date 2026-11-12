"""Offline regressions for the EODHD readback trust boundary.

The integration readback suite must fail closed at its explicitly named
storage target. These tests protect that behavior in the default offline
run: an empty or stale target is rejected (never skipped, never falling
back to another store), target summaries never carry credentials, and the
finlab child-result validator hard-fails — including on a real nonzero
child exit — without disclosing child stderr.

The helpers under test are private to the integration suite by design;
importing them here must not import that suite's dotenv-loading conftest
(`tests/integration/__init__.py` is empty and the helpers are module-level).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from fin3.config.settings import ClientConfig, MinioConfig
from fin3.options import OptionsDataFetcher
from fin3.options_schemas import (
    CHAIN_REPORT_ATTR,
    ChainCoverageManifest,
    ChainFetchReport,
)
from fin3.storage.arctic import ArcticStorage
from tests.integration.test_provider_eodhd_options import (
    SHY_DAY,
    SHY_START,
    _readback_target,
    _require_finlab_readback,
    _require_readback_artifact,
)
from tests.test_options import make_frame

_WINDOW_ROWS = [
    ("SHY250117C00091000", SHY_START.isoformat(), {}),
    ("SHY270617P00092000", SHY_DAY.isoformat(), {}),
    ("SHY280120C00092000", SHY_DAY.isoformat(), {}),
]


@pytest.fixture
def unit_config() -> ClientConfig:
    """Keyless fetcher config with no providers and no env resolution."""
    return ClientConfig(
        minio=MinioConfig(endpoint="localhost:9000", access_key="a", secret_key="b"),
        providers={},
        EODHD_API_KEY=None,
        _env_file=None,
    )


def _store_shy_snapshot(
    storage: ArcticStorage, config: ClientConfig
) -> tuple[pd.DataFrame, ChainCoverageManifest]:
    """Write a real SHY window snapshot through the production merge path."""
    frame = make_frame(_WINDOW_ROWS, root="SHY", start=SHY_START, end=SHY_DAY)
    report = ChainFetchReport.model_validate(frame.attrs[CHAIN_REPORT_ATTR])
    fetcher = OptionsDataFetcher(config, storage=storage)
    merged, days = fetcher._merge(None, frame, report, SHY_START, SHY_DAY)
    manifest = fetcher._build_manifest("SHY", merged, days, report)
    storage.write(
        "options-eod-eodhd", "SHY", merged, metadata=manifest.model_dump(mode="json")
    )
    return merged, manifest


def _mutated_metadata(
    manifest: ChainCoverageManifest, case: str
) -> dict[str, Any] | None:
    """Return the metadata variant one stale/mismatched case writes."""
    dump = manifest.model_dump(mode="json")
    if case == "missing_manifest":
        return None
    if case == "invalid_manifest":
        return {"junk": 1}
    if case == "wrong_root":
        dump["root"] = "SPY"
    elif case == "wrong_endpoints":
        for day in dump["days"]:
            shifted = date.fromisoformat(day["as_of"]) + timedelta(days=1)
            day["as_of"] = shifted.isoformat()
    elif case == "wrong_length":
        # Interior removal: endpoints stay exact so the length check fires.
        dump["days"] = dump["days"][:500] + dump["days"][501:]
    elif case == "wrong_requests":
        dump["requests_used"] = dump["requests_used"] + 1
    return dump


class TestReadbackArtifactGate:
    """The entry gate rejects unavailable or mismatched named targets."""

    def test_empty_target_fails_without_fallback(
        self, storage: ArcticStorage, unit_config: ClientConfig, tmp_path: Path
    ) -> None:
        """A valid snapshot in another LMDB store must not rescue an empty target."""
        other = ArcticStorage.from_lmdb(str(tmp_path / "other_lmdb"))
        merged, manifest = _store_shy_snapshot(other, unit_config)
        accepted = _require_readback_artifact(
            other,
            expected_days=len(manifest.days),
            expected_requests=manifest.requests_used,
        )
        assert accepted.days[0].as_of == SHY_START
        assert accepted.days[-1].as_of == SHY_DAY

        with pytest.raises(pytest.fail.Exception) as excinfo:
            _require_readback_artifact(
                storage,
                expected_days=len(manifest.days),
                expected_requests=manifest.requests_used,
            )
        message = str(excinfo.value)
        assert "holds no options-eod-eodhd/SHY snapshot" in message
        # The failure names the logical library/symbol and the physical
        # backend of the empty target.
        assert "backend=lmdb" in message
        assert "library=options-eod-eodhd symbol=SHY" in message
        # The failed read must not have created anything on the empty target.
        assert storage.has_library("options-eod-eodhd") is False
        assert other.has_library("options-eod-eodhd") is True

    @pytest.mark.parametrize(
        "case",
        [
            "missing_manifest",
            "invalid_manifest",
            "wrong_root",
            "wrong_endpoints",
            "wrong_length",
            "wrong_requests",
        ],
    )
    def test_stale_or_wrong_metadata_hard_fails(
        self, storage: ArcticStorage, unit_config: ClientConfig, case: str
    ) -> None:
        """Stale/wrong metadata is a hard gate failure naming the target."""
        merged, manifest = _store_shy_snapshot(storage, unit_config)
        storage.write(
            "options-eod-eodhd",
            "SHY",
            merged,
            metadata=_mutated_metadata(manifest, case),
        )
        with pytest.raises(pytest.fail.Exception) as excinfo:
            _require_readback_artifact(
                storage,
                expected_days=len(manifest.days),
                expected_requests=manifest.requests_used,
            )
        message = str(excinfo.value)
        assert message.startswith("EODHD readback artifact check failed")
        assert "backend=lmdb endpoint=lmdb" in message
        # A metadata failure is a failure outcome, never a skip outcome.
        assert not isinstance(excinfo.value, pytest.skip.Exception)


class TestReadbackTargetSummary:
    """Target summaries name physical resolution without credentials."""

    def test_lmdb_target_names_physical_resolution(
        self, storage: ArcticStorage
    ) -> None:
        summary = _readback_target(storage)
        assert "backend=lmdb" in summary
        assert "bucket=__lmdb__" in summary
        assert "arctic_library=options-eod-eodhd" in summary
        assert "library=options-eod-eodhd symbol=SHY" in summary

    def test_s3_target_names_endpoint_and_never_credentials(self) -> None:
        storage = ArcticStorage(
            MinioConfig(
                endpoint="minio.lan:9000",
                access_key="AKIAIOSFODNN7EXAMPLE",
                secret_key="wJalrXUtnFEMI-example-secret",
                secure=True,
                bucket="owned-bucket",
            )
        )
        summary = _readback_target(storage)
        assert "backend=s3s" in summary
        assert "endpoint=minio.lan:9000" in summary
        assert "secure=true" in summary
        assert "bucket=owned-bucket" in summary
        assert "access=" not in summary
        assert "AKIAIOSFODNN7EXAMPLE" not in summary
        assert "wJalrXUtnFEMI-example-secret" not in summary


class TestFinlabReadbackValidator:
    """The finlab child-result validator fails closed on boundary breaches."""

    @staticmethod
    def _baseline(bucket: str = "owned-bucket") -> dict[str, Any]:
        return {
            "status": "observed",
            "reason": None,
            "rows": 416,
            "digest": "a" * 64,
            "days": 1044,
            "endpoint": "minio.lan:9000",
            "bucket": bucket,
            "secure": False,
            "library": "options-eod-eodhd",
            "symbol": "SHY",
            "resolution": "owned-bucket-override",
        }

    @staticmethod
    def _completed(
        child: dict[str, Any],
        *,
        returncode: int = 0,
        stdout: str | None = None,
        stderr: str = "",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["python", "-c", "child"],
            returncode=returncode,
            stdout=json.dumps(child) + "\n" if stdout is None else stdout,
            stderr=stderr,
        )

    def test_valid_owned_bucket_record_is_accepted(self) -> None:
        """The fully valid baseline passes every required field check."""
        child = _require_finlab_readback(
            self._completed(self._baseline()),
            bucket="owned-bucket",
            expected_digest="a" * 64,
            expected_days=1044,
        )
        assert child["bucket"] == "owned-bucket"
        assert child["resolution"] == "owned-bucket-override"

    def test_real_nonzero_exit_hard_fails_without_disclosing_stderr(self) -> None:
        """A real exit-7 child produces a failure, not a skip or a leak."""
        sentinel = "FIN3_MINIO__SECRET_KEY=sentinel-hunter2-do-not-echo"
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.stderr.write({sentinel!r} + chr(10)); sys.exit(7)",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 7
        assert sentinel in proc.stderr  # the child really emitted the secret
        with pytest.raises(pytest.fail.Exception) as excinfo:
            _require_finlab_readback(
                proc,
                bucket="owned-bucket",
                expected_digest="a" * 64,
                expected_days=1044,
            )
        message = str(excinfo.value)
        assert "(exit code)" in message
        assert "owned-bucket" in message
        assert sentinel not in message
        assert not isinstance(excinfo.value, pytest.skip.Exception)

    @pytest.mark.parametrize(
        ("field", "value", "category"),
        [
            pytest.param("status", "no_data", "(no data)", id="no_data"),
            pytest.param("rows", 0, "(no data)", id="observed_zero_rows"),
            pytest.param(
                "bucket",
                "consumer-default-bucket",
                "(target mismatch)",
                id="wrong_bucket",
            ),
            pytest.param(
                "digest", "b" * 64, "(comparison mismatch)", id="digest_mismatch"
            ),
            pytest.param(
                "days", 1043, "(comparison mismatch)", id="day_count_mismatch"
            ),
            pytest.param("days", True, "(invalid payload)", id="boolean_days"),
        ],
    )
    def test_child_boundary_rejection(
        self, field: str, value: Any, category: str
    ) -> None:
        """One mutation per independent rejection; the rest stays valid."""
        child = self._baseline()
        child[field] = value
        if field == "status":
            child["rows"] = 0
            child["reason"] = "root_not_ingested"
        with pytest.raises(pytest.fail.Exception) as excinfo:
            _require_finlab_readback(
                self._completed(child),
                bucket="owned-bucket",
                expected_digest="a" * 64,
                expected_days=1044,
            )
        message = str(excinfo.value)
        assert category in message
        assert "owned-bucket" in message
        if category != "(invalid payload)":
            # Unknown payload fields are never quoted as attribution; valid
            # categories always carry the child's resolution literal.
            assert "resolution=owned-bucket-override" in message

    @pytest.mark.parametrize(
        "stdout",
        [
            pytest.param("", id="empty_stdout"),
            pytest.param("progress without json\n", id="non_json"),
            pytest.param('{"status": "observed"', id="truncated_json"),
            pytest.param('["not", "an", "object"]\n', id="non_object_json"),
            pytest.param('{ "ok": true }\n', id="object_without_fields"),
        ],
    )
    def test_malformed_child_output_hard_fails(self, stdout: str) -> None:
        """Zero exit with unusable payload output is a hard failure."""
        proc = subprocess.CompletedProcess(
            ["python", "-c", "child"], returncode=0, stdout=stdout, stderr=""
        )
        with pytest.raises(pytest.fail.Exception) as excinfo:
            _require_finlab_readback(
                proc,
                bucket="owned-bucket",
                expected_digest="a" * 64,
                expected_days=1044,
            )
        assert "(invalid payload)" in str(excinfo.value)

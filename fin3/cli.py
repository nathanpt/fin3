"""``fin3`` command line: ensure / sync / status for declarative manifests.

Construction order is fixed: parse arguments → load/validate manifest →
``_load_config()`` → ``configure_logging`` → ``_build_runner()`` → perform
operation. Manifest validation deliberately precedes environment
configuration so an invalid manifest is diagnosed even outside a configured
checkout. ``--json`` emits exactly one JSON value on stdout; all diagnostics
routed through ``logging`` go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from fin3.config.settings import ClientConfig
from fin3.exceptions import ConfigurationError, CostLimitExceededError, Fin3Error
from fin3.manifest import load_manifest
from fin3.storage.locking import LockAcquisitionError
from fin3.sync import ManifestReport, ManifestRunner, SymbolResult
from fin3.utils.logging import configure_logging


def _load_config() -> ClientConfig:
    """Production configuration seam (tests monkeypatch this).

    ``ClientConfig`` validates ``FIN3_*`` environment variables at
    construction; a missing ``FIN3_MINIO__*`` set raises
    ``pydantic.ValidationError``, which ``main`` renders as a
    configuration error.
    """
    return ClientConfig()  # type: ignore[call-arg]  # required fields come from env


def _build_runner(config: ClientConfig) -> ManifestRunner:
    """Production runner seam (tests monkeypatch this)."""
    return ManifestRunner(config)


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


def _collect_secrets(config: ClientConfig | None) -> list[str]:
    """Return configured secret values (API keys, MinIO credentials)."""
    if config is None:
        return []
    secrets: list[str] = []
    models = [config.minio, *config.providers.values()]
    for model in models:
        for name in getattr(type(model), "model_fields", {}):
            if "key" not in name and "secret" not in name:
                continue
            value = getattr(model, name, None)
            if isinstance(value, str) and value:
                secrets.append(value)
    return sorted(set(secrets), key=len, reverse=True)


def _redact(message: str, config: ClientConfig | None) -> str:
    for secret in _collect_secrets(config):
        if secret in message:
            message = message.replace(secret, "***")
    return message


def _redact_report(
    report: ManifestReport, config: ClientConfig | None
) -> ManifestReport:
    """Return a copy of *report* with secret-redacted error strings."""
    results: list[SymbolResult] = []
    for r in report.results:
        if r.error is None:
            results.append(r)
            continue
        results.append(
            SymbolResult(
                dataset_id=r.dataset_id,
                library=r.library,
                symbol=r.symbol,
                policy=r.policy,
                action=r.action,
                status=r.status,
                reason=r.reason,
                fetch_ranges=list(r.fetch_ranges),
                write_ranges=list(r.write_ranges),
                rows_written=r.rows_written,
                write_state_unknown=r.write_state_unknown,
                error=_redact(r.error, config),
            )
        )
    return ManifestReport(
        manifest_id=report.manifest_id,
        declaration_hash=report.declaration_hash,
        last_successful_hash=report.last_successful_hash,
        definition_state=report.definition_state,
        operation=report.operation,
        dry_run=report.dry_run,
        resolved_at=report.resolved_at,
        planned_libraries=report.planned_libraries,
        created_libraries=report.created_libraries,
        estimated_cost=report.estimated_cost,
        results=results,
        errors=[_redact(e, config) for e in report.errors],
        state_write_unknown=report.state_write_unknown,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _result_to_dict(result: SymbolResult) -> dict[str, Any]:
    return {
        "dataset_id": result.dataset_id,
        "library": result.library,
        "symbol": result.symbol,
        "policy": result.policy,
        "action": result.action,
        "status": result.status,
        "reason": result.reason,
        "fetch_ranges": [[_iso(a), _iso(b)] for a, b in result.fetch_ranges],
        "write_ranges": [[_iso(a), _iso(b)] for a, b in result.write_ranges],
        "rows_written": result.rows_written,
        "write_state_unknown": result.write_state_unknown,
        "error": result.error,
    }


def _report_to_dict(report: ManifestReport) -> dict[str, Any]:
    return {
        "manifest_id": report.manifest_id,
        "declaration_hash": report.declaration_hash,
        "last_successful_hash": report.last_successful_hash,
        "definition_state": report.definition_state,
        "operation": report.operation,
        "dry_run": report.dry_run,
        "resolved_at": _iso(report.resolved_at),
        "planned_libraries": report.planned_libraries,
        "created_libraries": report.created_libraries,
        "estimated_cost": report.estimated_cost,
        "totals": {
            "created": report.created_count,
            "updated": report.updated_count,
            "skipped": report.skipped_count,
            "failed": report.failed_count,
        },
        "results": [_result_to_dict(r) for r in report.results],
        "errors": list(report.errors),
        "state_write_unknown": report.state_write_unknown,
    }


def _format_report(report: ManifestReport) -> str:
    lines = [
        f"Manifest {report.manifest_id}: {report.operation}"
        + (" (dry-run)" if report.dry_run else ""),
        f"  definition: {report.definition_state}"
        + (
            f" (last successful hash: {report.last_successful_hash})"
            if report.last_successful_hash
            else ""
        ),
        f"  resolved_at: {_iso(report.resolved_at)}",
    ]
    if report.planned_libraries:
        lines.append(
            f"  planned library creations: {', '.join(report.planned_libraries)}"
        )
    if report.created_libraries:
        lines.append(f"  created libraries: {', '.join(report.created_libraries)}")
    if report.estimated_cost is not None:
        lines.append(f"  estimated cost: ${report.estimated_cost:.2f}")
    lines.append(
        f"  totals: {report.created_count} created, {report.updated_count} updated, "
        f"{report.skipped_count} skipped, {report.failed_count} failed"
    )

    if report.results:
        lines.append("  targets:")
        for r in report.results:
            line = (
                f"    {r.dataset_id}/{r.symbol} [{r.library}] "
                f"action={r.action} status={r.status}"
            )
            if r.reason:
                line += f" reason={r.reason}"
            if r.rows_written:
                line += f" rows={r.rows_written}"
            if r.fetch_ranges:
                ranges = ", ".join(
                    f"[{a.isoformat()}, {b.isoformat()}]" for a, b in r.fetch_ranges
                )
                line += f" fetch={ranges}"
            if r.write_ranges:
                ranges = ", ".join(
                    f"[{a.isoformat()}, {b.isoformat()}]" for a, b in r.write_ranges
                )
                line += f" write={ranges}"
            lines.append(line)
            if r.error is not None:
                lines.append(f"      error: {r.error}")
    for err in report.errors:
        lines.append(f"  error: {err}")
    if report.state_write_unknown:
        lines.append("  warning: manifest state write outcome unknown")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fin3",
        description="Declarative dataset manifests: ensure, sync, and status",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("ensure", "create missing libraries and symbols declared by the manifest"),
        ("sync", "reconcile stored data with the manifest per update policy"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("manifest", help="Path to a manifest .yaml/.yml/.json file")
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="Report planned actions without writing anything",
        )
        p.add_argument(
            "--json",
            action="store_true",
            help="Emit the report as a single JSON value on stdout",
        )
        p.add_argument(
            "--max-cost",
            type=float,
            default=None,
            metavar="USD",
            help="Abort before any write when the estimated download cost exceeds this",
        )

    status = sub.add_parser(
        "status", help="read-only preview of the actions sync would take"
    )
    status.add_argument("manifest", help="Path to a manifest .yaml/.yml/.json file")
    status.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as a single JSON value on stdout",
    )
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    config: ClientConfig | None = None
    try:
        manifest = load_manifest(args.manifest)
        config = _load_config()
        configure_logging(level=config.log_level, format_=config.log_format)
        runner = _build_runner(config)
        if args.command == "status":
            report = runner.status(manifest)
        elif args.command == "ensure":
            report = runner.ensure(
                manifest, dry_run=args.dry_run, max_cost=args.max_cost
            )
        else:
            report = runner.sync(manifest, dry_run=args.dry_run, max_cost=args.max_cost)
    except ConfigurationError as exc:
        payload = {
            "error": {
                "kind": "configuration",
                "message": _redact(str(exc), config),
            }
        }
        if getattr(args, "json", False):
            print(json.dumps(payload))
        else:
            print(
                f"configuration error: {payload['error']['message']}",
                file=sys.stderr,
            )
        return 2
    except ValidationError as exc:
        payload = {
            "error": {
                "kind": "configuration",
                "message": _redact(f"invalid configuration: {exc}", None),
            }
        }
        if getattr(args, "json", False):
            print(json.dumps(payload))
        else:
            print(
                f"configuration error: {payload['error']['message']}",
                file=sys.stderr,
            )
        return 2
    except (CostLimitExceededError, Fin3Error, LockAcquisitionError) as exc:
        payload = {
            "error": {
                "kind": "operational",
                "message": _redact(str(exc), config),
            }
        }
        if getattr(args, "json", False):
            print(json.dumps(payload))
        else:
            print(
                f"operational error: {payload['error']['message']}",
                file=sys.stderr,
            )
        return 1

    report = _redact_report(report, config)
    if getattr(args, "json", False):
        print(json.dumps(_report_to_dict(report)))
    else:
        print(_format_report(report))

    if report.failed_count > 0 or report.state_write_unknown or report.errors:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

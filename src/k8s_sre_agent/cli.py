"""Command line interface."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from k8s_sre_agent import __version__
from k8s_sre_agent.llm import PROVIDERS, LLMError, LLMSettings, build_provider
from k8s_sre_agent.models import ClusterSnapshot, Severity
from k8s_sre_agent.rca import diagnose
from k8s_sre_agent.report import to_json, to_markdown

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_FINDINGS = 2

FAIL_ON = {"never": None, "warning": Severity.WARNING, "critical": Severity.CRITICAL}


def _add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-n", "--namespace", default="default", help="namespace to inspect")
    parser.add_argument("-l", "--selector", help="label selector to narrow pods and deployments")
    parser.add_argument("--context", help="kubeconfig context (default: current context)")
    parser.add_argument(
        "--no-logs", action="store_true", help="do not read container logs (never sent anywhere)"
    )
    parser.add_argument(
        "--log-lines", type=int, default=40, help="log lines to read per failing container"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="k8s-sre-agent",
        description="Read-only Kubernetes incident diagnosis with optional LLM root cause analysis",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    diag = sub.add_parser("diagnose", help="detect problems and explain their root cause")
    _add_target_args(diag)
    diag.add_argument(
        "--from-snapshot",
        type=Path,
        metavar="FILE",
        help="analyse a snapshot saved with `snapshot` instead of querying the cluster",
    )
    diag.add_argument("-o", "--output", choices=["markdown", "json"], default="markdown")
    diag.add_argument(
        "--llm",
        choices=PROVIDERS,
        help="override SRE_AGENT_LLM_PROVIDER (credentials always come from the environment)",
    )
    diag.add_argument(
        "--fail-on",
        choices=list(FAIL_ON),
        default="never",
        help="exit with code 2 when a finding at or above this severity exists",
    )

    snap = sub.add_parser("snapshot", help="save a read-only snapshot of a namespace to JSON")
    _add_target_args(snap)
    snap.add_argument("-f", "--file", type=Path, required=True, help="output file")

    sub.add_parser("version", help="print the version")
    return parser


def _load_snapshot(args: argparse.Namespace) -> ClusterSnapshot:
    if getattr(args, "from_snapshot", None):
        data = json.loads(args.from_snapshot.read_text(encoding="utf-8"))
        return ClusterSnapshot.from_dict(data)
    from k8s_sre_agent.collector import collect  # noqa: PLC0415 - imports the kubernetes client

    return collect(
        args.namespace,
        label_selector=args.selector,
        include_logs=not args.no_logs,
        log_tail_lines=args.log_lines,
        context=args.context,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "version":
        print(__version__)
        return EXIT_OK

    try:
        snapshot = _load_snapshot(args)
    except Exception as exc:  # surface any collection error cleanly
        print(f"error: cannot read cluster state: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.command == "snapshot":
        args.file.write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")
        print(f"snapshot of {len(snapshot.pods)} pod(s) written to {args.file}", file=sys.stderr)
        return EXIT_OK

    try:
        settings = LLMSettings.from_env()
        if args.llm:
            settings.provider = args.llm
        provider = build_provider(settings)
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    result = diagnose(snapshot, provider)
    print(to_json(result) if args.output == "json" else to_markdown(result))

    threshold = FAIL_ON[args.fail_on]
    if threshold is not None and any(f.severity.rank <= threshold.rank for f in result.findings):
        return EXIT_FINDINGS
    return EXIT_OK

"""``python -m email_digest`` entry: ``digest`` commands (M2) + ``unsubscribe`` passthrough."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from agentkit.core import ConfigError

from unsubscribe.gmail_api_backend import GmailApiBackend
from unsubscribe.gmail_facade import GmailFacade
from unsubscribe.keep_list import (
    add_to_keep_list,
    load_keep_list,
    merge_keep_list,
    remove_from_keep_list,
    sender_key,
)

from email_digest.cache import cost_report_payload, format_cost_report
from email_digest.config import TopicConfig, load_topic_config
from email_digest.paths import default_cache_db_path, repo_root
from email_digest.pipeline import run_digest
from email_digest.spark_link import spark_deeplink
from email_digest.walkthrough import run_digest_sources

DEFAULT_KEEP_LIST_PATH = Path.home() / ".unsubscribe_keep.json"

_DIST_NAME = "unsubscribe"


def _package_version() -> str:
    """Installed distribution version (``pyproject`` name is still ``unsubscribe``)."""
    try:
        return importlib.metadata.version(_DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0.dev0"


def _default_topics_dir() -> Path:
    return repo_root() / "topics"


def _parse_since(s: str) -> date:
    y, mo, d = s.split("-", 2)
    return date(int(y), int(mo), int(d))


def _digest_run_error_payload(*, topic: str, file: str, error: str) -> dict[str, str]:
    """Stable JSON shape for ``digest run`` failures (single topic or ``--all`` items)."""
    return {"topic": topic, "file": file, "error": error}


def _print_digest_run_error(*, topic: str, file: str, error: str) -> None:
    print(json.dumps(_digest_run_error_payload(topic=topic, file=file, error=error), indent=2))



def _digest_cost(days: int = 7, cache_db: Path | None = None, *, json_out: bool = False) -> int:
    path = cache_db or default_cache_db_path()
    if json_out:
        print(json.dumps(cost_report_payload(path, days=days), indent=2))
        return 0
    report = format_cost_report(path, days=days)
    sys.stdout.write(report)
    return 0


def _digest_topics(ns: argparse.Namespace) -> int:
    """List ``topics/*.yaml`` (loads each file to validate)."""
    topics_dir: Path = ns.topics_dir or _default_topics_dir()
    if not topics_dir.is_dir():
        print(f"No topics directory: {topics_dir}", file=sys.stderr)
        return 2
    loaded: list[tuple[Path, Any]] = []
    for path in sorted(topics_dir.glob("*.yaml")):
        try:
            cfg = load_topic_config(path)
        except (OSError, KeyError, ValueError, TypeError, yaml.YAMLError, ConfigError) as e:
            print(f"Invalid topic config {path}: {e}", file=sys.stderr)
            return 1
        loaded.append((path, cfg))

    if ns.strict:
        bad = [(p.name, cfg.name, p.stem) for p, cfg in loaded if cfg.name != p.stem]
        if bad:
            for fname, name, stem in bad:
                print(
                    f"Topic file {fname!r}: YAML name {name!r} must match file stem {stem!r} "
                    "(rename the file or change ``name:`` in the YAML).",
                    file=sys.stderr,
                )
            return 1

    rows: list[dict[str, Any]] = [
        {"name": cfg.name, "file": path.name, "display_name": cfg.display_name}
        for path, cfg in loaded
    ]
    if ns.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print(f"(no *.yaml under {topics_dir})", file=sys.stderr)
        return 0
    for r in rows:
        sys.stdout.write(f"{r['name']}\t{r['display_name']}\n")
    return 0


def _digest_run(ns: argparse.Namespace) -> int:
    topics_dir: Path = ns.topics_dir or _default_topics_dir()
    if not ns.all and not ns.topic:
        print("Provide a topic name or use ``--all``.", file=sys.stderr)
        return 2

    since: date | None = None
    if ns.since:
        try:
            since = _parse_since(ns.since)
        except ValueError:
            print(
                f"Invalid --since {ns.since!r}; use YYYY-MM-DD (Gregorian calendar date).",
                file=sys.stderr,
            )
            return 2

    if ns.all:
        # Ordered plan: config/strict outcomes first; load Gmail only if at least one
        # topic reaches run_digest (cron-friendly when all YAML fail or dir is empty).
        actions: list[tuple[object, ...]] = []
        any_failed = False
        _ERR, _RUN = object(), object()

        for path in sorted(topics_dir.glob("*.yaml")):
            try:
                cfg = load_topic_config(path)
            except (OSError, KeyError, ValueError, TypeError, yaml.YAMLError, ConfigError) as e:
                any_failed = True
                actions.append(
                    (
                        _ERR,
                        _digest_run_error_payload(
                            topic=path.stem,
                            file=path.name,
                            error=f"config: {e}",
                        ),
                    )
                )
                continue
            if ns.strict and cfg.name != path.stem:
                any_failed = True
                actions.append(
                    (
                        _ERR,
                        _digest_run_error_payload(
                            topic=path.stem,
                            file=path.name,
                            error=(
                                f"strict: YAML name {cfg.name!r} must match file stem {path.stem!r} "
                                "(rename the file or change ``name:``)."
                            ),
                        ),
                    )
                )
                continue
            actions.append((_RUN, cfg, path))

        need_gmail = any(tag is _RUN for tag, *_ in actions)
        results: list[dict[str, Any]] = []
        if need_gmail:
            backend = GmailApiBackend.from_env()
            facade = GmailFacade(backend)
            for item in actions:
                tag = item[0]
                if tag is _ERR:
                    results.append(item[1])
                    continue
                _, cfg, path = item
                try:
                    results.append(
                        run_digest(
                            cfg,
                            facade=facade,
                            keep_list_path=ns.keep_list,
                            max_results=ns.max_results,
                            since=since,
                            cache_db=ns.cache_db,
                            dry_run=ns.dry_run,
                            output_dir=ns.output_dir,
                            template_dir=ns.template_dir,
                        )
                    )
                except Exception as e:
                    any_failed = True
                    results.append(
                        _digest_run_error_payload(
                            topic=cfg.name,
                            file=path.name,
                            error=str(e),
                        )
                    )
        else:
            for item in actions:
                results.append(item[1])

        print(json.dumps(results, indent=2))
        return 1 if any_failed else 0

    topic_path = topics_dir / f"{ns.topic}.yaml"
    try:
        cfg = load_topic_config(topic_path)
    except (OSError, KeyError, ValueError, TypeError, yaml.YAMLError, ConfigError) as e:
        _print_digest_run_error(
            topic=str(ns.topic),
            file=topic_path.name,
            error=f"config: {e}",
        )
        return 1
    if ns.strict and cfg.name != topic_path.stem:
        _print_digest_run_error(
            topic=str(ns.topic),
            file=topic_path.name,
            error=(
                f"strict: YAML name {cfg.name!r} must match file stem {topic_path.stem!r} "
                "(rename the file or change ``name:``)."
            ),
        )
        return 1

    backend = GmailApiBackend.from_env()
    facade = GmailFacade(backend)
    try:
        out = run_digest(
            cfg,
            facade=facade,
            keep_list_path=ns.keep_list,
            max_results=ns.max_results,
            since=since,
            cache_db=ns.cache_db,
            dry_run=ns.dry_run,
            output_dir=ns.output_dir,
            template_dir=ns.template_dir,
        )
    except Exception as e:
        _print_digest_run_error(
            topic=cfg.name,
            file=topic_path.name,
            error=str(e),
        )
        return 1
    print(json.dumps(out, indent=2))
    return 0


def _digest_sources(ns: argparse.Namespace) -> int:
    """Interactive review of digest-source candidates for one or all topics (replaces old ``candidates`` + ``walkthrough``)."""
    topics_dir: Path = ns.topics_dir or _default_topics_dir()
    if not ns.all and not ns.topic:
        print(
            "Provide a topic name (stem of ``topics/<stem>.yaml``) or use ``--all``.",
            file=sys.stderr,
        )
        return 2

    since: date | None = None
    if ns.since:
        try:
            since = _parse_since(ns.since)
        except ValueError:
            print(
                f"Invalid --since {ns.since!r}; use YYYY-MM-DD (Gregorian calendar date).",
                file=sys.stderr,
            )
            return 2

    if ns.all:
        actions: list[tuple[object, ...]] = []
        any_failed = False
        _ERR, _RUN = object(), object()

        for path in sorted(topics_dir.glob("*.yaml")):
            try:
                cfg = load_topic_config(path)
            except (OSError, KeyError, ValueError, TypeError, yaml.YAMLError, ConfigError) as e:
                any_failed = True
                actions.append((_ERR, path.stem, path.name, f"config: {e}"))
                continue
            if ns.strict and cfg.name != path.stem:
                any_failed = True
                actions.append(
                    (
                        _ERR,
                        path.stem,
                        path.name,
                        f"strict: YAML name {cfg.name!r} must match file stem {path.stem!r} "
                        "(rename the file or change ``name:``).",
                    )
                )
                continue
            actions.append((_RUN, cfg, path))

        need_gmail = any(tag is _RUN for tag, *_ in actions)
        if need_gmail:
            backend = GmailApiBackend.from_env()
            facade = GmailFacade(backend)
            for item in actions:
                tag = item[0]
                if tag is _ERR:
                    _, topic, file, error = item
                    print(
                        f"Sources — topic {topic!r} ({file}): {error}",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
                _, cfg, path = item
                try:
                    rc = run_digest_sources(
                        cfg,
                        path,
                        facade,
                        ns.keep_list,
                        since=since,
                        max_results=ns.max_results,
                        input_fn=input,
                        body=ns.body,
                        new_only=ns.new,
                    )
                    if rc != 0:
                        any_failed = True
                except KeyboardInterrupt:
                    return 130
        else:
            for item in actions:
                _, topic, file, error = item
                print(
                    f"Sources — topic {topic!r} ({file}): {error}",
                    file=sys.stderr,
                    flush=True,
                )

        return 1 if any_failed else 0

    topic_path = topics_dir / f"{ns.topic}.yaml"
    try:
        cfg = load_topic_config(topic_path)
    except (OSError, KeyError, ValueError, TypeError, yaml.YAMLError, ConfigError) as e:
        _print_digest_run_error(
            topic=str(ns.topic),
            file=topic_path.name,
            error=f"config: {e}",
        )
        return 1
    if ns.strict and cfg.name != topic_path.stem:
        _print_digest_run_error(
            topic=str(ns.topic),
            file=topic_path.name,
            error=(
                f"strict: YAML name {cfg.name!r} must match file stem {topic_path.stem!r} "
                "(rename the file or change ``name:``)."
            ),
        )
        return 1

    backend = GmailApiBackend.from_env()
    facade = GmailFacade(backend)
    return run_digest_sources(
        cfg,
        topic_path,
        facade,
        ns.keep_list,
        since=since,
        max_results=ns.max_results,
        input_fn=input,
        body=ns.body,
        new_only=ns.new,
    )


def _digest_keep(ns: argparse.Namespace) -> int:
    """Mutate the shared keep-list JSON (same store as ``digest run`` / unsubscribe)."""
    path: Path = ns.keep_list
    if ns.keep_cmd == "add":
        if sender_key(ns.from_header) is None:
            print(
                "Cannot derive a sender address from ``--from``; "
                "use a valid RFC5322-like From string or bare email.",
                file=sys.stderr,
            )
            return 1
        add_to_keep_list(path, ns.from_header, ns.subject)
        return 0
    if ns.keep_cmd == "remove":
        if sender_key(ns.from_header) is None:
            print(
                "Cannot derive a sender address from ``--from``; "
                "use a valid RFC5322-like From string or bare email.",
                file=sys.stderr,
            )
            return 1
        remove_from_keep_list(path, ns.from_header)
        return 0
    if ns.keep_cmd == "merge":
        try:
            raw = json.loads(ns.merge_file.read_text(encoding="utf-8"))
        except OSError as e:
            print(f"Cannot read --file: {e}", file=sys.stderr)
            return 1
        except json.JSONDecodeError as e:
            print(f"Invalid JSON in --file: {e}", file=sys.stderr)
            return 1
        try:
            merge_keep_list(path, raw)
        except TypeError as e:
            print(str(e), file=sys.stderr)
            return 1
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0
    return 2


def _digest_spark_check(ns: argparse.Namespace) -> int:
    """Print one ``readdle-spark://`` URL for manual paste (no Gmail, no network)."""
    mid = (ns.message_id or "").strip()
    if not mid:
        print(
            "Provide a non-empty RFC822 Message-ID (``--message-id``), including angle brackets if used.",
            file=sys.stderr,
        )
        return 2
    url = spark_deeplink(mid)
    if not url:
        print("Could not build Spark URL from message id.", file=sys.stderr)
        return 2
    print(url)
    return 0


def _main_digest(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="email_digest digest")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version", help="Print installed package version")

    cost_p = sub.add_parser("cost", help="Summarize LLM usage from SQLite (last N days)")
    cost_p.add_argument(
        "--days",
        type=int,
        default=7,
        help="Rolling window in days (default 7)",
    )
    cost_p.add_argument(
        "--cache-db",
        type=Path,
        default=None,
        help="SQLite cache path (default: env DIGEST_CACHE_DB or <repo>/cache/digest.sqlite)",
    )
    cost_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON (for scripts / cron)",
    )

    topics_p = sub.add_parser(
        "topics",
        help="List topic YAML files (validates each config)",
    )
    topics_p.add_argument(
        "--topics-dir",
        type=Path,
        default=None,
        help="Topics directory (default: <repo>/topics)",
    )
    topics_p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON array of {name, file, display_name}",
    )
    topics_p.add_argument(
        "--strict",
        action="store_true",
        help="Require YAML ``name`` to match the file stem (e.g. ``ai.yaml`` → name ``ai``); exit 1 if not",
    )

    run_p = sub.add_parser("run", help="Run digest pipeline")
    run_p.add_argument("topic", nargs="?", help="Topic stem (topics/<stem>.yaml)")
    run_p.add_argument(
        "--all",
        action="store_true",
        help="Run every ``*.yaml`` in the topics directory; JSON includes per-topic "
        "errors; exit 1 if any topic failed",
    )
    run_p.add_argument(
        "--strict",
        action="store_true",
        help="Require YAML ``name`` to match the file stem (same rule as ``digest topics --strict``)",
    )
    run_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect + extract + trending only (skip synthesis + HTML file)",
    )
    run_p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write HTML when not using ``--dry-run`` (default: <repo>/output)",
    )
    run_p.add_argument(
        "--template-dir",
        type=Path,
        default=None,
        help="Jinja template directory (default: <repo>/templates)",
    )
    run_p.add_argument(
        "--topics-dir",
        type=Path,
        default=None,
        help="Override topics directory (default: <repo>/topics)",
    )
    run_p.add_argument(
        "--keep-list",
        type=Path,
        default=DEFAULT_KEEP_LIST_PATH,
        help=f"Keep-list JSON (default: {DEFAULT_KEEP_LIST_PATH})",
    )
    run_p.add_argument(
        "--since",
        type=str,
        default=None,
        help="Lower date bound YYYY-MM-DD (maps to Gmail ``after:``)",
    )
    run_p.add_argument(
        "--max-results",
        type=int,
        default=50,
        help="Cap for Gmail ``messages.list`` (default 50)",
    )

    run_p.add_argument(
        "--cache-db",
        type=Path,
        default=None,
        help="SQLite cache path (default: <repo>/cache/digest.sqlite)",
    )

    sources_p = sub.add_parser(
        "sources",
        help="Review digest-source candidates interactively; [Enter] keep, [u] unsubscribe, [s] skip, [q] quit",
    )
    sources_p.add_argument(
        "topic",
        nargs="?",
        default=None,
        help="Topic stem (``topics/<stem>.yaml``); omit when using ``--all``",
    )
    sources_p.add_argument(
        "--all",
        action="store_true",
        help="Review sources for every ``*.yaml`` in the topics directory; config/strict errors printed to stderr, walkthrough continues to next topic; exit 1 if any topic failed",
    )
    sources_p.add_argument(
        "--strict",
        action="store_true",
        help="Require YAML ``name`` to match the file stem (same as ``digest run --strict``)",
    )
    sources_p.add_argument(
        "--topics-dir",
        type=Path,
        default=None,
        help="Override topics directory (default: <repo>/topics)",
    )
    sources_p.add_argument(
        "--since",
        type=str,
        default=None,
        help="Lower date bound YYYY-MM-DD (maps to Gmail ``after:``)",
    )
    sources_p.add_argument(
        "--max-results",
        type=int,
        default=50,
        help="Cap for Gmail ``messages.list`` (default 50)",
    )
    sources_p.add_argument(
        "--keep-list",
        type=Path,
        default=DEFAULT_KEEP_LIST_PATH,
        help=f"Keep-list JSON (default: {DEFAULT_KEEP_LIST_PATH})",
    )
    sources_p.add_argument(
        "--new",
        action="store_true",
        dest="new",
        help="Only show sources not already on the keep list",
    )
    sources_p.add_argument(
        "--body",
        action="store_true",
        help="Prefetch plain-text message bodies in parallel and show a preview (extra Gmail API calls)",
    )

    keep_p = sub.add_parser(
        "keep",
        help="Add, remove, or merge senders in the shared keep-list JSON (same file as digest run / unsubscribe)",
    )
    keep_sp = keep_p.add_subparsers(dest="keep_cmd", required=True)

    keep_add = keep_sp.add_parser(
        "add",
        help="Record a sender as kept (digest source / unsubscribe-protected)",
    )
    keep_add.add_argument(
        "--from",
        dest="from_header",
        required=True,
        help="RFC5322-like From value (e.g. ``News <news@example.com>``) or bare email",
    )
    keep_add.add_argument(
        "--subject",
        default="",
        help="Optional subject line stored with the entry (default empty)",
    )
    keep_add.add_argument(
        "--keep-list",
        type=Path,
        default=DEFAULT_KEEP_LIST_PATH,
        help=f"Keep-list JSON (default: {DEFAULT_KEEP_LIST_PATH})",
    )

    keep_rm = keep_sp.add_parser("remove", help="Remove a sender from the keep list")
    keep_rm.add_argument(
        "--from",
        dest="from_header",
        required=True,
        help="RFC5322-like From value or bare email (must parse to an address)",
    )
    keep_rm.add_argument(
        "--keep-list",
        type=Path,
        default=DEFAULT_KEEP_LIST_PATH,
        help=f"Keep-list JSON (default: {DEFAULT_KEEP_LIST_PATH})",
    )

    keep_merge = keep_sp.add_parser(
        "merge",
        help="Merge a JSON object of {address: {subject, date_kept?}} entries into the keep file",
    )
    keep_merge.add_argument(
        "--file",
        dest="merge_file",
        type=Path,
        required=True,
        help="Path to JSON object (same top-level shape as the keep file)",
    )
    keep_merge.add_argument(
        "--keep-list",
        type=Path,
        default=DEFAULT_KEEP_LIST_PATH,
        help=f"Keep-list JSON (default: {DEFAULT_KEEP_LIST_PATH})",
    )

    spark_p = sub.add_parser(
        "spark-check",
        help="Print a readdle-spark:// URL for manual paste into Spark (device check; no Gmail or network)",
    )
    spark_p.add_argument(
        "--message-id",
        type=str,
        default="<spark-device-check@example.com>",
        help="RFC822 Message-ID (angle brackets optional; used verbatim after strip)",
    )

    ns = p.parse_args(argv)
    if ns.cmd == "version":
        print(_package_version())
        return 0
    if ns.cmd == "cost":
        return _digest_cost(ns.days, ns.cache_db, json_out=ns.json)
    if ns.cmd == "topics":
        return _digest_topics(ns)
    if ns.cmd == "sources":
        return _digest_sources(ns)
    if ns.cmd == "keep":
        return _digest_keep(ns)
    if ns.cmd == "spark-check":
        return _digest_spark_check(ns)
    if ns.cmd == "run":
        return _digest_run(ns)
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--version", "-V"):
        print(_package_version())
        return 0
    if not argv:
        print(
            "Usage: python -m email_digest [--version|-V] digest "
            "<cost|keep|sources|spark-check|topics|run|version> | "
            "python -m email_digest unsubscribe [--days N | reauth]",
            file=sys.stderr,
        )
        return 2
    if argv[0] == "unsubscribe":
        from unsubscribe.cli import main as umain

        rest = argv[1:]
        return umain(rest)
    if argv[0] == "digest":
        return _main_digest(argv[1:])
    print(f"Unknown command {argv[0]!r}.", file=sys.stderr)
    return 2

"""CLI: ``unsubscribe check`` — shortlist, interactive review, keep-list (Iteration 4)."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from pathlib import Path

from unsubscribe.classifier import is_unsubscribable_newsletter
from unsubscribe.execution import (
    debugger_address_from_env,
    print_unsubscribe_report,
    run_automated_unsubscribe,
)
from unsubscribe.gmail_api_backend import GmailApiBackend
from unsubscribe.gmail_facade import GmailFacade, GmailHeaderSummary, headers_from_summary
from unsubscribe.keep_list import (
    add_to_keep_list,
    add_to_unsubscribed_list,
    is_kept,
    is_unsubscribed,
    load_keep_list,
    load_unsubscribed_list,
    remove_from_keep_list,
    save_keep_list,
    sender_key,
)

DEFAULT_KEEP_LIST_PATH = Path.home() / ".unsubscribe_keep.json"
_BODY_PREFETCH_WORKERS = 8
_PREVIEW_WIDTH = 72
_PREVIEW_MAX_LINES = 5

_MIN_LIST_SUMMARY_LEN = 20
_MAX_LIST_SUMMARY_CHARS = 140

# Opening lines Gmail often puts in snippet / HTML-to-text preamble (not the article).
_BOILERPLATE_SUBSTRINGS: tuple[str, ...] = (
    "view this email in your browser",
    "view in your browser",
    "email in your browser",
    "having trouble viewing",
    "trouble viewing this email",
    "forwarded to you",
    "did this email get forwarded",
    "sign up here",
    "sign up to get",
    "today, explained daily",
    "click here to unsubscribe",
    "to unsubscribe",
    "manage your preferences",
    "manage preferences",
    "you are receiving this email",
    "this email was sent to",
    "privacy policy",
    "if you would like to unsubscribe",
)

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
_AFTER_MONTH_DAY_YEAR = re.compile(
    rf"\b{_MONTH}\s+\d{{1,2}},?\s+20\d{{2}}\s+",
    re.IGNORECASE,
)


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def _is_boilerplate_chunk(s: str) -> bool:
    if len(s.strip()) < _MIN_LIST_SUMMARY_LEN:
        return True
    t = s.casefold()
    for frag in _BOILERPLATE_SUBSTRINGS:
        if frag in t:
            return True
    return False


def _split_into_candidate_pieces(text: str) -> list[str]:
    """Split plain text into clauses/sentences for picking a substantive lede."""
    t = _normalize_ws(text)
    if not t:
        return []
    # Primary: sentence boundaries
    parts = re.split(r"(?<=[.!?…])\s+", t)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = _AFTER_MONTH_DAY_YEAR.search(p)
        if m:
            tail = p[m.end() :].strip()
            if tail:
                out.append(tail)
        out.append(p)
    return [_normalize_ws(x) for x in out if x]


def substantive_list_summary(snippet: str, body: str) -> str:
    """
    One-line summary for the **numbered inbox list**: prefer the real article lede,
    not ``View in browser`` / signup / unsubscribe preamble (common in Gmail snippets).
    """
    snippet_n = _normalize_ws(snippet or "")
    body_n = _normalize_ws(body or "")

    for source in (body_n, snippet_n):
        if not source:
            continue
        for piece in _split_into_candidate_pieces(source):
            if _is_boilerplate_chunk(piece):
                continue
            if len(piece) > _MAX_LIST_SUMMARY_CHARS:
                cut = piece[: _MAX_LIST_SUMMARY_CHARS - 1]
                if " " in cut:
                    cut = cut.rsplit(" ", 1)[0]
                return cut + "…"
            return piece

    # Last resort: first non-tiny chunk of body/snippet
    for source in (body_n, snippet_n):
        if len(source) >= _MIN_LIST_SUMMARY_LEN and not _is_boilerplate_chunk(source):
            if len(source) > _MAX_LIST_SUMMARY_CHARS:
                cut = source[: _MAX_LIST_SUMMARY_CHARS - 1].rsplit(" ", 1)[0]
                return cut + "…"
            return source

    return ""


def _fetch_one_body_plain(facade: GmailFacade, message_id: str) -> str:
    try:
        return facade.get_message_body_text(message_id)
    except Exception as e:
        print(f"(Could not load body for {message_id}: {e})", file=sys.stderr)
        return ""


def _start_body_prefetch(
    facade: GmailFacade,
    messages: list[GmailHeaderSummary],
) -> tuple[ThreadPoolExecutor, dict[str, Future[str]]]:
    """Begin one plain-body fetch per message in parallel (only for ``messages``, not whole inbox)."""
    n = len(messages)
    workers = min(_BODY_PREFETCH_WORKERS, max(1, n))
    executor = ThreadPoolExecutor(max_workers=workers)
    futures: dict[str, Future[str]] = {
        m.id: executor.submit(_fetch_one_body_plain, facade, m.id) for m in messages
    }
    return executor, futures


def _resolve_kept_message(
    all_messages: list[GmailHeaderSummary],
    kept_sender_key: str,
    kept_subject: str,
) -> GmailHeaderSummary | None:
    """Find a listed message for this kept sender; prefer subject match, else latest-in-scan."""
    subj_cf = kept_subject.strip().casefold()
    same_sender: list[GmailHeaderSummary] = []
    for m in all_messages:
        sk = sender_key(m.from_)
        if sk is None or sk != kept_sender_key:
            continue
        same_sender.append(m)
        if m.subject.strip().casefold() == subj_cf:
            return m
    return same_sender[0] if same_sender else None


def _date_sort_key(date_header: str) -> float:
    try:
        dt = parsedate_to_datetime(date_header)
        if dt is None:
            return 0.0
        return dt.timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _sort_messages(messages: list[GmailHeaderSummary]) -> list[GmailHeaderSummary]:
    return sorted(messages, key=lambda m: _date_sort_key(m.date), reverse=True)


def _dedup_by_sender(
    messages: list[GmailHeaderSummary],
) -> list[GmailHeaderSummary]:
    """Keep only the first (most recent) message per sender address.

    Uses ``sender_key()`` for normalization.  Messages whose sender cannot be
    normalised (``sender_key`` returns *None*) are always included because they
    cannot be matched against any other message.
    """
    seen: set[str] = set()
    result: list[GmailHeaderSummary] = []
    for m in messages:
        sk = sender_key(m.from_)
        if sk is None:
            result.append(m)
        elif sk not in seen:
            seen.add(sk)
            result.append(m)
    return result


def _body_preview_lines(
    text: str,
    *,
    width: int = _PREVIEW_WIDTH,
    max_lines: int = _PREVIEW_MAX_LINES,
) -> str:
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        cand = " ".join(cur + [w])
        if len(cand) > width and cur:
            lines.append(" ".join(cur))
            cur = [w]
            if len(lines) >= max_lines:
                break
        else:
            cur.append(w)
    if len(lines) < max_lines and cur:
        lines.append(" ".join(cur))
    return "\n".join(lines[:max_lines])


def _prompt_loop(
    prompt: str,
    *,
    input_fn: Callable[[str], str],
    valid_empty: bool,
    valid_u: bool,
    valid_q: bool,
    valid_k: bool = False,
    valid_y: bool = False,
) -> str:
    while True:
        raw = input_fn(prompt)
        s = raw.strip()
        if valid_empty and s == "":
            return ""
        if valid_k and s.lower() == "k":
            return "k"
        if valid_y and s.lower() == "y":
            return "y"
        if valid_u and s.lower() == "u":
            return "u"
        if valid_q and s.lower() == "q":
            return "q"
        if not valid_empty and s == "":
            continue
        if valid_u or valid_q or valid_k or valid_y:
            print("  (Valid keys — try again.)")


def _print_selection_summary(
    new_rows: list[tuple[int, str, str]],
    reconsidered_rows: list[tuple[str, str]],
) -> None:
    """``new_rows``: (walkthrough #, From, Subject).

    ``reconsidered_rows``: (sender email or key for display, Subject) — re-check picks are not numbered.
    """
    print()
    print("Selected for unsubscribe:")
    if new_rows:
        nums = ", ".join(f"#{n}" for n in sorted({t[0] for t in new_rows}))
        print(f"  New: {nums}")
    if reconsidered_rows:
        inner = ", ".join(f"{sender} ({subj})" for sender, subj in reconsidered_rows)
        print(f"  Kept (reconsidered): {inner}")
    total = len(new_rows) + len(reconsidered_rows)
    print(f"  {total} total")


def run_check(
    days: int,
    *,
    facade: GmailFacade,
    keep_list_path: Path = DEFAULT_KEEP_LIST_PATH,
    unsubscribed_list_path: Path | None = None,
    input_fn: Callable[[str], str] = input,
    skip_automation: bool = False,
) -> int:
    """Run interactive check. Returns process exit code (0 or 130 on interrupt)."""
    query = f"newer_than:{days}d -in:chats"
    new_unsub_rows: list[tuple[int, str, str]] = []
    reconsidered_selected: list[tuple[str, str]] = []

    unsub_path = unsubscribed_list_path
    keep_data = load_keep_list(keep_list_path)
    unsub_data = load_unsubscribed_list(unsub_path)

    if keep_data:
        print("Previously kept (will not be asked):")
        for idx, sk in enumerate(sorted(keep_data.keys()), start=1):
            meta = keep_data[sk]
            subj = meta.get("subject", "")
            dk = meta.get("date_kept", "")
            print(f'  {idx}. {sk} — "{subj}" (kept {dk})')
        print()

    if unsub_data:
        print("Previously unsubscribed (will not be asked):")
        for idx, sk in enumerate(sorted(unsub_data.keys()), start=1):
            meta = unsub_data[sk]
            subj = meta.get("subject", "")
            du = meta.get("date_unsubscribed", "")
            print(f'  {idx}. {sk} — "{subj}" (unsub\'d {du})')
        print()

    try:
        print("Fetching inbox from Gmail…", flush=True)
        messages = facade.list_messages(query, max_results=50)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as e:
        print(f"Could not list messages: {e}", file=sys.stderr)
        return 1

    all_messages = list(messages)
    selected_for_unsub: list[tuple[int | None, GmailHeaderSummary]] = []
    try:
        candidates = [
            m
            for m in messages
            if is_unsubscribable_newsletter(
                headers_from_summary(m),
                has_body_unsubscribe_link=False,
            )
            and not is_kept(keep_data, m.from_)
            and not is_unsubscribed(unsub_data, m.from_)
        ]
        candidates = _sort_messages(candidates)
        candidates = _dedup_by_sender(candidates)
        numbered: list[tuple[int, GmailHeaderSummary]] = list(
            enumerate(candidates, start=1)
        )

        if not numbered:
            print(
                f"No new newsletters with unsubscribe links found in the last {days} days."
            )
            print()

        else:
            # Only ``len(candidates)`` body fetches (not list_messages cap / keep-list size).
            # Prefetch in parallel while the user reads the shortlist; walkthrough blocks
            # only if it catches up to a message still loading.
            body_pool, body_futures = _start_body_prefetch(facade, candidates)
            try:
                for num, m in numbered:
                    summary = substantive_list_summary(m.snippet or "", "")
                    if not summary:
                        summary = "(no preview)"
                    print(f"  {num}. {m.from_} : {m.subject} :: {summary}")
                print()

                stop_walkthrough = False
                for num, m in numbered:
                    if stop_walkthrough:
                        break
                    body = body_futures[m.id].result()
                    preview = _body_preview_lines(body) if body else "(no preview)"
                    print("─" * 60)
                    print(f"  #{num}  From: {m.from_}")
                    print(f"  Subject: {m.subject!r}")
                    print(f"  Date: {m.date}")
                    print()
                    print(preview)
                    print()
                    while True:
                        action = _prompt_loop(
                            "  [Enter] keep  [u] unsubscribe  [q] quit walkthrough\n"
                            "  > ",
                            input_fn=input_fn,
                            valid_empty=True,
                            valid_u=True,
                            valid_q=True,
                        )
                        if action == "":
                            add_to_keep_list(keep_list_path, m.from_, m.subject)
                            keep_data = load_keep_list(keep_list_path)
                            break
                        if action == "u":
                            new_unsub_rows.append((num, m.from_, m.subject))
                            selected_for_unsub.append((num, m))
                            break
                        if action == "q":
                            print(
                                "(Stopping walkthrough early; prior keeps from Enter are already saved.)"
                            )
                            stop_walkthrough = True
                            break
            finally:
                body_pool.shutdown(wait=False, cancel_futures=True)

        keep_data = load_keep_list(keep_list_path)
        save_keep_list(keep_list_path, keep_data)

        if keep_data:
            print("Reconsider any previously kept newsletters?")
            snapshot = sorted(keep_data.items(), key=lambda kv: kv[0])
            kept_rows: list[
                tuple[str, dict[str, object], GmailHeaderSummary | None, str, str]
            ] = []
            for sk, meta in snapshot:
                subj = meta.get("subject", "")
                resolved = _resolve_kept_message(all_messages, sk, subj)
                summary = "(no preview)"
                display_from = sk
                if resolved is not None:
                    display_from = resolved.from_
                    # Snippet only: avoids one full-body fetch per kept sender here (was
                    # mistaken for “N walkthrough” loads when the keep list is long).
                    summary = substantive_list_summary(
                        resolved.snippet or "", ""
                    ) or ("(no preview)")
                else:
                    summary = "(not in current search window)"
                kept_rows.append((sk, meta, resolved, summary, display_from))

            for idx, (_sk, meta, _res, summary, display_from) in enumerate(
                kept_rows, start=1
            ):
                subj = meta.get("subject", "")
                print(f"  {idx}. {display_from} : {subj} :: {summary}")
            print()

            gate = _prompt_loop(
                "  [y] Review each one above  [Enter] or [k] Skip (keep all, no changes)\n"
                "  > ",
                input_fn=input_fn,
                valid_empty=True,
                valid_u=False,
                valid_q=False,
                valid_k=True,
                valid_y=True,
            )
            if gate == "y":
                for idx, (sk, meta, resolved, _sum, display_from) in enumerate(
                    kept_rows, start=1
                ):
                    keep_data = load_keep_list(keep_list_path)
                    if sk not in keep_data:
                        continue
                    subj = meta.get("subject", "")
                    dk = meta.get("date_kept", "")
                    print("─" * 60)
                    print(f"  #{idx}  Previously kept: {subj}")
                    print(f"  From: {display_from}")
                    print(f"  Kept on: {dk}")
                    print()
                    action = _prompt_loop(
                        "  [Enter] keep (no change)  [u] unsubscribe  [q] skip remaining\n"
                        "  > ",
                        input_fn=input_fn,
                        valid_empty=True,
                        valid_u=True,
                        valid_q=True,
                    )
                    if action == "":
                        continue
                    if action == "u":
                        sk_disp = sender_key(display_from) or display_from
                        reconsidered_selected.append((sk_disp, subj))
                        if resolved is not None:
                            selected_for_unsub.append((None, resolved))
                        else:
                            print(
                                f"(No message in this search window matched kept sender {sk!r}; "
                                "unsubscribe automation skipped for that entry.)",
                                file=sys.stderr,
                            )
                        remove_from_keep_list(keep_list_path, sk)
                        keep_data = load_keep_list(keep_list_path)
                    elif action == "q":
                        break

        keep_data = load_keep_list(keep_list_path)
        save_keep_list(keep_list_path, keep_data)

        _print_selection_summary(new_unsub_rows, reconsidered_selected)

        if selected_for_unsub and not skip_automation:
            while True:
                raw = input_fn(
                    f"Press Enter to unsubscribe all {len(selected_for_unsub)} selected "
                    "[q to quit]\n  > "
                )
                choice = raw.strip()
                if choice.lower() == "q":
                    return 0
                if choice == "":
                    dbg = debugger_address_from_env()
                    report = run_automated_unsubscribe(
                        facade,
                        selected_for_unsub,
                        debugger_address=dbg,
                    )
                    print_unsubscribe_report(report)
                    # Record unsubscribed senders for future runs
                    for r in report:
                        if r.get("status") in ("confirmed", "server-acknowledged"):
                            add_to_unsubscribed_list(
                                unsub_path, r.get("sender", ""), r.get("subject", "")
                            )
                    break
                print("  (Enter or q — try again.)")

        return 0
    except KeyboardInterrupt:
        print("\nInterrupted. Partial selections:")
        _print_selection_summary(new_unsub_rows, reconsidered_selected)
        return 130


def _default_token_path() -> Path:
    return Path(os.environ.get("GOOGLE_OAUTH_TOKEN", "~/.google/oauth_token.json")).expanduser()


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    else:
        argv = list(argv)
    # Bare `unsubscribe` (no args) runs the primary workflow, like `unsubscribe check`.
    if not argv:
        argv = ["check"]

    parser = argparse.ArgumentParser(prog="unsubscribe")
    sub = parser.add_subparsers(dest="command", required=True)
    check_p = sub.add_parser("check", help="Review recent newsletters and update the keep-list.")
    check_p.add_argument(
        "--days",
        type=int,
        default=3,
        help="Gmail newer_than:Nd (default: 3).",
    )
    reauth_p = sub.add_parser("reauth", help="Regenerate expired OAuth token via browser flow.")
    reauth_p.add_argument(
        "--token-path",
        type=Path,
        default=_default_token_path(),
        help="Output path for the new token JSON.",
    )
    reauth_p.add_argument(
        "--client-secret-path",
        type=Path,
        default=None,
        help="Path to client_secret.json (default: ~/.google/client_secret.json).",
    )
    reauth_p.add_argument(
        "--browser",
        type=str,
        default=None,
        help="Browser to open for OAuth consent (e.g. 'brave', 'chrome').",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            backend = GmailApiBackend.from_env()
            facade = GmailFacade(backend)
            return run_check(args.days, facade=facade)
        if args.command == "reauth":
            GmailApiBackend.regenerate_token(
                args.token_path,
                client_secret_path=args.client_secret_path,
                browser=args.browser,
            )
            print(f"Token written to {args.token_path}")
            return 0
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

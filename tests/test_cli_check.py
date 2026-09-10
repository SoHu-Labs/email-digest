"""CLI ``unsubscribe`` (Iteration 4) with fake backend and scripted input."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from unsubscribe.cli import main, run_check
from unsubscribe.gmail_facade import GmailFacade, GmailHeaderSummary, headers_from_summary

_UNSUB = "<https://list-manage.com/unsub/x>"


def _msg(
    mid: str,
    *,
    from_: str,
    subject: str,
    date: str,
    snippet: str = "sn",
) -> GmailHeaderSummary:
    return GmailHeaderSummary(
        id=mid,
        thread_id="t",
        from_=from_,
        subject=subject,
        date=date,
        snippet=snippet,
        list_unsubscribe=_UNSUB,
        list_unsubscribe_post=None,
    )


class _FakeBackend:
    def __init__(self, messages: list[GmailHeaderSummary]) -> None:
        self.messages = messages
        self.last_query: str | None = None
        self.last_max_results: int | None = None
        self.bodies: dict[str, str] = {}

    def list_messages(self, query: str, *, max_results: int = 50):
        self.last_query = query
        self.last_max_results = max_results
        return self.messages[:max_results]

    def get_message_html(self, message_id: str) -> str:
        return "<html></html>"

    def get_message_body_text(self, message_id: str) -> str:
        return self.bodies.get(message_id, f"Long body text for {message_id}.")

    def get_profile_email(self) -> str:
        return "fake-check@example.com"

    def send_html_email(self, *, to: str, subject: str, html: str) -> None:
        pass


def test_headers_from_summary_round_trip() -> None:
    m = _msg("1", from_="A <a@a.com>", subject="S", date="Mon, 1 Jan 2024 00:00:00 +0000")
    h = headers_from_summary(m)
    assert h["From"] == "A <a@a.com>"
    assert h["List-Unsubscribe"] == _UNSUB


def test_run_check_query_days_and_chats(tmp_path: Path) -> None:
    k = tmp_path / ".unsubscribe_keep.json"
    fb = _FakeBackend([])
    facade = GmailFacade(fb)

    def _inp(_p: str = "") -> str:
        return ""

    run_check(7, facade=facade, keep_list_path=k, unsubscribed_list_path=tmp_path / ".unsubscribed.json", input_fn=_inp, skip_automation=True)
    assert fb.last_query is not None
    assert "newer_than:7d" in fb.last_query
    assert "-in:chats" in fb.last_query


def test_run_check_scans_full_window_not_fifty(tmp_path: Path) -> None:
    k = tmp_path / ".unsubscribe_keep.json"
    fb = _FakeBackend([])
    facade = GmailFacade(fb)

    def _inp(_p: str = "") -> str:
        return ""

    run_check(28, facade=facade, keep_list_path=k, unsubscribed_list_path=tmp_path / ".unsubscribed.json", input_fn=_inp, skip_automation=True)
    assert fb.last_max_results == 500


def test_run_check_renders_boxed_sections(capsys, tmp_path: Path) -> None:
    k = tmp_path / ".unsubscribe_keep.json"
    k.write_text(
        json.dumps({"kept@old.com": {"subject": "Old", "date_kept": "2024-01-01"}}),
        encoding="utf-8",
    )
    unsub = tmp_path / ".unsubscribed.json"
    unsub.write_text(
        json.dumps({"gone@old.com": {"subject": "Gone", "date_unsubscribed": "2024-01-02"}}),
        encoding="utf-8",
    )
    messages = [
        _msg("m1", from_="N <new@list.com>", subject="Alpha", date="Wed, 10 Jan 2024 12:00:00 +0000"),
    ]
    fb = _FakeBackend(messages)
    facade = GmailFacade(fb)
    inputs = iter(["u", ""])

    def _inp(_p: str = "") -> str:
        return next(inputs)

    run_check(3, facade=facade, keep_list_path=k, unsubscribed_list_path=unsub, input_fn=_inp, skip_automation=True)
    out = capsys.readouterr().out
    assert "┌" in out
    assert "└" in out
    assert "│ Previously kept (will not be asked):" in out
    assert "│ Previously unsubscribed (will not be asked):" in out
    assert "│ New newsletters (last 3 days):" in out
    assert "┌─ #1 " in out
    assert "│ [Enter] keep   [u] unsubscribe   [q] quit walkthrough" in out


def test_run_check_filters_kept_and_shows_summary(capsys, tmp_path: Path) -> None:
    k = tmp_path / ".unsubscribe_keep.json"
    k.write_text(
        json.dumps(
            {"kept@old.com": {"subject": "Old", "date_kept": "2024-01-01"}},
            indent=2,
        ),
        encoding="utf-8",
    )
    messages = [
        _msg(
            "m_kept",
            from_="K <kept@old.com>",
            subject="Dup",
            date="Fri, 5 Jan 2024 12:00:00 +0000",
        ),
        _msg(
            "m_newer",
            from_="N <new1@list.com>",
            subject="Alpha",
            date="Wed, 10 Jan 2024 12:00:00 +0000",
        ),
        _msg(
            "m_older",
            from_="O <new2@list.com>",
            subject="Beta",
            date="Mon, 8 Jan 2024 12:00:00 +0000",
        ),
    ]
    fb = _FakeBackend(messages)
    facade = GmailFacade(fb)

    inputs = iter(
        [
            "u",
            "u",
            "y",
            "u",
        ]
    )

    def _inp(_p: str = "") -> str:
        return next(inputs)

    run_check(3, facade=facade, keep_list_path=k, unsubscribed_list_path=tmp_path / ".unsubscribed.json", input_fn=_inp, skip_automation=True)
    out = capsys.readouterr().out
    assert "Previously kept (will not be asked):" in out
    assert '  1. kept@old.com — "Old" (kept 2024-01-01)' in out
    assert "kept@old.com" in out
    assert "  1. N <new1@list.com> : Alpha ::" in out
    assert "  2. O <new2@list.com> : Beta ::" in out
    assert "K <kept@old.com> : Old ::" in out
    assert "Selected for unsubscribe:" in out
    assert "  New: #1, #2" in out
    assert "  Kept (reconsidered): kept@old.com (Old)" in out
    assert "  3 total" in out
    data = json.loads(k.read_text(encoding="utf-8"))
    assert "kept@old.com" not in data
    assert data == {}


def test_run_check_zero_new_still_reconsider(capsys, tmp_path: Path) -> None:
    k = tmp_path / ".unsubscribe_keep.json"
    k.write_text(
        json.dumps({"sole@x.com": {"subject": "S", "date_kept": "d"}}),
        encoding="utf-8",
    )
    fb = _FakeBackend([])
    facade = GmailFacade(fb)
    inputs = iter([""])

    def _inp(_p: str = "") -> str:
        return next(inputs)

    run_check(3, facade=facade, keep_list_path=k, unsubscribed_list_path=tmp_path / ".unsubscribed.json", input_fn=_inp, skip_automation=True)
    out = capsys.readouterr().out
    assert "No new newsletters with unsubscribe links found" in out
    assert "Reconsider any previously kept newsletters?" in out
    assert "  1. sole@x.com : S ::" in out


def test_run_check_q_mid_walkthrough_keeps_incremental_save(tmp_path: Path, capsys) -> None:
    """Enter-keep on first message persists before **q** quits walkthrough on the next."""
    k = tmp_path / ".unsubscribe_keep.json"
    messages = [
        _msg("a", from_="A <a@a.com>", subject="A", date="Wed, 10 Jan 2024 00:00:00 +0000"),
        _msg("b", from_="B <b@b.com>", subject="B", date="Tue, 9 Jan 2024 00:00:00 +0000"),
    ]
    fb = _FakeBackend(messages)
    facade = GmailFacade(fb)
    inputs = iter(["", "q", ""])

    def _inp(_p: str = "") -> str:
        return next(inputs)

    run_check(3, facade=facade, keep_list_path=k, unsubscribed_list_path=tmp_path / ".unsubscribed.json", input_fn=_inp, skip_automation=True)
    data = json.loads(k.read_text(encoding="utf-8"))
    assert "a@a.com" in data
    out = capsys.readouterr().out
    assert "Stopping walkthrough early" in out


def test_main_empty_argv_defaults_to_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def _fake_run_check(days: int, **kwargs: object) -> int:
        seen["days"] = days
        return 0

    monkeypatch.setattr("unsubscribe.cli.run_check", _fake_run_check)
    monkeypatch.setattr(
        "unsubscribe.cli.GmailApiBackend",
        MagicMock(from_env=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr("unsubscribe.cli.GmailFacade", MagicMock())

    assert main([]) == 0
    assert seen.get("days") == 3


def test_main_flag_first_runs_check_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def _fake_run_check(days: int, **kwargs: object) -> int:
        seen["days"] = days
        return 0

    monkeypatch.setattr("unsubscribe.cli.run_check", _fake_run_check)
    monkeypatch.setattr(
        "unsubscribe.cli.GmailApiBackend",
        MagicMock(from_env=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr("unsubscribe.cli.GmailFacade", MagicMock())

    assert main(["--days", "28"]) == 0
    assert seen.get("days") == 28


def test_main_check_is_not_a_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("unsubscribe.cli.run_check", MagicMock(return_value=0))
    monkeypatch.setattr(
        "unsubscribe.cli.GmailApiBackend",
        MagicMock(from_env=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr("unsubscribe.cli.GmailFacade", MagicMock())

    with pytest.raises(SystemExit) as ei:
        main(["check"])
    assert ei.value.code == 2


def test_main_help() -> None:
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0


def test_main_reauth_help() -> None:
    with pytest.raises(SystemExit) as ei:
        main(["reauth", "--help"])
    assert ei.value.code == 0


def test_run_check_automation_enter_calls_run_automated(
    capsys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLEADS_BROWSER_DEBUGGER_ADDRESS", "127.0.0.1:9222")
    k = tmp_path / ".unsubscribe_keep.json"
    messages = [
        _msg("m1", from_="X <x@x.com>", subject="S", date="Wed, 10 Jan 2024 12:00:00 +0000"),
    ]
    fb = _FakeBackend(messages)
    facade = GmailFacade(fb)
    inputs = iter(["u", ""])

    def _inp(_p: str = "") -> str:
        return next(inputs)

    sample_rows = [
        {
            "email_index": 1,
            "subject": "S",
            "sender": "X <x@x.com>",
            "method": "one-click",
            "status": "server-acknowledged",
            "detail": "One-Click unsubscribe accepted (HTTP 204).",
        },
    ]
    with patch("unsubscribe.cli.run_automated_unsubscribe", return_value=sample_rows) as mock_auto:
        run_check(3, facade=facade, keep_list_path=k, unsubscribed_list_path=tmp_path / ".unsubscribed.json", input_fn=_inp, skip_automation=False)
    mock_auto.assert_called_once()
    out = capsys.readouterr().out
    assert "── Results ──" in out
    assert "one-click POST" in out or "server accepted" in out
    assert "may require further steps" in out


def test_run_check_confirm_prompt_renders_boxed_form(
    capsys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLEADS_BROWSER_DEBUGGER_ADDRESS", "127.0.0.1:9222")
    k = tmp_path / ".unsubscribe_keep.json"
    messages = [
        _msg("m1", from_="X <x@x.com>", subject="S", date="Wed, 10 Jan 2024 12:00:00 +0000"),
    ]
    fb = _FakeBackend(messages)
    facade = GmailFacade(fb)
    inputs = iter(["u", ""])

    def _inp(_p: str = "") -> str:
        return next(inputs)

    with patch("unsubscribe.cli.run_automated_unsubscribe", return_value=[]):
        run_check(3, facade=facade, keep_list_path=k, unsubscribed_list_path=tmp_path / ".unsubscribed.json", input_fn=_inp, skip_automation=False)
    out = capsys.readouterr().out
    assert "┌─ Unsubscribe all 1 selected " in out
    assert "│ [Enter] confirm   [q] quit" in out


def test_run_check_automation_q_skips(capsys, tmp_path: Path) -> None:
    k = tmp_path / ".unsubscribe_keep.json"
    messages = [
        _msg("m1", from_="X <x@x.com>", subject="S", date="Wed, 10 Jan 2024 12:00:00 +0000"),
    ]
    fb = _FakeBackend(messages)
    facade = GmailFacade(fb)
    inputs = iter(["u", "q"])

    def _inp(_p: str = "") -> str:
        return next(inputs)

    with patch("unsubscribe.cli.run_automated_unsubscribe") as mock_auto:
        run_check(3, facade=facade, keep_list_path=k, unsubscribed_list_path=tmp_path / ".unsubscribed.json", input_fn=_inp, skip_automation=False)
    mock_auto.assert_not_called()


def test_run_check_reconsider_gate_k_skips_review(tmp_path: Path, capsys) -> None:
    k = tmp_path / ".unsubscribe_keep.json"
    k.write_text(
        json.dumps({"a@a.com": {"subject": "Subj", "date_kept": "2024-01-01"}}),
        encoding="utf-8",
    )
    fb = _FakeBackend([])
    facade = GmailFacade(fb)
    inputs = iter(["k"])

    def _inp(_p: str = "") -> str:
        return next(inputs)

    run_check(3, facade=facade, keep_list_path=k, unsubscribed_list_path=tmp_path / ".unsubscribed.json", input_fn=_inp, skip_automation=True)
    out = capsys.readouterr().out
    data = json.loads(k.read_text(encoding="utf-8"))
    assert "a@a.com" in data
    assert "  1. a@a.com : Subj ::" in out
    assert "  #1  Previously kept:" not in out


def test_run_check_walkthrough_enter_keeps_sender(tmp_path: Path) -> None:
    k = tmp_path / ".unsubscribe_keep.json"
    messages = [
        _msg("x", from_="Z <z@z.com>", subject="Zed", date="Wed, 10 Jan 2024 12:00:00 +0000"),
    ]
    fb = _FakeBackend(messages)
    facade = GmailFacade(fb)
    inputs = iter(["", ""])

    def _inp(_p: str = "") -> str:
        return next(inputs)

    run_check(3, facade=facade, keep_list_path=k, unsubscribed_list_path=tmp_path / ".unsubscribed.json", input_fn=_inp, skip_automation=True)
    data = json.loads(k.read_text(encoding="utf-8"))
    assert "z@z.com" in data


def test_run_check_walkthrough_u_only_does_not_keep(tmp_path: Path) -> None:
    k = tmp_path / ".unsubscribe_keep.json"
    messages = [
        _msg("x", from_="Z <z@z.com>", subject="Zed", date="Wed, 10 Jan 2024 12:00:00 +0000"),
    ]
    fb = _FakeBackend(messages)
    facade = GmailFacade(fb)
    inputs = iter(["u", ""])

    def _inp(_p: str = "") -> str:
        return next(inputs)

    run_check(3, facade=facade, keep_list_path=k, unsubscribed_list_path=tmp_path / ".unsubscribed.json", input_fn=_inp, skip_automation=True)
    assert json.loads(k.read_text(encoding="utf-8")) == {}


def test_invalid_prompt_repeats(capsys, tmp_path: Path) -> None:
    k = tmp_path / ".unsubscribe_keep.json"
    messages = [
        _msg("x", from_="X <x@x.com>", subject="X", date="Wed, 10 Jan 2024 00:00:00 +0000"),
    ]
    fb = _FakeBackend(messages)
    facade = GmailFacade(fb)
    inputs = iter(["maybe", "u", ""])

    def _inp(_p: str = "") -> str:
        return next(inputs)

    run_check(3, facade=facade, keep_list_path=k, unsubscribed_list_path=tmp_path / ".unsubscribed.json", input_fn=_inp, skip_automation=True)
    assert "try again" in capsys.readouterr().out


def test_dedup_by_sender_keeps_most_recent_only(capsys, tmp_path: Path) -> None:
    """Multiple messages from the same sender → only the most recent appears."""
    k = tmp_path / ".unsubscribe_keep.json"
    # Two emails from the same sender (different display names, same address)
    messages = [
        _msg(
            "older",
            from_="Newsletter <nl@example.com>",
            subject="Older issue",
            date="Wed, 8 Jan 2024 12:00:00 +0000",
        ),
        _msg(
            "newer",
            from_="NL <nl@example.com>",
            subject="Newer issue",
            date="Fri, 10 Jan 2024 12:00:00 +0000",
        ),
    ]
    fb = _FakeBackend(messages)
    facade = GmailFacade(fb)
    inputs = iter(["", ""])  # keep the deduped entry, then done with reconsider

    def _inp(_p: str = "") -> str:
        return next(inputs)

    run_check(3, facade=facade, keep_list_path=k, unsubscribed_list_path=tmp_path / ".unsubscribed.json", input_fn=_inp, skip_automation=True)
    out = capsys.readouterr().out
    # Only one entry shown (the most recent "Newer issue")
    assert "  1. NL <nl@example.com> : Newer issue ::" in out
    # The older duplicate must NOT appear as a numbered entry
    assert "Older issue" not in out
    # Keep-list should have the sender stored once
    data = json.loads(k.read_text(encoding="utf-8"))
    assert "nl@example.com" in data
    assert data["nl@example.com"]["subject"] == "Newer issue"

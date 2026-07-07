"""Unit tests for Brave batch unsubscribe (WebDriver mocked)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from unsubscribe.browser_unsubscribe import (
    UnsubscribeElementNotFoundError,
    UnsubscribeFlowCase,
    _maybe_click_unsubscribe_from_all,
    _maybe_fill_visible_email_field,
    _page_suggests_unsubscribed_confirmed,
    _try_click_unsubscribe_on_page,
    batch_browser_unsubscribe,
    _find_unsubscribe_element,
)


@pytest.fixture(autouse=True)
def _disable_real_page_capture_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """``batch_browser_unsubscribe`` always requests capture; mock driver must not write to repo."""
    class _NoDiskCapture:
        @staticmethod
        def create(_jobs: object) -> None:
            return None

    monkeypatch.setattr(
        "unsubscribe.browser_unsubscribe.PageCaptureSession",
        _NoDiskCapture,
    )


def _visible_el() -> MagicMock:
    el = MagicMock()
    el.is_displayed.return_value = True
    return el


def test_find_unsubscribe_prefers_visible() -> None:
    driver = MagicMock()
    hidden = _visible_el()
    hidden.is_displayed.return_value = False
    visible = _visible_el()
    driver.find_elements.return_value = [hidden, visible]
    el = _find_unsubscribe_element(driver)
    assert el is visible


def _job(
    idx: int | None, url: str, delivered: str | None = None
) -> tuple[int | None, str, str, str, str | None]:
    return (idx, "Subj", "A <a@a.com>", url, delivered)


def test_batch_attach_once_get_each_url_quit_once() -> None:
    mock_driver = MagicMock()
    mock_driver.window_handles = ["main"]
    mock_el = _visible_el()
    urls = ["https://one.example/unsub", "https://two.example/unsub"]
    jobs = [_job(1, urls[0]), _job(2, urls[1])]

    with (
        patch(
            "unsubscribe.browser_unsubscribe.ensure_brave_running",
        ),
        patch(
            "unsubscribe.browser_unsubscribe.chrome_driver_attach",
            return_value=mock_driver,
        ) as mock_attach,
        patch(
            "unsubscribe.browser_unsubscribe._try_click_unsubscribe_on_page",
        ) as mock_try,
        patch(
            "unsubscribe.browser_unsubscribe._page_suggests_unsubscribed_confirmed",
            return_value=True,
        ),
    ):
        out = batch_browser_unsubscribe(
            jobs,
            debugger_address="127.0.0.1:9222",
            timeout_per_url_s=10,
            quiet=True,
        )

    mock_attach.assert_called_once_with(debugger_address="127.0.0.1:9222")
    assert mock_driver.get.call_count == 2
    assert mock_driver.get.call_args_list[0].args[0] == urls[0]
    assert mock_driver.get.call_args_list[1].args[0] == urls[1]
    mock_try.assert_called()
    mock_driver.quit.assert_called_once()
    assert len(out) == 2
    assert out[0]["status"] == "confirmed" and out[1]["status"] == "confirmed"
    assert out[0]["email_index"] == 1


def test_batch_failure_on_one_url_continues_and_quits_once() -> None:
    mock_driver = MagicMock()
    mock_driver.window_handles = ["main"]

    calls = {"n": 0}

    def _try(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise UnsubscribeElementNotFoundError("nope")

    jobs = [
        _job(None, "https://bad.example/u"),
        _job(None, "https://good.example/u"),
    ]

    with (
        patch(
            "unsubscribe.browser_unsubscribe.ensure_brave_running",
        ),
        patch(
            "unsubscribe.browser_unsubscribe.chrome_driver_attach",
            return_value=mock_driver,
        ),
        patch(
            "unsubscribe.browser_unsubscribe._try_click_unsubscribe_on_page",
            side_effect=_try,
        ),
        patch(
            "unsubscribe.browser_unsubscribe.save_live_brave_failure_trace",
        ) as mock_trace,
        patch(
            "unsubscribe.browser_unsubscribe._page_suggests_unsubscribed_confirmed",
            return_value=True,
        ),
    ):
        out = batch_browser_unsubscribe(
            jobs,
            debugger_address="127.0.0.1:9222",
            quiet=True,
        )

    mock_trace.assert_called()
    mock_driver.quit.assert_called_once()
    assert out[0]["status"] == "failed"
    assert "nope" in out[0]["detail"]
    assert out[1]["status"] == "confirmed"


def test_empty_jobs_no_attach() -> None:
    with patch("unsubscribe.browser_unsubscribe.chrome_driver_attach") as mock_attach:
        out = batch_browser_unsubscribe([], debugger_address="127.0.0.1:9222", quiet=True)
    mock_attach.assert_not_called()
    assert out == []


def test_unsubscribe_flow_cases_are_distinct_documented_strings() -> None:
    """Each ``UnsubscribeFlowCase`` value is listed for coverage of page shapes."""
    cases = (
        UnsubscribeFlowCase.SIMPLE_SINGLE_CLICK,
        UnsubscribeFlowCase.UNSUBSCRIBE_FROM_ALL_THEN_CLICK,
        UnsubscribeFlowCase.EMAIL_FIELD_THEN_CLICK,
    )
    assert len(set(cases)) == 3
    assert all(isinstance(c, str) and c for c in cases)


def test_confirmation_detects_dmv_subscriber_list_thank_you_copy() -> None:
    """Government-style list removal (e.g. DMV) — confirms without generic 'unsubscribed' wording."""
    copy = (
        "Thank you\n"
        "You have been successfully removed from this subscriber list and won't receive "
        "any further emails from us."
    )
    with patch(
        "unsubscribe.browser_unsubscribe._visible_page_text",
        return_value=copy,
    ):
        assert _page_suggests_unsubscribed_confirmed(MagicMock()) is True


def test_confirmation_detects_linkedin_style_copy_typographic_apostrophe() -> None:
    """LinkedIn uses ``You've unsubscribed`` / ``You'll no longer receive`` (often U+2019 in DOM)."""
    copy = (
        "You\u2019ve unsubscribed You\u2019ll no longer receive emails from LinkedIn about new articles."
    )
    with patch(
        "unsubscribe.browser_unsubscribe._visible_page_text",
        return_value=copy,
    ):
        assert _page_suggests_unsubscribed_confirmed(MagicMock()) is True


def test_try_click_skips_interaction_when_landing_already_confirmed() -> None:
    """One-click URLs can land on a final confirmation page with no unsubscribe control."""
    with patch("unsubscribe.browser_unsubscribe.time.sleep"):
        with patch("unsubscribe.browser_unsubscribe.WebDriverWait"):
            driver = MagicMock()
            with patch(
                "unsubscribe.browser_unsubscribe._maybe_click_unsubscribe_from_all",
            ) as m_all:
                with patch(
                    "unsubscribe.browser_unsubscribe._maybe_fill_visible_email_field",
                ) as m_fill:
                    with patch(
                        "unsubscribe.browser_unsubscribe._click_unsubscribe_once_main_or_iframes",
                    ) as m_click:
                        with patch(
                            "unsubscribe.browser_unsubscribe._page_suggests_unsubscribed_confirmed",
                            return_value=True,
                        ) as m_conf:
                            _try_click_unsubscribe_on_page(
                                driver, settle_s=0.01, subscriber_email=None
                            )
    m_conf.assert_called_once()
    m_all.assert_not_called()
    m_fill.assert_not_called()
    m_click.assert_not_called()


def test_maybe_click_unsubscribe_from_all_uses_execute_script() -> None:
    """``UnsubscribeFlowCase.UNSUBSCRIBE_FROM_ALL_THEN_CLICK`` — DOM scan + click."""
    driver = MagicMock()
    driver.execute_script.return_value = True
    assert _maybe_click_unsubscribe_from_all(driver) is True
    driver.execute_script.assert_called_once()
    script, needles_arg = driver.execute_script.call_args[0]
    assert "needles" in script
    assert "unsubscribe from all" in needles_arg


def test_maybe_fill_visible_email_field_fills_first_empty() -> None:
    """``UnsubscribeFlowCase.EMAIL_FIELD_THEN_CLICK`` — fill before main Unsubscribe click."""
    inp = MagicMock()
    inp.is_displayed.return_value = True
    inp.get_attribute.return_value = ""
    driver = MagicMock()
    driver.find_elements.return_value = [inp]
    assert _maybe_fill_visible_email_field(driver, "reader@example.com") is True
    inp.clear.assert_called_once()
    inp.send_keys.assert_called_once_with("reader@example.com")


def test_maybe_fill_visible_email_field_skips_when_blank_param() -> None:
    driver = MagicMock()
    assert _maybe_fill_visible_email_field(driver, "   ") is False
    driver.find_elements.assert_not_called()


def test_maybe_fill_visible_email_field_skips_prefilled() -> None:
    inp = MagicMock()
    inp.is_displayed.return_value = True
    inp.get_attribute.return_value = "already@here.org"
    driver = MagicMock()
    driver.find_elements.return_value = [inp]
    assert _maybe_fill_visible_email_field(driver, "other@x.com") is False
    inp.send_keys.assert_not_called()


def test_try_click_runs_pre_steps_then_main_click() -> None:
    with patch("unsubscribe.browser_unsubscribe.time.sleep"):
        with patch("unsubscribe.browser_unsubscribe.WebDriverWait"):
            driver = MagicMock()
            with patch(
                "unsubscribe.browser_unsubscribe._maybe_click_unsubscribe_from_all",
                return_value=False,
            ) as m_all:
                with patch(
                    "unsubscribe.browser_unsubscribe._maybe_fill_visible_email_field",
                    return_value=False,
                ) as m_fill:
                    with patch(
                        "unsubscribe.browser_unsubscribe._click_unsubscribe_once_main_or_iframes",
                    ) as m_click:
                        with patch(
                            "unsubscribe.browser_unsubscribe._page_suggests_unsubscribed_confirmed",
                            side_effect=[False, True],
                        ):
                            _try_click_unsubscribe_on_page(
                                driver, settle_s=0.01, subscriber_email=None
                            )
            m_all.assert_called_once_with(driver)
            m_fill.assert_not_called()
            m_click.assert_called_once_with(driver)


def test_try_click_fills_email_when_subscriber_email_set() -> None:
    with patch("unsubscribe.browser_unsubscribe.time.sleep"):
        with patch("unsubscribe.browser_unsubscribe.WebDriverWait"):
            driver = MagicMock()
            with patch(
                "unsubscribe.browser_unsubscribe._maybe_click_unsubscribe_from_all",
                return_value=False,
            ):
                with patch(
                    "unsubscribe.browser_unsubscribe._maybe_fill_visible_email_field",
                    return_value=True,
                ) as m_fill:
                    with patch(
                        "unsubscribe.browser_unsubscribe._click_unsubscribe_once_main_or_iframes",
                    ):
                        with patch(
                            "unsubscribe.browser_unsubscribe._page_suggests_unsubscribed_confirmed",
                            side_effect=[False, True],
                        ):
                            _try_click_unsubscribe_on_page(
                                driver,
                                settle_s=0.01,
                                subscriber_email="me@list.org",
                            )
            m_fill.assert_called_once_with(driver, "me@list.org")


def test_try_click_second_unsubscribe_round_when_not_confirmed() -> None:
    """After 'unsubscribe from all' + first click, some pages need a confirm Unsubscribe."""
    with patch("unsubscribe.browser_unsubscribe.time.sleep"):
        with patch("unsubscribe.browser_unsubscribe.WebDriverWait"):
            driver = MagicMock()
            n = {"click": 0}

            def _count_click(d: MagicMock) -> None:
                n["click"] += 1

            with patch(
                "unsubscribe.browser_unsubscribe._maybe_click_unsubscribe_from_all",
                return_value=False,
            ):
                with patch(
                    "unsubscribe.browser_unsubscribe._maybe_fill_visible_email_field",
                    return_value=False,
                ):
                    with patch(
                        "unsubscribe.browser_unsubscribe._click_unsubscribe_once_main_or_iframes",
                        side_effect=_count_click,
                    ):
                        with patch(
                            "unsubscribe.browser_unsubscribe._page_suggests_unsubscribed_confirmed",
                            return_value=False,
                        ):
                            _try_click_unsubscribe_on_page(driver, settle_s=0.01)
    assert n["click"] == 2


def test_find_unsubscribe_finds_submit_input_via_script() -> None:
    """Sites such as Wizz Air use ``input[type=submit]`` with value ``Unsubscribe`` (no link text)."""
    driver = MagicMock()
    driver.find_elements.return_value = []
    submit = _visible_el()
    driver.execute_script.return_value = submit
    el = _find_unsubscribe_element(driver)
    assert el is submit


def test_batch_uses_job_mailbox_hint_when_env_absent() -> None:
    mock_driver = MagicMock()
    mock_driver.window_handles = ["main"]
    jobs = [(1, "S", "snd", "https://wizz.example/u", "hint@mailbox.test")]
    with (
        patch(
            "unsubscribe.browser_unsubscribe.ensure_brave_running",
        ),
        patch(
            "unsubscribe.browser_unsubscribe.chrome_driver_attach",
            return_value=mock_driver,
        ),
        patch(
            "unsubscribe.browser_unsubscribe._try_click_unsubscribe_on_page",
        ) as mock_try,
        patch(
            "unsubscribe.browser_unsubscribe._page_suggests_unsubscribed_confirmed",
            return_value=True,
        ),
    ):
        batch_browser_unsubscribe(
            jobs,
            debugger_address="127.0.0.1:9222",
            timeout_per_url_s=10,
            quiet=True,
        )
    assert mock_try.call_args.kwargs.get("subscriber_email") == "hint@mailbox.test"


def test_batch_prefers_env_subscriber_over_job_mailbox_hint() -> None:
    mock_driver = MagicMock()
    mock_driver.window_handles = ["main"]
    jobs = [(1, "S", "snd", "https://wizz.example/u", "hint@mailbox.test")]
    with (
        patch(
            "unsubscribe.browser_unsubscribe.ensure_brave_running",
        ),
        patch(
            "unsubscribe.browser_unsubscribe.chrome_driver_attach",
            return_value=mock_driver,
        ),
        patch(
            "unsubscribe.browser_unsubscribe._try_click_unsubscribe_on_page",
        ) as mock_try,
        patch(
            "unsubscribe.browser_unsubscribe._page_suggests_unsubscribed_confirmed",
            return_value=True,
        ),
    ):
        batch_browser_unsubscribe(
            jobs,
            debugger_address="127.0.0.1:9222",
            timeout_per_url_s=10,
            quiet=True,
            subscriber_email="env-wins@example.org",
        )
    assert mock_try.call_args.kwargs.get("subscriber_email") == "env-wins@example.org"


def test_batch_forwards_subscriber_email_to_page_handler() -> None:
    mock_driver = MagicMock()
    mock_driver.window_handles = ["main"]
    jobs = [_job(1, "https://pref.example/unsub")]
    with (
        patch(
            "unsubscribe.browser_unsubscribe.ensure_brave_running",
        ),
        patch(
            "unsubscribe.browser_unsubscribe.chrome_driver_attach",
            return_value=mock_driver,
        ),
        patch(
            "unsubscribe.browser_unsubscribe._try_click_unsubscribe_on_page",
        ) as mock_try,
        patch(
            "unsubscribe.browser_unsubscribe._page_suggests_unsubscribed_confirmed",
            return_value=True,
        ),
    ):
        batch_browser_unsubscribe(
            jobs,
            debugger_address="127.0.0.1:9222",
            timeout_per_url_s=10,
            quiet=True,
            subscriber_email="subscriber@dmv.example",
        )
    assert mock_try.call_args.kwargs.get("subscriber_email") == "subscriber@dmv.example"

"""Gmail API (``users.messages`` list/get/send, profile) using OAuth token file from disk."""

from __future__ import annotations

import base64
import os
import re
import threading
from email.utils import getaddresses
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from unsubscribe.gmail_facade import GmailHeaderSummary, GmailTransportError

_ENV_OAUTH_TOKEN = "GOOGLE_OAUTH_TOKEN"

_SCOPE_GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
_SCOPE_GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
_SCOPES = (_SCOPE_GMAIL_READONLY,)  # minimum needed; gmail.send checked lazily at send time

_METADATA_HEADERS = (
    "List-Unsubscribe",
    "List-Unsubscribe-Post",
    "Subject",
    "From",
    "Date",
    "Message-ID",
    "Delivered-To",
    "To",
)


def _mailbox_from_rfc5322_header_value(raw: str | None) -> str | None:
    """First ``@`` address from a possibly multi-recipient RFC 5322 header value."""
    if not raw or not (raw := raw.strip()):
        return None
    addrs = getaddresses([raw.replace("\n", " ")])
    for _name, addr in addrs:
        a = addr.strip()
        if a and "@" in a:
            return a
    return None


def _recipient_mailbox_for_browser_forms(headers: dict[str, str]) -> str | None:
    """Prefer ``Delivered-To`` (actual delivery) then ``To`` for ``type=email`` form prefills."""
    for key in ("Delivered-To", "To"):
        em = _mailbox_from_rfc5322_header_value(headers.get(key))
        if em:
            return em
    return None

_MAX_BODY_TEXT_CHARS = 500

# google-api-python-client service objects are not thread-safe; use one ``build()`` per thread.
_tls_gmail = threading.local()
_LIST_MESSAGES_MAX_WORKERS_CAP = 16


def _thread_local_gmail_service(credentials: Credentials) -> object:
    key = id(credentials)
    if getattr(_tls_gmail, "creds_key", None) != key:
        _tls_gmail.creds_key = key
        _tls_gmail.service = build(
            "gmail",
            "v1",
            credentials=credentials,
            cache_discovery=False,
        )
    return _tls_gmail.service


def _header_summary_from_get_api(get_api, list_item: dict) -> GmailHeaderSummary:
    """Build :class:`GmailHeaderSummary` using two ``messages().get`` calls (metadata + minimal)."""
    mid = list_item["id"]
    tid_hint = list_item.get("threadId", "")
    meta = get_api(
        userId="me",
        id=mid,
        format="metadata",
        metadataHeaders=list(_METADATA_HEADERS),
    ).execute()
    headers = {
        h["name"]: h["value"]
        for h in (meta.get("payload", {}).get("headers") or [])
    }
    hl = {k.strip().lower(): (v or "").strip() for k, v in headers.items()}
    minimal = get_api(
        userId="me",
        id=mid,
        format="minimal",
    ).execute()
    return GmailHeaderSummary(
        id=mid,
        thread_id=meta.get("threadId", tid_hint),
        from_=hl.get("from", headers.get("From", "")),
        subject=hl.get("subject", headers.get("Subject", "")),
        date=hl.get("date", headers.get("Date", "")),
        snippet=minimal.get("snippet", ""),
        list_unsubscribe=hl.get("list-unsubscribe") or None,
        list_unsubscribe_post=hl.get("list-unsubscribe-post") or None,
        delivered_to=_recipient_mailbox_for_browser_forms(headers),
        rfc_message_id=hl.get("message-id") or None,
    )


def _header_summary_from_list_item_threaded(
    credentials: Credentials, list_item: dict
) -> GmailHeaderSummary:
    service = _thread_local_gmail_service(credentials)
    get_api = service.users().messages().get
    return _header_summary_from_get_api(get_api, list_item)


def _urlsafe_b64decode(data: str) -> bytes:
    pad = (4 - len(data) % 4) % 4
    return base64.urlsafe_b64decode(data + ("=" * pad))


def _raw_from_part_body(part: dict) -> str | None:
    body = part.get("body") or {}
    raw = body.get("data")
    if not raw:
        return None
    try:
        return _urlsafe_b64decode(raw).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return None


def html_from_gmail_message_payload(payload: dict) -> str | None:
    """First ``text/html`` body in a Gmail API ``payload`` tree, or ``None``."""
    if (payload.get("mimeType") or "").lower() == "text/html":
        h = _raw_from_part_body(payload)
        if h:
            return h
    for part in payload.get("parts") or []:
        mt = (part.get("mimeType") or "").lower()
        if mt == "text/html":
            html = _raw_from_part_body(part)
            if html:
                return html
        nested = html_from_gmail_message_payload(part)
        if nested:
            return nested
    return None


def plaintext_from_gmail_message_payload(payload: dict) -> str | None:
    """First ``text/plain`` body in a Gmail API ``payload`` tree, or ``None``."""
    if (payload.get("mimeType") or "").lower() == "text/plain":
        t = _raw_from_part_body(payload)
        if t:
            return t
    for part in payload.get("parts") or []:
        mt = (part.get("mimeType") or "").lower()
        if mt == "text/plain":
            text = _raw_from_part_body(part)
            if text:
                return text
        nested = plaintext_from_gmail_message_payload(part)
        if nested:
            return nested
    return None


def _get_message_html_threaded(
    credentials: Credentials, message_id: str
) -> tuple[str, str]:
    """Fetch one message's HTML body (thread-local service).  Returns (message_id, html)."""
    service = _thread_local_gmail_service(credentials)
    full = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )
    payload = full.get("payload") or {}
    html = html_from_gmail_message_payload(payload)
    if not html:
        raise GmailTransportError(
            f"No text/html part in Gmail message {message_id!r}."
        )
    return message_id, html


def strip_html_to_text(html: str) -> str:
    """Best-effort HTML → single-line plain text (no external deps)."""
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class GmailApiBackend:
    """Gmail API backend: OAuth token file (read + send for digest self-email)."""

    def __init__(
        self,
        *,
        credentials: Credentials,
        list_messages_max_workers: int | None = None,
    ) -> None:
        self._credentials = credentials
        # None => min(inbox size, cap). Use ``1`` in tests with shared mocks. Real runs fan out.
        self._list_messages_max_workers = list_messages_max_workers

    @classmethod
    def from_token_path(cls, path: Path) -> GmailApiBackend:
        p = path.expanduser()
        if not p.is_file():
            raise ValueError(f"OAuth token path is not a file: {p}")
        creds = Credentials.from_authorized_user_file(str(p))
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    from google.auth.transport.requests import Request

                    creds.refresh(Request())
                    p.write_text(creds.to_json())
                except Exception as e:
                    raise ValueError(
                        f"Could not refresh OAuth token ({p}): {e}"
                    ) from e
            else:
                raise ValueError(
                    f"OAuth token missing or invalid ({p}); re-authorize with at least "
                    f"OAuth token missing or invalid ({p}); re-authorize with at least "
                    f"{_SCOPE_GMAIL_READONLY}."
                )
        return cls(credentials=creds)

    @classmethod
    def from_env(cls) -> GmailApiBackend:
        raw = os.environ.get(_ENV_OAUTH_TOKEN, "").strip()
        if not raw:
            raise ValueError(
                f"Set {_ENV_OAUTH_TOKEN} to the authorized-user JSON file from your OAuth flow "
                f"(must include {_SCOPE_GMAIL_READONLY})."
            )
        return cls.from_token_path(Path(raw))

    @staticmethod
    def regenerate_token(
        token_path: Path,
        *,
        client_secret_path: Path | None = None,
        scopes: tuple[str, ...] = _SCOPES,
        port: int = 0,
        browser: str | None = None,
    ) -> None:
        """Run OAuth installed-app flow and write a new token JSON file.

        Opens a browser for Google OAuth consent. The resulting token is written
        to *token_path*.

        Call after a refresh_token has been revoked (e.g. app in testing mode,
        too many outstanding tokens, or user revoked access).
        """
        from google_auth_oauthlib.flow import InstalledAppFlow

        p = token_path.expanduser()
        cs = (client_secret_path or Path.home() / ".google" / "client_secret.json").expanduser()
        if not cs.is_file():
            raise ValueError(f"Client secret file not found: {cs}")

        flow = InstalledAppFlow.from_client_secrets_file(str(cs), list(scopes))
        creds = flow.run_local_server(port=port, browser=browser)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(creds.to_json())

    def _service(self):
        return build("gmail", "v1", credentials=self._credentials, cache_discovery=False)

    def list_messages(self, query: str, *, max_results: int = 10) -> list[GmailHeaderSummary]:
        if max_results < 1:
            raise ValueError("max_results must be at least 1")
        try:
            service = self._service()
            list_resp = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
            raw_msgs = list_resp.get("messages") or []
            if not raw_msgs:
                return []

            n = len(raw_msgs)
            configured = self._list_messages_max_workers
            if configured is None:
                max_workers = min(_LIST_MESSAGES_MAX_WORKERS_CAP, n)
            else:
                max_workers = max(1, min(configured, n))

            # One worker: same-thread ``get`` calls (mock-friendly, low overhead for tiny scans).
            if max_workers == 1:
                get_api = service.users().messages().get
                return [_header_summary_from_get_api(get_api, m) for m in raw_msgs]

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                return list(
                    pool.map(
                        lambda item: _header_summary_from_list_item_threaded(
                            self._credentials, item
                        ),
                        raw_msgs,
                    )
                )
        except HttpError as e:
            raise GmailTransportError(f"Gmail API error: {e}") from e

    def get_message_html(self, message_id: str) -> str:
        """Fetch ``format=full`` and return the first ``text/html`` body."""
        try:
            service = self._service()
            full = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            payload = full.get("payload") or {}
            html = html_from_gmail_message_payload(payload)
            if not html:
                raise GmailTransportError(
                    f"No text/html part in Gmail message {message_id!r}."
                )
            return html
        except HttpError as e:
            raise GmailTransportError(f"Gmail API error: {e}") from e

    def get_message_body_text(self, message_id: str) -> str:
        """Plain text for previews: HTML stripped when present, else first ``text/plain``."""
        try:
            service = self._service()
            full = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            payload = full.get("payload") or {}
            html = html_from_gmail_message_payload(payload)
            if html:
                text = strip_html_to_text(html)
            else:
                text = (plaintext_from_gmail_message_payload(payload) or "").strip()
            if len(text) > _MAX_BODY_TEXT_CHARS:
                text = text[:_MAX_BODY_TEXT_CHARS]
            return text
        except HttpError as e:
            raise GmailTransportError(f"Gmail API error: {e}") from e

    def get_message_html_bulk(
        self,
        message_ids: list[str],
        *,
        max_workers: int | None = None,
    ) -> dict[str, str]:
        """Fetch full HTML for multiple messages in parallel with thread-local clients.

        Returns ``{message_id: html_body}``.  Missing-HTML messages raise
        ``GmailTransportError`` (error-tolerant callers should catch per-message).

        *max_workers*: ``None`` auto-scales (capped), ``1`` uses same-thread
        (mock-friendly for tests / tiny scans).
        """
        if not message_ids:
            return {}
        n = len(message_ids)
        configured = max_workers
        if configured is None:
            mw = min(_LIST_MESSAGES_MAX_WORKERS_CAP, n)
        else:
            mw = max(1, min(configured, n))
        if mw == 1:
            return {mid: self.get_message_html(mid) for mid in message_ids}
        with ThreadPoolExecutor(max_workers=mw) as pool:
            pairs = list(
                pool.map(
                    lambda mid: _get_message_html_threaded(
                        self._credentials, mid
                    ),
                    message_ids,
                )
            )
        return dict(pairs)

    def get_profile_email(self) -> str:
        """Authenticated account address (``users.getProfile``)."""
        try:
            service = self._service()
            prof = service.users().getProfile(userId="me").execute()
            email = (prof or {}).get("emailAddress")
            if not isinstance(email, str) or not email.strip():
                raise GmailTransportError(
                    "Gmail getProfile response missing emailAddress."
                )
            return email.strip()
        except HttpError as e:
            raise GmailTransportError(f"Gmail API error: {e}") from e

    def send_html_email(self, *, to: str, subject: str, html: str) -> None:
        """Send a new message via ``users.messages.send`` (RFC822 built locally)."""
        from email.message import EmailMessage

        if _SCOPE_GMAIL_SEND not in self._credentials.scopes:
            raise ValueError(
                f"gmail.send scope required for sending; token has {self._credentials.scopes}. "
                "Re-authorize with gmail.send scope or set output.also_email_to to null."
            )

        from_addr = self.get_profile_email()
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to
        msg.set_content(
            "This digest is HTML; use an HTML-capable mail client.\n",
            subtype="plain",
            charset="utf-8",
        )
        msg.add_alternative(html, subtype="html", charset="utf-8")
        raw_bytes = msg.as_bytes()
        raw = base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")
        try:
            service = self._service()
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
        except HttpError as e:
            raise GmailTransportError(f"Gmail API error: {e}") from e

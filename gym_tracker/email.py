"""Outbound invite email behind a thin, swappable transport.

Public surface:
    send_invite_email(to_email, confirm_url, *, to_name=None) -> None
    EmailSendError

When ``EMAIL_ENABLED`` is false (the default) no HTTP call is made; the
confirm URL is logged at INFO so local/dev/test flows can complete manually.
Only the Resend transport is implemented today; adding another provider is a
new ``_send_via_*`` function plus an ``EMAIL_PROVIDER`` branch here, nothing
elsewhere.
"""
import logging
from typing import Optional, Tuple

import httpx
from markupsafe import escape

from gym_tracker.config import Settings

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"


class EmailSendError(Exception):
    """The invite email could not be handed off to the provider."""


def _render(confirm_url: str, to_name: Optional[str]) -> Tuple[str, str, str]:
    subject = "You're invited to Gym Tracker"

    # Plain-text part: no markup context, use the raw values.
    text_greeting = f"Hi {to_name}," if to_name else "Hi,"
    text = (
        f"{text_greeting}\n\n"
        "An administrator has invited you to Gym Tracker. Confirm your account "
        "using the link below, then sign in with your Google account:\n\n"
        f"{confirm_url}\n\n"
        "If you weren't expecting this, you can ignore this email."
    )

    # HTML part: every interpolated value is attacker-influenced (client name,
    # request-derived base URL) -> escape for both text and attribute context.
    safe_url = escape(confirm_url)
    html_greeting = f"Hi {escape(to_name)}," if to_name else "Hi,"
    html = (
        f"<p>{html_greeting}</p>"
        "<p>An administrator has invited you to Gym Tracker. Confirm your "
        "account, then sign in with your Google account:</p>"
        f'<p><a href="{safe_url}">Confirm my account</a></p>'
        f"<p>Or paste this link into your browser:<br>{safe_url}</p>"
        "<p>If you weren't expecting this, you can ignore this email.</p>"
    )
    return subject, text, html


def send_invite_email(to_email: str, confirm_url: str, *, to_name: Optional[str] = None) -> None:
    settings = Settings()
    subject, text, html = _render(confirm_url, to_name)

    if not settings.EMAIL_ENABLED:
        logger.info("invite email (disabled) for %s: %s", to_email, confirm_url)
        return

    provider = (settings.EMAIL_PROVIDER or "").strip().lower()
    if provider == "resend":
        _send_via_resend(settings, to_email, subject, text, html)
        return
    raise EmailSendError(f"unsupported EMAIL_PROVIDER: {settings.EMAIL_PROVIDER!r}")


def _send_via_resend(settings: Settings, to_email: str, subject: str, text: str, html: str) -> None:
    try:
        resp = httpx.post(
            RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.EMAIL_FROM,
                "to": [to_email],
                "reply_to": settings.EMAIL_REPLY_TO,
                "subject": subject,
                "text": text,
                "html": html,
            },
            timeout=10,
        )
    except httpx.HTTPError as exc:  # transport failure (connect/read/timeout)
        raise EmailSendError(f"invite email transport error: {exc}") from exc

    if not 200 <= resp.status_code < 300:
        raise EmailSendError(f"invite email rejected by provider (HTTP {resp.status_code})")

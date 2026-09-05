"""Email config settings, invite-token helpers, and send_invite_email transport."""
import pytest


# ---------------------------------------------------------------------------
# Task 2 — email configuration settings
# ---------------------------------------------------------------------------

def test_email_settings_defaults(monkeypatch):
    for k in ["EMAIL_ENABLED", "EMAIL_PROVIDER", "RESEND_API_KEY",
              "EMAIL_FROM", "EMAIL_REPLY_TO", "APP_BASE_URL"]:
        monkeypatch.delenv(k, raising=False)
    from gym_tracker.config import Settings
    s = Settings()
    assert s.EMAIL_ENABLED is False
    assert s.EMAIL_PROVIDER == "resend"
    assert s.RESEND_API_KEY == ""
    assert s.EMAIL_FROM == "Gym Tracker <admin@gym.x-mas.ro>"
    assert s.EMAIL_REPLY_TO == "dumitru@x-mas.ro"
    assert s.APP_BASE_URL == ""


def test_email_enabled_truthy(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    from gym_tracker.config import Settings
    assert Settings().EMAIL_ENABLED is True


def test_email_enabled_falsey(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "0")
    from gym_tracker.config import Settings
    assert Settings().EMAIL_ENABLED is False

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


# ---------------------------------------------------------------------------
# Task 3 — token helpers
# ---------------------------------------------------------------------------

def test_hash_token_is_sha256_hex():
    from gym_tracker.invites import hash_token
    assert hash_token("abc") == \
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_generate_token_unique_and_urlsafe():
    from gym_tracker.invites import generate_token
    a, b = generate_token(), generate_token()
    assert a != b and len(a) >= 32


# ---------------------------------------------------------------------------
# Task 3 — send_invite_email
# ---------------------------------------------------------------------------

def test_disabled_logs_url_no_http(monkeypatch, caplog):
    monkeypatch.setenv("EMAIL_ENABLED", "false")
    import httpx

    def boom(*a, **k):
        raise AssertionError("no HTTP when disabled")

    monkeypatch.setattr(httpx, "post", boom, raising=False)
    from gym_tracker.email import send_invite_email
    with caplog.at_level("INFO"):
        send_invite_email("c@example.com", "https://h/invite/confirm?token=RAW")
    assert "https://h/invite/confirm?token=RAW" in caplog.text


def test_enabled_posts_to_resend(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    calls = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"id": "e1"}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.update(url=url, headers=headers, json=json)
        return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    from gym_tracker.email import send_invite_email
    send_invite_email("c@example.com", "https://h/x?token=RAW", to_name="Cee")
    assert calls["url"] == "https://api.resend.com/emails"
    assert calls["headers"]["Authorization"] == "Bearer re_test"
    body = calls["json"]
    assert body["from"] == "Gym Tracker <admin@gym.x-mas.ro>"
    assert body["reply_to"] == "dumitru@x-mas.ro"
    assert body["to"] == ["c@example.com"]
    assert "https://h/x?token=RAW" in body["text"]
    assert "https://h/x?token=RAW" in body["html"]
    assert body["subject"]


def test_enabled_non_2xx_raises(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")

    class FakeResp:
        status_code = 422
        text = "bad"

        def json(self):
            return {"message": "bad"}

    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
    from gym_tracker.email import send_invite_email, EmailSendError
    with pytest.raises(EmailSendError):
        send_invite_email("c@example.com", "https://h/x?token=RAW")


def test_enabled_transport_error_raises(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")

    import httpx

    def raiser(*a, **k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "post", raiser)
    from gym_tracker.email import send_invite_email, EmailSendError
    with pytest.raises(EmailSendError):
        send_invite_email("c@example.com", "https://h/x?token=RAW")


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("EMAIL_PROVIDER", "carrier-pigeon")
    from gym_tracker.email import send_invite_email, EmailSendError
    with pytest.raises(EmailSendError):
        send_invite_email("c@example.com", "https://h/x?token=RAW")

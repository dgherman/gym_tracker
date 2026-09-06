"""Item 1 — History / Reports top nav: a top 'Home' link, no bottom 'Back',
and History drops its page-local navbar-brand."""
import os


def _login(c, email="owner@x.com"):
    os.environ["DEV_LOGIN_EMAIL"] = email
    r = c.get("/dev/login", follow_redirects=False)
    assert r.status_code in (200, 302, 303, 307), r.text


def test_history_has_top_home_link_no_back_no_brand(couples):
    c = couples
    _login(c)
    body = c.get("/history").text
    assert 'href="/"' in body
    assert ">Home</a>" in body
    assert body.index(">Home</a>") < body.index("<h1")
    assert ">Back</a>" not in body
    assert "navbar-brand" not in body


def test_reports_has_top_home_link_no_back(couples):
    c = couples
    _login(c)
    body = c.get("/reports").text
    assert 'href="/"' in body
    assert ">Home</a>" in body
    assert body.index(">Home</a>") < body.index("<h1")
    assert ">Back</a>" not in body

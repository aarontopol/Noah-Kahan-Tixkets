"""Tests for the TextBelt URL-block fallback (unverified accounts reject links)."""
from monitor.notifier import TextBeltNotifier, strip_urls


def test_strip_urls_removes_links_keeps_content():
    msg = ("🎫 Noah Kahan Aug 8 @ Coors Field: 2 seat(s) under target!\n"
           "• Sec 120 Row 3 x6 @ $289/ea (mock)\n"
           "https://example.com/listing/mk-2")
    out = strip_urls(msg)
    assert "https://" not in out
    assert "Sec 120 Row 3" in out
    assert out.splitlines()[-1].startswith("• Sec 120")  # empty link line dropped


def test_url_blocked_send_retries_without_links(monkeypatch):
    calls = []

    class FakeResp:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    def fake_post(url, data=None, timeout=None):
        calls.append(data["message"])
        if "http" in data["message"]:
            return FakeResp({"success": False,
                             "error": "Sorry, ability to send URLs via text is limited to verified accounts."})
        return FakeResp({"success": True, "quotaRemaining": 42})

    monkeypatch.setattr("monitor.notifier.requests.post", fake_post)
    notifier = TextBeltNotifier("key", "+14044443292")
    ok = notifier.send("Seats found!\nhttps://example.com/x")

    assert ok is True
    assert len(calls) == 2
    assert "http" in calls[0] and "http" not in calls[1]


def test_other_errors_do_not_retry(monkeypatch):
    calls = []

    class FakeResp:
        def json(self):
            return {"success": False, "error": "Out of quota"}

    def fake_post(url, data=None, timeout=None):
        calls.append(data["message"])
        return FakeResp()

    monkeypatch.setattr("monitor.notifier.requests.post", fake_post)
    ok = TextBeltNotifier("key", "+14044443292").send("Seats!\nhttps://example.com/x")
    assert ok is False
    assert len(calls) == 1


def test_no_retry_when_message_has_no_links(monkeypatch):
    calls = []

    class FakeResp:
        def json(self):
            return {"success": False,
                    "error": "Sorry, ability to send URLs via text is limited to verified accounts."}

    def fake_post(url, data=None, timeout=None):
        calls.append(data["message"])
        return FakeResp()

    monkeypatch.setattr("monitor.notifier.requests.post", fake_post)
    ok = TextBeltNotifier("key", "+14044443292").send("plain message, no links")
    assert ok is False
    assert len(calls) == 1  # nothing to strip, so no pointless retry

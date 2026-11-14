from datetime import datetime, timedelta, timezone

from app.channels.window import outbound_mode, within_service_window

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def test_window_edges():
    assert within_service_window(NOW - timedelta(hours=23, minutes=59), NOW)
    assert not within_service_window(NOW - timedelta(hours=24), NOW)
    assert not within_service_window(None, NOW)


def test_replies_and_scheduled_messages_choose_the_right_mode():
    recent = NOW - timedelta(hours=1)
    stale = NOW - timedelta(hours=30)
    assert outbound_mode("whatsapp", "reply", recent, NOW) == "text"
    assert outbound_mode("whatsapp", "reply", stale, NOW) == "template"
    assert outbound_mode("whatsapp", "proactive", recent, NOW) == "template"
    assert outbound_mode("instagram", "reply", recent, NOW) == "text"
    assert outbound_mode("instagram", "reply", stale, NOW) == "skip"
    assert outbound_mode("instagram", "proactive", recent, NOW) == "skip"

"""24-hour customer-service window and outbound mode."""

from datetime import datetime, timedelta, timezone

WINDOW = timedelta(hours=24)

TEMPLATE_NAMES = {
    "morning": "smartstock_morning_briefing",
    "evening": "smartstock_evening_checkin",
    "weekly": "smartstock_weekly_wrap",
    "reply": "smartstock_reply",
}


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def within_service_window(last_inbound: datetime | None, now: datetime) -> bool:
    if last_inbound is None:
        return False
    return (_aware(now) - _aware(last_inbound)) < WINDOW


def outbound_mode(channel: str, kind: str, last_inbound: datetime | None, now: datetime) -> str:
    """Return text, template, or skip.

    Scheduled messages always use a WhatsApp template. Instagram has no
    template path here, so proactive Instagram sends are skipped.
    """
    if channel == "instagram":
        if kind == "proactive":
            return "skip"
        if within_service_window(last_inbound, now):
            return "text"
        return "skip"
    if kind == "proactive":
        return "template"
    if within_service_window(last_inbound, now):
        return "text"
    return "template"

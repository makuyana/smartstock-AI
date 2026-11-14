"""Shop time is always Africa/Johannesburg."""

from datetime import datetime
from zoneinfo import ZoneInfo

JOHANNESBURG = ZoneInfo("Africa/Johannesburg")


def jhb_now() -> datetime:
    return datetime.now(JOHANNESBURG)

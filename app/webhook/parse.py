"""Turn Meta webhook JSON into a list of inbound events."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class InboundEvent:
    channel: str
    external_id: str
    sender_id: str
    sender_name: str | None
    sent_at: datetime
    type: str
    text: str | None = None
    media_id: str | None = None
    media_url: str | None = None
    mime_type: str | None = None
    caption: str | None = None
    raw: dict = field(default_factory=dict)


def parse_webhook(payload: dict) -> list[InboundEvent]:
    if not isinstance(payload, dict):
        return []
    obj = payload.get("object")
    events: list[InboundEvent] = []
    if obj == "whatsapp_business_account":
        events.extend(_whatsapp(payload))
    elif obj in {"instagram", "page"}:
        events.extend(_instagram(payload))
    return _dedupe(events)


def _dedupe(events: list[InboundEvent]) -> list[InboundEvent]:
    seen: set[tuple[str, str]] = set()
    kept: list[InboundEvent] = []
    for event in events:
        key = (event.channel, event.external_id)
        if key in seen:
            continue
        seen.add(key)
        kept.append(event)
    return kept


def _from_unix(value) -> datetime:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    if number > 10_000_000_000:
        number = number / 1000
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _whatsapp(payload: dict) -> list[InboundEvent]:
    events: list[InboundEvent] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {
                contact.get("wa_id"): (contact.get("profile") or {}).get("name")
                for contact in value.get("contacts") or []
                if contact.get("wa_id")
            }
            for message in value.get("messages") or []:
                event = _whatsapp_message(message, names)
                if event is not None:
                    events.append(event)
    return events


def _whatsapp_message(message: dict, names: dict) -> InboundEvent | None:
    external_id = message.get("id")
    sender = message.get("from")
    if not external_id or not sender:
        return None
    kind = message.get("type") or "other"
    text = None
    caption = None
    media_id = None
    mime = None
    event_type = "other"
    if kind == "text":
        event_type = "text"
        text = (message.get("text") or {}).get("body")
    elif kind == "image":
        event_type = "image"
        image = message.get("image") or {}
        media_id = image.get("id")
        mime = image.get("mime_type")
        caption = image.get("caption")
    elif kind in {"audio", "voice"}:
        event_type = "audio"
        audio = message.get("audio") or message.get("voice") or {}
        media_id = audio.get("id")
        mime = audio.get("mime_type")
    elif kind == "button":
        event_type = "text"
        text = (message.get("button") or {}).get("text")
    elif kind == "interactive":
        event_type = "text"
        interactive = message.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        text = reply.get("title")
    elif kind == "document":
        event_type = "image"
        document = message.get("document") or {}
        media_id = document.get("id")
        mime = document.get("mime_type")
        caption = document.get("caption")
    return InboundEvent(
        channel="whatsapp",
        external_id=str(external_id),
        sender_id=str(sender),
        sender_name=names.get(sender),
        sent_at=_from_unix(message.get("timestamp")),
        type=event_type,
        text=text,
        media_id=media_id,
        mime_type=mime,
        caption=caption,
        raw=message,
    )


def _instagram(payload: dict) -> list[InboundEvent]:
    events: list[InboundEvent] = []
    for entry in payload.get("entry") or []:
        for item in entry.get("messaging") or []:
            event = _instagram_messaging(item)
            if event is not None:
                events.append(event)
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            if "message" in value or "sender" in value:
                event = _instagram_messaging(value)
                if event is not None:
                    events.append(event)
    return events


def _instagram_messaging(item: dict) -> InboundEvent | None:
    message = item.get("message") or {}
    if message.get("is_echo"):
        return None
    external_id = message.get("mid") or item.get("mid")
    sender = (item.get("sender") or {}).get("id")
    if not external_id or not sender:
        return None
    text = message.get("text")
    media_url = None
    mime = None
    event_type = "text" if text else "other"
    attachments = message.get("attachments") or []
    if attachments:
        first = attachments[0] or {}
        attach_type = first.get("type") or "other"
        payload = first.get("payload") or {}
        media_url = payload.get("url")
        if attach_type == "image":
            event_type = "image"
        elif attach_type == "audio":
            event_type = "audio"
        else:
            event_type = "other"
        mime = payload.get("mime_type")
    if text and event_type == "other":
        event_type = "text"
    return InboundEvent(
        channel="instagram",
        external_id=str(external_id),
        sender_id=str(sender),
        sender_name=None,
        sent_at=_from_unix(item.get("timestamp")),
        type=event_type,
        text=text,
        media_url=media_url,
        mime_type=mime,
        caption=text if event_type in {"image", "audio"} else None,
        raw=item,
    )

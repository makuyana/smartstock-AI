"""Send WhatsApp Cloud API and Instagram messages."""

import logging

import httpx

from app.channels.window import TEMPLATE_NAMES

logger = logging.getLogger("smartstock.meta")


class MetaError(RuntimeError):
    pass


class MetaClient:
    def __init__(self, settings, http: httpx.AsyncClient):
        self.settings = settings
        self.http = http

    def _url(self, base: str, account_id: str) -> str:
        return (
            f"{base.rstrip('/')}/{self.settings.graph_api_version}/{account_id}/messages"
        )

    async def send_whatsapp_text(self, to: str, body: str) -> dict:
        if not self.settings.whatsapp_token or not self.settings.whatsapp_phone_number_id:
            raise MetaError("WhatsApp token or phone number id is missing")
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": body[:4096]},
        }
        return await self._post(
            self._url(self.settings.graph_api_base_url, self.settings.whatsapp_phone_number_id),
            payload,
            self.settings.whatsapp_token,
        )

    async def send_whatsapp_template(self, to: str, template_name: str, body_param: str) -> dict:
        if not self.settings.whatsapp_token or not self.settings.whatsapp_phone_number_id:
            raise MetaError("WhatsApp token or phone number id is missing")
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "en"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": _template_text(body_param)}],
                    }
                ],
            },
        }
        return await self._post(
            self._url(self.settings.graph_api_base_url, self.settings.whatsapp_phone_number_id),
            payload,
            self.settings.whatsapp_token,
        )

    async def send_instagram_text(self, to: str, body: str) -> dict:
        if not self.settings.instagram_token or not self.settings.instagram_account_id:
            raise MetaError("Instagram token or account id is missing")
        payload = {"recipient": {"id": to}, "message": {"text": body[:1000]}}
        if "graph.facebook.com" in self.settings.instagram_graph_base_url:
            payload["messaging_type"] = "RESPONSE"
        return await self._post(
            self._url(self.settings.instagram_graph_base_url, self.settings.instagram_account_id),
            payload,
            self.settings.instagram_token,
        )

    async def download_whatsapp_media(self, media_id: str) -> tuple[bytes, str] | None:
        if not self.settings.whatsapp_token:
            return None
        meta_url = (
            f"{self.settings.graph_api_base_url.rstrip('/')}/"
            f"{self.settings.graph_api_version}/{media_id}"
        )
        headers = {"Authorization": f"Bearer {self.settings.whatsapp_token}"}
        try:
            meta = await self.http.get(meta_url, headers=headers)
        except httpx.HTTPError:
            logger.exception("media lookup failed")
            return None
        if meta.status_code >= 300:
            return None
        info = meta.json()
        file_url = info.get("url")
        if not file_url:
            return None
        try:
            downloaded = await self.http.get(file_url, headers=headers)
        except httpx.HTTPError:
            logger.exception("media download failed")
            return None
        if downloaded.status_code >= 300:
            return None
        return downloaded.content, info.get("mime_type") or "application/octet-stream"

    async def _post(self, url: str, payload: dict, token: str) -> dict:
        try:
            response = await self.http.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise MetaError("outbound request failed") from exc
        if response.status_code >= 300:
            logger.warning("meta send status %s", response.status_code)
            raise MetaError(f"send failed with status {response.status_code}")
        data = response.json()
        logger.info("meta send ok path=%s", url.rsplit("/", 2)[-2] + "/messages")
        return data


def provider_message_id(raw: dict) -> str | None:
    messages = raw.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        return messages[0].get("id")
    if raw.get("message_id"):
        return str(raw["message_id"])
    return None


def _template_text(text: str) -> str:
    import re

    flat = re.sub(r"\s+", " ", text).strip()
    return flat[:900]


def template_for(slot: str) -> str:
    return TEMPLATE_NAMES[slot]

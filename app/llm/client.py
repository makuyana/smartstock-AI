"""LLM calls. Any OpenAI-compatible base URL works, including xAI (https://api.x.ai/v1)."""

import json
from dataclasses import dataclass, field

import httpx


class LLMError(RuntimeError):
    pass


class LLMNotConfigured(LLMError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient:
    def __init__(self, settings, http: httpx.AsyncClient):
        self.settings = settings
        self.http = http

    @property
    def configured(self) -> bool:
        return bool(self.settings.llm_api_key)

    async def chat(self, messages, tools=None, tool_choice=None) -> LLMResponse:
        if not self.configured:
            raise LLMNotConfigured("LLM_API_KEY is empty")
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        payload: dict = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": 0,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        response = await self.http.post(
            url,
            headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
            json=payload,
        )
        if response.status_code >= 300:
            raise LLMError(f"LLM request failed with status {response.status_code}")
        message = response.json()["choices"][0]["message"]
        calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                arguments = json.loads(arguments or "{}")
            calls.append(
                ToolCall(
                    id=str(call.get("id") or ""),
                    name=str(function.get("name") or ""),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        return LLMResponse(content=_message_text(message.get("content")), tool_calls=calls)


def _message_text(content) -> str | None:
    """xAI and OpenAI both return a string. Some replies use a list of text parts."""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("text"):
                parts.append(str(part["text"]))
        text = "\n".join(part for part in parts if part).strip()
        return text or None
    return str(content)

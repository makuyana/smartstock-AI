"""LLM calls. The provider is whatever OpenAI-compatible base URL is configured."""

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
        return LLMResponse(content=message.get("content"), tool_calls=calls)

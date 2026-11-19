import asyncio
from pathlib import Path

from app.config import Settings
from app.llm.client import LLMClient


class Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class HTTP:
    def __init__(self, payload):
        self.payload = payload
        self.url = None
        self.headers = None
        self.json = None

    async def post(self, url, headers, json):
        self.url = url
        self.headers = headers
        self.json = json
        return Response(self.payload)


def test_defaults_point_at_xai_grok_and_the_key_is_not_in_source():
    settings = Settings(llm_api_key="")
    assert settings.llm_base_url == "https://api.x.ai/v1"
    assert settings.llm_model == "grok-4"
    source = Path("app/llm/client.py").read_text()
    assert "sk-" not in source
    assert "xai-" not in source
    render = Path("render.yaml").read_text()
    assert "key: LLM_API_KEY\n        sync: false" in render
    assert "https://api.x.ai/v1" in render
    assert "grok-4" in render
    example = Path(".env.example").read_text()
    assert "LLM_API_KEY=" in example
    assert "https://api.x.ai/v1" in example
    assert not any(line.startswith("LLM_API_KEY=") and len(line) > len("LLM_API_KEY=") for line in example.splitlines())


def test_client_posts_to_the_xai_chat_completions_endpoint():
    http = HTTP(
        {
            "choices": [
                {
                    "message": {
                        "content": [{"type": "text", "text": "Sharp."}],
                        "tool_calls": None,
                    }
                }
            ]
        }
    )
    settings = Settings(
        llm_api_key="test-key-not-real",
        llm_base_url="https://api.x.ai/v1/",
        llm_model="grok-4",
    )
    client = LLMClient(settings, http)
    reply = asyncio.run(
        client.chat(
            [{"role": "user", "content": "stock"}],
            tools=[{"type": "function", "function": {"name": "get_stock", "parameters": {}}}],
        )
    )
    assert http.url == "https://api.x.ai/v1/chat/completions"
    assert http.json["model"] == "grok-4"
    assert http.headers["Authorization"] == "Bearer test-key-not-real"
    assert "test-key-not-real" not in str(http.json)
    assert reply.content == "Sharp."
    assert reply.tool_calls == []

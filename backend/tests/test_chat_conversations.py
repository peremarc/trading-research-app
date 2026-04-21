from __future__ import annotations

import json

from app.core.config import Settings
from app.domains.learning import api as learning_api
from app.domains.learning.conversations import ChatConversationService


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_chat_conversation_crud_and_archiving(client) -> None:
    created = client.post(
        "/api/v1/chat/conversations",
        json={
            "title": "MSTR y mNAV",
            "labels": ["MSTR", "idea"],
            "preferred_llm": "qwen2.5",
        },
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["title"] == "MSTR y mNAV"
    assert payload["preferred_llm"] == "qwen2.5"
    assert sorted(payload["labels"]) == ["idea", "mstr"]

    listing = client.get("/api/v1/chat/conversations")
    assert listing.status_code == 200
    assert listing.json()[0]["id"] == payload["id"]

    updated = client.request(
        "PATCH",
        f"/api/v1/chat/conversations/{payload['id']}",
        json={"preferred_llm": "gemini-2.5-flash", "labels": ["macro", "debate"]},
    )
    assert updated.status_code == 200
    assert updated.json()["preferred_llm"] == "gemini-2.5-flash"
    assert sorted(updated.json()["labels"]) == ["debate", "macro"]

    archived = client.post(f"/api/v1/chat/conversations/{payload['id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    archived_list = client.get("/api/v1/chat/conversations?include_archived=true")
    assert archived_list.status_code == 200
    assert archived_list.json()[0]["status"] == "archived"


def test_chat_presets_endpoint_marks_unconfigured_models_not_ready(client) -> None:
    response = client.get("/api/v1/chat/presets")
    assert response.status_code == 200
    preset_map = {item["key"]: item for item in response.json()}
    assert preset_map["gemini-2.5-flash"]["ready"] is False
    assert preset_map["codex-gateway"]["ready"] is False
    assert preset_map["qwen2.5"]["ready"] is False
    assert preset_map["gpt-5.4 xhigh"]["ready"] is False


def test_chat_message_persists_thread_memory_and_research(client) -> None:
    created = client.post("/api/v1/chat/conversations", json={"preferred_llm": "gemini-2.5-flash"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    turn = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={
            "content": "Creo que NVDA merece research porque la tesis parece interesante pero falta validar earnings y volumen",
            "message_type": "idea_discussion",
        },
    )
    assert turn.status_code == 201
    payload = turn.json()
    actions = payload["assistant_message"]["actions_taken"]
    action_names = {item["action"] for item in actions}
    assert "memory_saved" in action_names
    assert "research_task_created" in action_names
    assert payload["assistant_message"]["context"]["requested_llm"] == "gemini-2.5-flash"
    assert payload["assistant_message"]["context"]["fallback_used"] is True
    assert payload["assistant_message"]["context"]["used_provider"] == "local_rules"

    detail = client.get(f"/api/v1/chat/conversations/{conversation_id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert len(detail_payload["messages"]) == 2
    assert detail_payload["messages"][0]["role"] == "user"
    assert detail_payload["messages"][1]["role"] == "assistant"

    research = client.get("/api/v1/research/tasks")
    assert research.status_code == 200
    assert any("NVDA" in item["title"] for item in research.json())

    memory = client.get("/api/v1/memory")
    assert memory.status_code == 200
    assert any(item["memory_type"] == "chat_insight" for item in memory.json())


def test_chat_ticker_review_returns_structured_bias(client) -> None:
    created = client.post("/api/v1/chat/conversations", json={"preferred_llm": "gemini-2.5-flash"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    turn = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": "Que opinas de NVDA?", "message_type": "ticker_review"},
    )
    assert turn.status_code == 201
    payload = turn.json()
    assistant = payload["assistant_message"]
    assert assistant["message_type"] == "ticker_review"
    assert assistant["context"]["topic"] == "ticker_review"
    assert assistant["context"]["analysis"]["bias"] in {
        "bullish",
        "neutral",
        "bearish",
        "too_early",
        "insufficient_evidence",
    }
    assert "Sesgo actual:" in assistant["content"]


def test_chat_gemini_preset_is_ready_and_persists_used_model(client, monkeypatch) -> None:
    settings = Settings(
        gemini_api_key="gem-test",
        chat_llm_default="gemini-2.5-flash",
    )

    def fake_urlopen(request, timeout=0):
        assert "generateContent" in request.full_url
        return _FakeHTTPResponse({"candidates": [{"content": {"parts": [{"text": '{"reply":"Respuesta Gemini"}'}]}}]})

    monkeypatch.setattr("app.providers.llm.urlopen", fake_urlopen)
    learning_api.chat_conversation_service = ChatConversationService(settings=settings)

    presets = client.get("/api/v1/chat/presets")
    assert presets.status_code == 200
    preset_map = {item["key"]: item for item in presets.json()}
    assert preset_map["gemini-2.5-flash"]["ready"] is True

    created = client.post("/api/v1/chat/conversations", json={"preferred_llm": "gemini-2.5-flash"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    turn = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": "Idea sobre AAPL con riesgo controlado", "message_type": "idea_discussion"},
    )
    assert turn.status_code == 201
    assistant = turn.json()["assistant_message"]
    assert assistant["content"] == "Respuesta Gemini"
    assert assistant["context"]["used_provider"] == "gemini"
    assert assistant["context"]["used_model"] == "gemini-2.5-flash"
    assert assistant["context"]["fallback_used"] is False


def test_chat_qwen_preset_uses_openai_compatible_mapping(client, monkeypatch) -> None:
    captured: dict = {}
    settings = Settings(
        ai_fallback_model="qwen2.5:3b",
        ai_fallback_api_base="https://qwen.local/v1",
        ai_fallback_api_key="qwen-key",
        chat_llm_default="qwen2.5",
    )

    def fake_urlopen(request, timeout=0):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse({"choices": [{"message": {"content": '{"reply":"Respuesta Qwen"}'}}]})

    monkeypatch.setattr("app.providers.llm.urlopen", fake_urlopen)
    learning_api.chat_conversation_service = ChatConversationService(settings=settings)

    created = client.post("/api/v1/chat/conversations", json={"preferred_llm": "qwen2.5"})
    assert created.status_code == 201

    turn = client.post(
        f"/api/v1/chat/conversations/{created.json()['id']}/messages",
        json={"content": "Idea de mercado sobre META", "message_type": "idea_discussion"},
    )
    assert turn.status_code == 201
    assert captured["payload"]["model"] == "qwen2.5:3b"
    assert "reasoning_effort" not in captured["payload"]
    assistant = turn.json()["assistant_message"]
    assert assistant["context"]["used_provider"] == "openai_compatible"
    assert assistant["context"]["used_model"] == "qwen2.5:3b"


def test_chat_gpt54_xhigh_propagates_reasoning_effort(client, monkeypatch) -> None:
    captured: dict = {}
    settings = Settings(
        chat_gpt54_model="gpt-5.4",
        chat_gpt54_api_base="https://openai.local/v1",
        chat_gpt54_api_key="gpt-key",
        chat_gpt54_reasoning_effort="xhigh",
        chat_llm_default="gpt-5.4 xhigh",
    )

    def fake_urlopen(request, timeout=0):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse({"choices": [{"message": {"content": '{"reply":"Respuesta GPT"}'}}]})

    monkeypatch.setattr("app.providers.llm.urlopen", fake_urlopen)
    learning_api.chat_conversation_service = ChatConversationService(settings=settings)

    created = client.post("/api/v1/chat/conversations", json={"preferred_llm": "gpt-5.4 xhigh"})
    assert created.status_code == 201

    turn = client.post(
        f"/api/v1/chat/conversations/{created.json()['id']}/messages",
        json={"content": "Que opinas de MSTR?", "message_type": "ticker_review"},
    )
    assert turn.status_code == 201
    assert captured["payload"]["model"] == "gpt-5.4"
    assert captured["payload"]["reasoning_effort"] == "xhigh"
    assistant = turn.json()["assistant_message"]
    assert assistant["context"]["used_provider"] == "openai_compatible"
    assert assistant["context"]["used_model"] == "gpt-5.4"
    assert assistant["context"]["reasoning_effort"] == "xhigh"
    assert assistant["context"]["fallback_used"] is False


def test_chat_codex_gateway_preset_uses_gateway_adapter(client, monkeypatch) -> None:
    captured: dict = {}
    settings = Settings(
        codex_gateway_base_url="https://dev-codex-gateway.peremarc.com",
        codex_gateway_api_key="codex-gateway",
        codex_gateway_model_label="gateway-label",
        codex_gateway_codex_model="gpt-5.3-codex-spark",
        chat_llm_default="codex-gateway",
    )

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse({"choices": [{"message": {"content": '{"reply":"Respuesta Codex Gateway"}'}}]})

    monkeypatch.setattr("app.providers.llm.urlopen", fake_urlopen)
    learning_api.chat_conversation_service = ChatConversationService(settings=settings)

    presets = client.get("/api/v1/chat/presets")
    assert presets.status_code == 200
    preset_map = {item["key"]: item for item in presets.json()}
    assert preset_map["codex-gateway"]["ready"] is True

    created = client.post("/api/v1/chat/conversations", json={"preferred_llm": "codex-gateway"})
    assert created.status_code == 201

    turn = client.post(
        f"/api/v1/chat/conversations/{created.json()['id']}/messages",
        json={"content": "Que opinas de BTC proxy vs MSTR?", "message_type": "ticker_review"},
    )
    assert turn.status_code == 201
    assert captured["url"] == "https://dev-codex-gateway.peremarc.com/v1/chat/completions"
    assert captured["payload"]["model"] == "gateway-label"
    assert captured["payload"]["codex_model"] == "gpt-5.3-codex-spark"
    assistant = turn.json()["assistant_message"]
    assert assistant["content"] == "Respuesta Codex Gateway"
    assert assistant["context"]["used_provider"] == "codex_gateway"
    assert assistant["context"]["used_model"] == "gateway-label"

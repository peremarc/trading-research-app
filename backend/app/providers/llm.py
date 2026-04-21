from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMProviderError(RuntimeError):
    pass


class JSONDecisionProvider(Protocol):
    provider_name: str
    model_name: str | None

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_json_schema: dict | None = None,
    ) -> dict: ...


@dataclass(frozen=True)
class LLMProviderSpec:
    provider: str
    model: str | None
    api_key: str | None = None
    api_base: str | None = None
    temperature: float = 0.15
    max_output_tokens: int = 500
    request_timeout_seconds: int = 20
    reasoning_effort: str | None = None
    codex_model: str | None = None


def normalize_provider_name(provider: str | None) -> str:
    normalized = str(provider or "").strip().lower().replace("-", "_")
    aliases = {
        "codexgateway": "codex_gateway",
        "openai": "openai_compatible",
    }
    return aliases.get(normalized, normalized)


def provider_is_ready(spec: LLMProviderSpec) -> bool:
    provider = normalize_provider_name(spec.provider)
    if provider == "gemini":
        return bool((spec.api_key or "").strip() and (spec.model or "").strip())
    if provider == "openai_compatible":
        return bool((spec.api_base or "").strip() and (spec.model or "").strip())
    if provider == "codex_gateway":
        return bool((spec.api_base or "").strip() and (spec.model or "").strip())
    return False


def build_json_decision_provider(spec: LLMProviderSpec) -> JSONDecisionProvider | None:
    provider = normalize_provider_name(spec.provider)
    if provider == "gemini":
        if not provider_is_ready(spec):
            return None
        return GeminiDecisionProvider(
            model=spec.model or "",
            api_key=spec.api_key or "",
            temperature=spec.temperature,
            request_timeout_seconds=spec.request_timeout_seconds,
        )
    if provider == "openai_compatible":
        if not provider_is_ready(spec):
            return None
        return OpenAICompatibleDecisionProvider(
            model=spec.model or "",
            api_base=spec.api_base or "",
            api_key=spec.api_key,
            temperature=spec.temperature,
            max_output_tokens=spec.max_output_tokens,
            request_timeout_seconds=spec.request_timeout_seconds,
            reasoning_effort=spec.reasoning_effort,
        )
    if provider == "codex_gateway":
        if not provider_is_ready(spec):
            return None
        return CodexGatewayDecisionProvider(
            model=spec.model or "",
            api_base=spec.api_base or "",
            api_key=spec.api_key,
            temperature=spec.temperature,
            max_output_tokens=spec.max_output_tokens,
            request_timeout_seconds=spec.request_timeout_seconds,
            codex_model=spec.codex_model,
        )
    return None


class GeminiDecisionProvider:
    provider_name = "gemini"

    def __init__(self, *, model: str, api_key: str, temperature: float, request_timeout_seconds: int) -> None:
        self.model_name = model
        self.api_key = api_key
        self.temperature = temperature
        self.request_timeout_seconds = max(int(request_timeout_seconds), 1)

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_json_schema: dict | None = None,
    ) -> dict:
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "responseMimeType": "application/json",
                "responseJsonSchema": response_json_schema
                or {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "confidence": {"type": "number"},
                        "thesis": {"type": "string"},
                        "risks": {"type": "array", "items": {"type": "string"}},
                        "lessons_applied": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["action", "confidence", "thesis", "risks", "lessons_applied"],
                },
            },
        }
        request = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "trading-research-app/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.request_timeout_seconds) as response:
                raw_payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise LLMProviderError(f"Gemini request failed: {exc}") from exc

        content = self._extract_text(raw_payload)
        if not content:
            raise LLMProviderError("Gemini returned no decision content.")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMProviderError("Gemini returned malformed JSON decision content.") from exc

    @staticmethod
    def _extract_text(payload: dict) -> str:
        candidates = payload.get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts") or []
        return "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )


class OpenAICompatibleDecisionProvider:
    provider_name = "openai_compatible"

    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str | None,
        temperature: float,
        max_output_tokens: int,
        request_timeout_seconds: int,
        reasoning_effort: str | None = None,
    ) -> None:
        self.model_name = model
        self.api_base = api_base
        self.api_key = api_key
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.request_timeout_seconds = max(int(request_timeout_seconds), 1)
        self.reasoning_effort = reasoning_effort

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_json_schema: dict | None = None,
    ) -> dict:
        del response_json_schema
        payload = self._build_payload(system_prompt=system_prompt, user_prompt=user_prompt)
        request = Request(
            self._chat_completions_endpoint(),
            data=json.dumps(payload).encode("utf-8"),
            headers=self._build_headers(),
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.request_timeout_seconds) as response:
                raw_payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise LLMProviderError(f"{self.provider_name} request failed: {exc}") from exc

        content = self._extract_message_content(raw_payload)
        if not content:
            raise LLMProviderError(f"{self.provider_name} returned no decision content.")
        try:
            return self._extract_json_object(content)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(f"{self.provider_name} returned malformed JSON decision content.") from exc

    def _build_payload(self, *, system_prompt: str, user_prompt: str) -> dict:
        payload = {
            "model": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    def _chat_completions_endpoint(self) -> str:
        return f"{self.api_base.rstrip('/')}/chat/completions"

    def _build_headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "trading-research-app/0.1",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _extract_message_content(payload: dict) -> str:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        return ""

    @staticmethod
    def _extract_json_object(content: str) -> dict:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise json.JSONDecodeError("No JSON object found.", content, 0)
        return json.loads(content[start : end + 1])


class CodexGatewayDecisionProvider(OpenAICompatibleDecisionProvider):
    provider_name = "codex_gateway"

    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str | None,
        temperature: float,
        max_output_tokens: int,
        request_timeout_seconds: int,
        codex_model: str | None = None,
    ) -> None:
        super().__init__(
            model=model,
            api_base=api_base,
            api_key=api_key,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            request_timeout_seconds=request_timeout_seconds,
            reasoning_effort=None,
        )
        self.codex_model = (codex_model or "").strip() or None

    def _chat_completions_endpoint(self) -> str:
        return f"{self.api_base.rstrip('/')}/v1/chat/completions"

    def _build_payload(self, *, system_prompt: str, user_prompt: str) -> dict:
        payload = super()._build_payload(system_prompt=system_prompt, user_prompt=user_prompt)
        if self.codex_model:
            payload["codex_model"] = self.codex_model
        return payload

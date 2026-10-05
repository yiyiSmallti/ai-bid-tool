"""One accounted model call that must return a JSON object matching a wire contract."""

import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from app.providers.base import MalformedOutput, ProviderFailure, TruncatedOutput
from app.schemas.contracts import Contract, ProviderUsage

if TYPE_CHECKING:
    from app.providers.llm import HTTPExtractor


def strict_schema(model: type[Contract]) -> dict:
    """The contract's JSON schema with every object closed and every property required."""
    schema = model.model_json_schema()

    def close(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            node.pop("default", None)
            for child in node.values():
                close(child)
        elif isinstance(node, list):
            for child in node:
                close(child)

    close(schema)
    return schema


def json_request(llm: "HTTPExtractor", system: str, text: str, schema: dict, name: str) -> dict:
    settings = llm.settings
    body = {"model": llm.model, "max_tokens": settings.llm_max_output_tokens}
    if llm.name == "anthropic":
        body |= {
            "system": system,
            "messages": [{"role": "user", "content": text}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if settings.llm_effort:
            body["output_config"]["effort"] = settings.llm_effort
        if settings.llm_anthropic_fallback:
            body["fallbacks"] = "default"
    else:
        if settings.llm_json_mode == "json_schema":
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema, "strict": True},
            }
        else:
            response_format = {"type": "json_object"}
            system += "\nJSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
        body |= {
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
            "response_format": response_format,
        }
    return llm.build_request(body)


async def json_call[T: Contract](
    llm: "HTTPExtractor", client, body: dict, wire: type[T], purpose: str
) -> T:
    """Send `body` through the accounted HTTP boundary and validate the reply as `wire`."""
    result, _ = await json_call_with_usage(llm, client, body, wire, purpose)
    return result


async def json_call_with_usage[T: Contract](
    llm: "HTTPExtractor", client, body: dict, wire: type[T], purpose: str
) -> tuple[T, ProviderUsage]:
    """Return the already-settled HTTP receipt; consumers must not meter it again."""
    settings = llm.settings
    headers = {}
    if llm.name == "anthropic":
        headers = {
            "anthropic-version": "2023-06-01",
        }
        if settings.llm_api_key is not None:
            headers["x-api-key"] = settings.llm_api_key.get_secret_value()
        if settings.llm_anthropic_fallback:
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        url = (settings.llm_base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    else:
        if settings.llm_api_key is not None:
            headers["Authorization"] = "Bearer " + settings.llm_api_key.get_secret_value()
        url = (settings.llm_base_url or "https://api.openai.com/v1").rstrip(
            "/"
        ) + "/chat/completions"
    payload, usage = await llm.post(client, url, headers, body, safe_metadata=True)
    if llm.name == "anthropic":
        if payload.get("stop_reason") == "refusal":
            raise ProviderFailure(
                f"Model declined {purpose}", refused=True, code="provider_refused"
            )
        if payload.get("stop_reason") == "max_tokens":
            raise TruncatedOutput([usage])
        blocks = payload.get("content")
        if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
            raise MalformedOutput([usage])
        content = "".join(block["text"] for block in blocks if isinstance(block.get("text"), str))
    else:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise MalformedOutput([usage])
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise MalformedOutput([usage])
        if message.get("refusal"):
            raise ProviderFailure(
                f"Model declined {purpose}", refused=True, code="provider_refused"
            )
        if choice.get("finish_reason") == "length":
            raise TruncatedOutput([usage])
        content = message.get("content")
    if not isinstance(content, str):
        raise MalformedOutput([usage])
    try:
        return wire.model_validate_json(content), usage
    except ValidationError:
        raise MalformedOutput([usage]) from None

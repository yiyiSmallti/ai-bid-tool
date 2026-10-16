"""Synthetic OpenAI-compatible model for the enterprise end-to-end run.

It answers extraction, drafting and agent decision calls over HTTPS with fixed,
synthetic replies and appends one line per call to /tmp/vendor.log naming the
call kind and the model, never a header. While /tmp/hold exists, agent
decision calls wait, so a run can act on an agent whose call is in flight.
"""

import asyncio
import json
import re
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

LOG = Path("/tmp/vendor.log")
HOLD = Path("/tmp/hold")
PAGE = re.compile(r'<page number="(\d+)">\n(.*?)\n</page>', re.S)


def record(**line) -> None:
    with LOG.open("a") as handle:
        handle.write(json.dumps(line) + "\n")


def reply(model: str, content: dict) -> JSONResponse:
    return JSONResponse(
        {
            "model": model,
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 80},
        }
    )


def extraction(user: str) -> dict:
    items = []
    for page, text in PAGE.findall(user):
        line = [row.strip() for row in text.splitlines() if row.strip()][-1]
        items.append(
            {
                "category": "qualification" if page == "2" else "technical",
                "starred": False,
                "text": line,
                "ref": page,
                "quote": line,
                "condition": None,
            }
        )
    return {"items": items}


def proposals(sent: dict) -> dict:
    return {
        "items": [
            {
                "requirement_id": requirement["requirement_id"],
                "response_kind": "commitment",
                "suggested_disposition": "respond",
                "response_text": "Synthetic model proposal for human review.",
                "deviation": "none",
                "deviation_note": "The synthetic proposal describes the specified obligation.",
                "evidence": [],
            }
            for requirement in sent["requirements"]
        ]
    }


async def completions(request: Request) -> JSONResponse:
    body = await request.json()
    model = body.get("model")
    user = body["messages"][-1]["content"]
    sent = json.loads(user) if user.startswith("{") else None
    if sent is not None and "session_id" in sent:
        held = await asyncio.to_thread(HOLD.exists)
        record(kind="decision", model=model, held=held)
        for _ in range(1200):
            if not await asyncio.to_thread(HOLD.exists):
                break
            await asyncio.sleep(0.25)
        return reply(
            model,
            {
                "action": {
                    "kind": "human_action",
                    "action": "clarify",
                    "resource_ids": [],
                    "question": "Synthetic question: which cards should be drafted first?",
                }
            },
        )
    if sent is not None and "requirements" in sent:
        record(kind="draft", model=model, requirements=len(sent["requirements"]))
        return reply(model, proposals(sent))
    record(kind="extract", model=model)
    return reply(model, extraction(user))


app = Starlette(routes=[Route("/v1/chat/completions", completions, methods=["POST"])])

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8443,
        ssl_certfile="/e2e/vendor.crt",
        ssl_keyfile="/e2e/vendor.key",
        log_level="warning",
    )

"""Smoke test of an installed lite-cache (CI `package` job, design §11 S1.5): run with the wheel's
own venv, `python -I scripts/smoke.py`, with LITECACHE_DATABASE_URL (and LITECACHE_SCHEMA) set.

It migrates the schema from the SQL files shipped in the wheel, plugs CacheHook into a real
lite-model whose HTTP goes to a stand-in endpoint, and checks that the second identical call is
answered from PostgreSQL. Only installed packages are imported: nothing from this checkout."""

import json

import httpx2
from lite_model import Message, ModelsConfig, RolePolicy, RoleSpec, build, call_context

from lite_cache import CacheHook, open_store

REPLY = {
    "id": "chatcmpl-smoke",
    "object": "chat.completion",
    "created": 1,
    "model": "qwen3:8b",
    "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "pong"}}
    ],
    "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
}


def main() -> None:
    sent: list[httpx2.Request] = []

    def endpoint(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, json=REPLY)

    store = open_store()  # migrates from the shipped SQL files
    hook = CacheHook(store)
    try:
        hook.invalidate(scope="smoke")  # a rerun starts empty
        model = build(
            ModelsConfig.model_validate({"backend": "ollama", "model": "qwen3:8b"}),
            RoleSpec({"answer": RolePolicy()}),
            http_client=httpx2.Client(transport=httpx2.MockTransport(endpoint)),
        )
        model.hooks.register(hook)
        ask = [Message("user", "ping")]
        with call_context(scope="smoke", cache=True):
            first = model.text("answer", ask)
            second = model.text("answer", ask)
        assert (first.text, first.cached) == ("pong", False), first
        assert (second.text, second.cached) == ("pong", True), second
        assert len(sent) == 1, f"{len(sent)} requests sent"
        assert hook.store_errors == 0
        [stats] = hook.stats(scope="smoke")
        print(json.dumps({"ok": True, "hits": stats.hits, "stored": stats.stored}))
    finally:
        hook.invalidate(scope="smoke")
        store.close()


if __name__ == "__main__":
    main()

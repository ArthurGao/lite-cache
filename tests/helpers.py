"""Test helpers: a real `LiteModel` on a recording transport, and hand-made `Call`s."""

import dataclasses
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from lite_model import LiteModel, Message, ModelsConfig, RolePolicy, RoleSpec, build
from lite_model.hooks import Call

from tests.upstream import Upstream

ROLES = RoleSpec({"answer": RolePolicy(), "graph": RolePolicy()})
LOCAL: dict[str, Any] = {"backend": "ollama", "model": "qwen3:8b"}
ASK = (Message("user", "Hi?"),)


def local_model(up: Upstream, **config: Any) -> LiteModel:
    """A real LiteModel on local Ollama defaults whose requests go to `up` (no network)."""
    return build(ModelsConfig.model_validate(LOCAL | config), ROLES, http_client=up.client())


def a_call(
    *,
    role: str = "answer",
    backend: str | None = None,
    model: str | None = None,
    messages: Sequence[Message] = ASK,
    params: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> Call:
    """A `Call` as lite-model would build it for `role`, with the given parts changed."""
    target = local_model(Upstream()).target(role)
    target = dataclasses.replace(
        target, backend=backend or target.backend, model=model or target.model
    )
    request = {
        "model": target.model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        **(params if params is not None else {"temperature": 0.0}),  # as lite-model sends it
    }
    return Call(
        target=target,
        messages=tuple(messages),
        request=MappingProxyType(request),
        context=MappingProxyType(dict(context or {})),
    )

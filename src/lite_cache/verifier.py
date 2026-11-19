"""`ModelVerifier`: an L2 `Verifier` over a host's lite-model role (design §13.1). The host
declares a non-routable role (e.g. `cache_verifier`: qwen3:8b, `reasoning_effort: none`,
temperature 0); the verdict is a structured reply held to `SameAnswer`."""

from importlib.resources import files

from lite_model import Message, Model
from lite_model.config import Target
from pydantic import BaseModel, StrictBool

from lite_cache.canonical import content_hash
from lite_cache.errors import VerifierError


class SameAnswer(BaseModel):
    same: StrictBool


def prompt_text(version: str) -> str:
    return files("lite_cache").joinpath(f"verifier_prompt_{version}.txt").read_text()


class ModelVerifier:
    """`Verifier` implementation. `target` defaults to `model.target(role)`; pass it when `model`
    has no `target()` (a `Bounded` around the built model)."""

    def __init__(
        self,
        model: Model,
        role: str,
        *,
        target: Target | None = None,
        prompt_version: str = "v1",
    ) -> None:
        self._model = model
        self._role = role
        self._prompt = prompt_text(prompt_version)
        target = target if target is not None else model.target(role)  # type: ignore[attr-defined]
        # Everything that decides the verdict: backend type (not the host's name for it), model,
        # prompt and the role's output parameters. A change makes the old threshold inapplicable.
        decides = {
            "prompt": self._prompt,
            "temperature": target.temperature,
            "reasoning_effort": target.reasoning_effort,
            "max_output_tokens": target.max_output_tokens,
        }
        digest = content_hash(decides)[:8]
        self.model_id = f"{target.type}:{target.model}#{digest}"

    def same_answer(self, question: str, candidate: str) -> bool:
        messages = [
            Message("system", self._prompt),
            Message("user", f"A: {question}\nB: {candidate}"),
        ]
        try:  # CacheHook runs this without the outer call's policy (no caching, no L2)
            reply = self._model.structured(self._role, messages, SameAnswer)
        except Exception as exc:  # call failed, off-schema reply, ...: a read failure
            raise VerifierError(f"verifier {self.model_id}: {type(exc).__name__}") from exc
        return reply.value.same

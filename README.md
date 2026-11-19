# lite-cache

An LLM response cache for the lite modules: it plugs into
[lite-model](https://github.com/ArthurGao/lite-model) as a hook, answers a repeated request (L1, exact)
from PostgreSQL instead of calling the model again, and counts hits and the tokens they saved.
Design: `docs/lite-cache-design.md`. Development: `uv sync`, then `uv run --env-file .env pytest`.

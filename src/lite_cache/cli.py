"""The `litecache` CLI (design §6). Commands arrive in S1."""

import typer

app = typer.Typer(no_args_is_help=True, help="lite-cache: an LLM response cache (design §6).")

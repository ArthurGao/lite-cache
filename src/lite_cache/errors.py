"""lite-cache's errors (design §3.5)."""


class LiteCacheError(Exception):
    """Base of every error lite-cache raises."""


class StoreError(LiteCacheError):
    """The store could not be read or written (a database error, or no database configured)."""


class InvalidScope(LiteCacheError, ValueError):
    """A scope that is not one or more `/`-separated `[A-Za-z0-9_.-]+` segments (design §3.1)."""


class ConfigError(LiteCacheError):
    """The cache cannot serve what a call asks for: L2 without an embedder or without a
    threshold calibrated for that embedder (design §0), or a malformed setting."""


class EmbedderError(StoreError):
    """The injected embedder failed or returned a vector of the wrong size: a read failure, so
    the call fails before anything is sent (design §3.5), told apart from a database error."""


class VerifierError(StoreError):
    """The injected L2 verifier failed (raised, timed out, answered off-schema): a read failure,
    so the call fails before anything is sent (design §13, §3.5)."""

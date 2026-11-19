"""lite-cache's errors (design §3.5)."""


class LiteCacheError(Exception):
    """Base of every error lite-cache raises."""


class StoreError(LiteCacheError):
    """The store could not be read or written (a database error, or no database configured)."""


class InvalidScope(LiteCacheError, ValueError):
    """A scope that is not one or more `/`-separated `[A-Za-z0-9_.-]+` segments (design §3.1)."""

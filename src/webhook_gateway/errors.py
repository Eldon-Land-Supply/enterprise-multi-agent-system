"""Errors that can be translated directly into safe HTTP responses."""


class WebhookError(ValueError):
    """A client-facing webhook validation error."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

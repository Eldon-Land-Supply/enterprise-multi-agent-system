from dataclasses import dataclass
from typing import Optional


@dataclass
class ApiError(Exception):
    """Normalized error for GitHub API operations."""

    status_code: int
    message: str
    operation: str
    details: Optional[dict] = None

    def __str__(self) -> str:  # pragma: no cover - simple formatter
        base = f"{self.operation} failed with status {self.status_code}: {self.message}"
        if self.details:
            return f"{base} | details={self.details}"
        return base

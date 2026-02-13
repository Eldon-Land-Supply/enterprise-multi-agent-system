from typing import Any, Dict, List, Literal, Optional, TypedDict


class User(TypedDict, total=False):
    login: str
    id: int
    url: str


class Repository(TypedDict, total=False):
    id: int
    name: str
    full_name: str
    private: bool
    owner: User
    html_url: str
    description: Optional[str]


class ErrorResponse(TypedDict, total=False):
    message: str
    documentation_url: Optional[str]
    errors: Optional[List[Any]]


HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]

Payload = Dict[str, Any]
Headers = Dict[str, str]
JSONValue = Any

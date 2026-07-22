"""Fail-closed admission policy for GitHub deliveries that can trigger AI spend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


DEFAULT_TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
SAFE_ACTIONS = {
    "pull_request": frozenset(
        {"opened", "reopened", "synchronize", "ready_for_review"}
    ),
    "issues": frozenset({"opened", "reopened", "edited"}),
    "issue_comment": frozenset({"created"}),
    "check_run": frozenset({"completed"}),
    "check_suite": frozenset({"completed"}),
}


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    reason: str
    repository_id: str = ""
    repository: str = ""


class GitHubAdmissionPolicy:
    """Authorize repository, event, action, actor association, and GitHub App."""

    def __init__(
        self,
        allowed_repository_ids: set[str] | frozenset[str],
        allowed_events: set[str] | frozenset[str],
        *,
        trusted_associations: set[str] | frozenset[str] = DEFAULT_TRUSTED_ASSOCIATIONS,
        allowed_app_ids: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        self._repositories = {
            str(value).strip() for value in allowed_repository_ids if str(value).strip()
        }
        self._events = {
            str(value).strip() for value in allowed_events if str(value).strip()
        }
        self._associations = {
            str(value).strip().upper()
            for value in trusted_associations
            if str(value).strip()
        }
        self._apps = {
            str(value).strip() for value in allowed_app_ids if str(value).strip()
        }

    def evaluate(
        self, event_type: str, payload: Mapping[str, Any]
    ) -> AdmissionDecision:
        repository = payload.get("repository")
        if not isinstance(repository, Mapping):
            return AdmissionDecision(False, "missing_repository")
        repository_id = str(repository.get("id") or "")
        repository_name = str(repository.get("full_name") or "")
        if not repository_id or repository_id not in self._repositories:
            return AdmissionDecision(
                False,
                "repository_not_allowed",
                repository_id,
                repository_name,
            )
        if event_type not in self._events:
            return AdmissionDecision(
                False, "event_not_allowed", repository_id, repository_name
            )
        if event_type == "push":
            return AdmissionDecision(True, "allowed", repository_id, repository_name)

        action = str(payload.get("action") or "")
        if action not in SAFE_ACTIONS.get(event_type, frozenset()):
            return AdmissionDecision(
                False, "action_not_allowed", repository_id, repository_name
            )

        if event_type in {"check_run", "check_suite"}:
            check = payload.get(event_type)
            app = check.get("app") if isinstance(check, Mapping) else None
            app_id = str(app.get("id") or "") if isinstance(app, Mapping) else ""
            allowed = bool(app_id and app_id in self._apps)
            return AdmissionDecision(
                allowed,
                "allowed" if allowed else "app_not_allowed",
                repository_id,
                repository_name,
            )

        association_container = {
            "pull_request": "pull_request",
            "issues": "issue",
            "issue_comment": "comment",
        }.get(event_type)
        item = payload.get(association_container or "")
        association = (
            str(item.get("author_association") or "").upper()
            if isinstance(item, Mapping)
            else ""
        )
        allowed = association in self._associations
        return AdmissionDecision(
            allowed,
            "allowed" if allowed else "actor_not_trusted",
            repository_id,
            repository_name,
        )

import pytest

from webhook_gateway.github_admission import GitHubAdmissionPolicy


def policy(events=("push", "issue_comment"), apps=()):
    return GitHubAdmissionPolicy({"123"}, set(events), allowed_app_ids=set(apps))


def payload(**values):
    result = {"repository": {"id": 123, "full_name": "owner/repo"}}
    result.update(values)
    return result


def test_push_requires_exact_repository_id():
    assert policy().evaluate("push", payload()).allowed
    assert (
        not policy()
        .evaluate("push", {"repository": {"id": 999, "full_name": "owner/repo"}})
        .allowed
    )


@pytest.mark.parametrize("action", ["edited", "deleted"])
def test_issue_comment_rejects_non_created_actions(action):
    decision = policy().evaluate(
        "issue_comment",
        payload(action=action, comment={"author_association": "OWNER"}),
    )
    assert not decision.allowed
    assert decision.reason == "action_not_allowed"


def test_issue_comment_requires_trusted_association():
    public = policy().evaluate(
        "issue_comment",
        payload(action="created", comment={"author_association": "NONE"}),
    )
    member = policy().evaluate(
        "issue_comment",
        payload(action="created", comment={"author_association": "MEMBER"}),
    )
    assert not public.allowed
    assert member.allowed


def test_checks_require_explicit_github_app_id():
    check = payload(action="completed", check_run={"app": {"id": 77}})
    assert not policy(events={"check_run"}).evaluate("check_run", check).allowed
    assert (
        policy(events={"check_run"}, apps={"77"}).evaluate("check_run", check).allowed
    )

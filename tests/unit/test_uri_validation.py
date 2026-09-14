# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for common Viking URI boundary validation."""

from contextlib import nullcontext

import pytest

from openviking.core.uri_validation import validate_content_target_uri, validate_viking_uri
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidURIError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.parametrize(
    "uri",
    [
        "viking://resources/docs",
        "viking://session/s1",
        "viking://agent/code-agent/memories/facts/project.md",
        "viking://",
        "viking://~",
        "viking://~/memories/x",
    ],
)
def test_validate_viking_uri_accepts_supported_forms(uri: str):
    assert validate_viking_uri(uri) == uri.strip()


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "   ",
        "viking:/resources/docs",
        "resources/docs",
        "/resources/docs",
        "s3://bucket/key",
        "https://example.com/doc.md",
        "viking://unsupported/doc.md",
        "viking://temp/generated",
        "viking://queue/tasks",
    ],
)
def test_validate_viking_uri_rejects_invalid_or_unsupported_forms(uri: str):
    with pytest.raises(InvalidURIError):
        validate_viking_uri(uri)


def test_validate_viking_uri_reports_explicit_scheme_requirement():
    with pytest.raises(InvalidURIError) as exc_info:
        validate_viking_uri("ssd")

    message = str(exc_info.value)
    assert "viking://" in message
    assert "temp" not in message
    assert "queue" not in message
    assert "frozenset" not in message


def test_validate_viking_uri_hides_home_alias_in_public_scope_list():
    with pytest.raises(InvalidURIError) as exc_info:
        validate_viking_uri("viking://temp/generated")

    reason = exc_info.value.details["reason"]
    scope_list = reason.split("Must be one of:", 1)[1]
    assert "resources" in scope_list
    # The home alias is accepted but never advertised in the public scope list.
    assert "~" not in scope_list

    with pytest.raises(InvalidURIError) as exc_info:
        validate_viking_uri("viking://", allowed_scopes={"resources"})

    reason = exc_info.value.details["reason"]
    assert reason.startswith("URI must include one of:")
    assert "~" not in reason


def test_validate_viking_uri_supports_internal_and_operation_scopes():
    assert validate_viking_uri("viking://temp/generated", allow_internal=True)

    with pytest.raises(InvalidURIError) as exc_info:
        validate_viking_uri("viking://user/memories", allowed_scopes={"resources"})

    message = str(exc_info.value)
    assert "resources" in message
    assert "user" in message
    assert "temp" not in message
    assert "queue" not in message

    with pytest.raises(InvalidURIError, match="Invalid scope"):
        validate_viking_uri(
            "viking://invalid_scope/doc",
            allowed_scopes={"invalid_scope"},
        )


def test_validate_viking_uri_hides_home_alias_for_unknown_scopes():
    # Unknown scopes fail at the parser, whose message also enumerates scopes.
    with pytest.raises(InvalidURIError) as exc_info:
        validate_viking_uri("viking://bogus/x")

    reason = exc_info.value.details["reason"]
    assert "~" not in reason.split("Must be one of:", 1)[1]


def test_validate_viking_uri_rejects_home_alias_for_restricted_scopes():
    with pytest.raises(InvalidURIError) as exc_info:
        validate_viking_uri("viking://~", allowed_scopes={"resources"})

    reason = exc_info.value.details["reason"]
    assert reason.startswith("Invalid scope '~'")
    # Only the echoed offending scope may mention '~'; the advertised list must not.
    scope_list = reason.split("Must be one of:", 1)[1]
    assert "resources" in scope_list
    assert "~" not in scope_list


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN])
@pytest.mark.parametrize(
    "uri, actor_peer_id, error",
    [
        ("viking://agent/skills", "workspace-peer", None),
        ("viking://agent/skills/", "workspace-peer", None),
        ("viking://agent/skills/demo", "workspace-peer", None),
        ("viking://agent/skills/demo/SKILL.md", "workspace-peer", None),
        ("viking://agent/skills", None, None),
        ("viking://agent/skills/demo/SKILL.md", None, None),
        ("viking://agent/skills", "skills", None),
        ("viking://agent/skills/demo/SKILL.md", "skills", None),
        ("viking://user/alice/skills", "workspace-peer", None),
        ("viking://user/alice/skills", None, None),
        ("viking://user/bob/skills", "workspace-peer", PermissionDeniedError),
        ("viking://user/bob/skills", None, PermissionDeniedError),
        ("viking://agent/skills-extra", "workspace-peer", InvalidURIError),
        ("viking://agent/skills-extra/skills/demo", "workspace-peer", PermissionDeniedError),
        ("viking://agent/skills-extra/skills/demo", None, None),
        ("viking://agent/other-peer/skills/demo", "workspace-peer", PermissionDeniedError),
        ("viking://agent/workspace-peer/skills/demo", "workspace-peer", None),
        ("viking://agent/workspace-peer/skills/demo", None, None),
    ],
)
def test_validate_content_target_uri_preserves_skill_access(uri, actor_peer_id, error, role):
    ctx = RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=role,
        actor_peer_id=actor_peer_id,
    )
    expected = pytest.raises(error) if error else nullcontext()

    with expected:
        assert validate_content_target_uri(uri, ctx, kind="skill") == uri.rstrip("/")

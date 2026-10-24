"""Phase 23 检索层访问控制契约测试。"""

from __future__ import annotations

import pytest

from app.retrieval.access_control import (
    AccessContext,
    AccessRule,
    Role,
    filter_accessible,
)


def _context(**overrides) -> AccessContext:
    payload = dict(team="qa", project="shop", role=Role.VIEWER)
    payload.update(overrides)
    return AccessContext.model_validate(payload)


def test_rule_checks_all_configured_dimensions() -> None:
    rule = AccessRule(teams=["qa", "platform"], projects=["shop"], min_role=Role.EDITOR)

    assert rule.allows(_context(role=Role.EDITOR)) is True
    assert rule.allows(_context(role=Role.ADMIN)) is True
    assert rule.allows(_context(role=Role.VIEWER)) is False
    assert rule.allows(_context(team="other")) is False
    assert rule.allows(_context(project="other")) is False


def test_none_dimension_means_unrestricted() -> None:
    rule = AccessRule()

    assert rule.allows(_context()) is True
    assert rule.allows(_context(team="any", project="any")) is True


def test_empty_dimension_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="teams"):
        AccessRule(teams=[])
    with pytest.raises(ValueError, match="projects"):
        AccessRule(projects=["  "])


def test_filter_accessible_removes_denied_items_before_scoring() -> None:
    items = [("public", AccessRule()), ("secret", AccessRule(min_role=Role.ADMIN))]

    visible = filter_accessible(
        items,
        context=_context(role=Role.VIEWER),
        rule_of=lambda item: item[1],
    )

    assert [item[0] for item in visible] == ["public"]


def test_missing_rule_means_public() -> None:
    items = [("a", None), ("b", AccessRule(teams=["qa"]))]

    visible = filter_accessible(
        items,
        context=_context(),
        rule_of=lambda item: item[1],
    )

    assert [item[0] for item in visible] == ["a", "b"]

"""权限表本身。见 2026-08-23 网页服务化设计 §1、§3

这里只测表；测「路由是否照着表执行」在 tests/web/test_authorization.py。
"""
import pytest

from framework_reader.identity import ROLES
from framework_reader.identity.permissions import (
    ALL_PERMISSIONS,
    INTERPRETATION_CONFIRM,
    INTERPRETATION_DRAFT,
    ROLE_PERMISSIONS,
    allows,
    permissions_of,
)


def test_every_role_has_an_entry():
    assert set(ROLE_PERMISSIONS) == set(ROLES)


def test_no_role_grants_an_unknown_permission():
    for role, perms in ROLE_PERMISSIONS.items():
        assert perms <= ALL_PERMISSIONS, f"{role} 授予了表外的权限"


def test_roles_add_up_not_inherit():
    """角色是加法的。author + approver 应当同时能起草和签字。"""
    both = permissions_of(["author", "approver"])
    assert INTERPRETATION_DRAFT in both and INTERPRETATION_CONFIRM in both


def test_admin_cannot_confirm():
    """管理员能配置系统，不代表他懂这条控制。设计 §3 要盯住的第一条。"""
    assert not allows(["admin"], INTERPRETATION_CONFIRM)


def test_admin_cannot_draft():
    """起草花的是组织的钱。"""
    assert not allows(["admin"], INTERPRETATION_DRAFT)


def test_an_admin_who_wants_to_confirm_must_grant_himself_the_role():
    assert allows(["admin", "approver"], INTERPRETATION_CONFIRM)


def test_approver_cannot_spend_money():
    assert not allows(["approver"], INTERPRETATION_DRAFT)


def test_viewer_can_only_read_and_export():
    from framework_reader.identity.permissions import CONTENT_READ, REPORT_EXPORT

    assert permissions_of(["viewer"]) == {CONTENT_READ, REPORT_EXPORT, "member:read"}


def test_everyone_can_read():
    from framework_reader.identity.permissions import CONTENT_READ

    for role in ROLES:
        assert allows([role], CONTENT_READ)


def test_no_roles_means_no_permissions():
    assert permissions_of([]) == frozenset()


def test_an_unknown_role_grants_nothing():
    assert permissions_of(["god"]) == frozenset()


@pytest.mark.parametrize("permission", sorted(ALL_PERMISSIONS))
def test_every_permission_is_reachable_by_someone(permission):
    """表里挂着一个谁都没有的权限，多半是写错了名字。"""
    assert any(permission in perms for perms in ROLE_PERMISSIONS.values())

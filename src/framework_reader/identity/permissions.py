"""权限：谁能干什么。见 2026-08-23 网页服务化设计 §1、§3

**这张表是唯一的事实来源。** 设计文档里的矩阵是它的人类可读副本；
路由上的 `@needs(...)` 引用这里的权限名；遍历测试拿它对答案。

两条不变的规矩：

- **角色是加法的，不是继承树**（§1.1）。一个人的权限是他各角色的并集。
  不做 `admin ⊃ author ⊃ viewer`——继承树的必然后果是「管理员自动拥有一切」，
  而在这个产品里那恰恰是错的：管理员能配置系统，不代表他懂这条控制。
- **权限的单位是动作，不是页面**（§1.2）。同一个动作可能出现在三个页面上；
  按页面授权就要判三次，迟早漏一处。
"""

# ---- 权限名。划分依据是**后果**，不是实现方式 ----
CONTENT_READ = "content:read"
REPORT_EXPORT = "report:export"
FRAMEWORK_IMPORT = "framework:import"
FRAMEWORK_DELETE = "framework:delete"
# 起草与「让 AI 重写」共用一个权限：它们的共同点不是「都用 AI」，是**都花钱**
INTERPRETATION_DRAFT = "interpretation:draft"
INTERPRETATION_WRITE = "interpretation:write"
INTERPRETATION_CONFIRM = "interpretation:confirm"
ASSESSMENT_WRITE = "assessment:write"
# 上传的是本组织的内部制度，而且它会被发进模型的 payload。
# 所以既不给 viewer 看（那一档是留给外部审计与刚入职的人的），
# 也不和「导入框架」共用一个权限——后果不是一回事。
DOCUMENT_READ = "document:read"
DOCUMENT_WRITE = "document:write"
MEMBER_READ = "member:read"
MEMBER_MANAGE = "member:manage"
ROLE_GRANT = "role:grant"
MODEL_READ = "model:read"
MODEL_WRITE = "model:write"
AUDIT_READ = "audit:read"

ALL_PERMISSIONS = frozenset({
    CONTENT_READ, REPORT_EXPORT, FRAMEWORK_IMPORT, FRAMEWORK_DELETE,
    INTERPRETATION_DRAFT, INTERPRETATION_WRITE, INTERPRETATION_CONFIRM,
    ASSESSMENT_WRITE, DOCUMENT_READ, DOCUMENT_WRITE,
    MEMBER_READ, MEMBER_MANAGE, ROLE_GRANT,
    MODEL_READ, MODEL_WRITE, AUDIT_READ,
})

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    # 管系统。**不含起草与确认**：起草花的是组织的钱，确认是专业判断。
    # 管理员要做就给自己加角色——而那一步会进审计日志。提权留痕正是要的。
    "admin": frozenset({
        CONTENT_READ, REPORT_EXPORT, FRAMEWORK_IMPORT, FRAMEWORK_DELETE,
        # 文档能看能删：传错一份内部制度上去，得有人能立刻拿掉。
        # 那是管理动作，和「改解读」那种专业动作不是一回事。
        DOCUMENT_READ, DOCUMENT_WRITE,
        MEMBER_READ, MEMBER_MANAGE, ROLE_GRANT, MODEL_READ, MODEL_WRITE,
        AUDIT_READ,
    }),
    # 干活的人。能花钱起草，能改字段，不能签字。
    "author": frozenset({
        CONTENT_READ, REPORT_EXPORT, FRAMEWORK_IMPORT,
        INTERPRETATION_DRAFT, INTERPRETATION_WRITE, ASSESSMENT_WRITE,
        DOCUMENT_READ, DOCUMENT_WRITE,
        MEMBER_READ, MODEL_READ,
    }),
    # 认领这段话的人。能改（签字前总要能顺手改一句），不能花钱起草。
    "approver": frozenset({
        CONTENT_READ, REPORT_EXPORT,
        INTERPRETATION_WRITE, INTERPRETATION_CONFIRM, ASSESSMENT_WRITE,
        # 能看不能传：签字前要能核对「这句话的依据是我们哪份文件的哪一段」。
        DOCUMENT_READ,
        MEMBER_READ,
    }),
    # 给外部审计、给管理层、给刚入职的人。
    # **没有 document:read**：这一档是给外面的人留的，而上传的是内部制度全文。
    "viewer": frozenset({CONTENT_READ, REPORT_EXPORT, MEMBER_READ}),
}


def permissions_of(roles) -> frozenset[str]:
    """一个人的权限 = 他各角色权限的并集。"""
    out: set[str] = set()
    for role in roles:
        out |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(out)


def allows(roles, permission: str) -> bool:
    return permission in permissions_of(roles)

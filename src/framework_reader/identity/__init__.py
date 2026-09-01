"""身份层：账号、角色、会话、邀请、审计。

见 `docs/superpowers/specs/2026-08-23-hosted-service-rbac-aad-design.md`。
S1 只做**身份**（你是谁）；**授权**（你能干什么）是 S2 的事。
"""
ROLES = ("admin", "author", "approver", "viewer")

# 新账号默认只读。默认值决定了忘记配置时会发生什么，而忘记配置是常态。
DEFAULT_ROLE = "viewer"

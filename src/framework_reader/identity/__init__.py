"""Identity layer: accounts, roles, sessions, invites, audit.

See `docs/superpowers/specs/2026-08-23-hosted-service-rbac-aad-design.md`.
S1 does **identity** only (who you are); **authorization** (what you can do) is
S2's job.
"""
ROLES = ("admin", "author", "approver", "viewer")

# New accounts default to read-only. The default decides what happens when
# configuration is forgotten, and forgetting to configure is the norm.
DEFAULT_ROLE = "viewer"

"""Permissions: who may do what. See the 2026-08-23 hosted-service design §1, §3

**This table is the single source of truth.** The matrix in the design document
is its human-readable copy; the `@needs(...)` decorators on routes reference the
permission names here; the exhaustive test checks its answers against it.

Two standing rules:

- **Roles are additive, not an inheritance tree** (§1.1). A person's permissions
  are the union of all his roles. No `admin ⊃ author ⊃ viewer` - the inevitable
  consequence of an inheritance tree is "the admin automatically has
  everything", and in this product that is precisely wrong: being able to
  configure the system does not mean the admin understands this control.
- **The unit of permission is an action, not a page** (§1.2). The same action
  can appear on three pages; authorizing per page means checking three times,
  and sooner or later one check is missed.
"""

# ---- Permission names. The dividing criterion is **consequence**, not implementation ----
CONTENT_READ = "content:read"
REPORT_EXPORT = "report:export"
FRAMEWORK_IMPORT = "framework:import"
FRAMEWORK_DELETE = "framework:delete"
# Drafting and "let the AI rewrite" share one permission: what they have in
# common is not "both use AI" but **both cost money**
INTERPRETATION_DRAFT = "interpretation:draft"
INTERPRETATION_WRITE = "interpretation:write"
INTERPRETATION_CONFIRM = "interpretation:confirm"
ASSESSMENT_WRITE = "assessment:write"
# What gets uploaded is the organization's internal policy, and it is sent into
# the model's payload. So it is neither visible to viewer (that tier is reserved
# for external audit and brand-new hires), nor does it share a permission with
# "import framework" - the consequences are not the same thing.
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
    # Run the system. **No drafting and no confirming**: drafting spends the
    # organization's money, confirming is professional judgment. An admin who
    # wants either grants himself the role - and that step lands in the audit
    # log. Leaving a trail on privilege escalation is exactly the point.
    "admin": frozenset({
        CONTENT_READ, REPORT_EXPORT, FRAMEWORK_IMPORT, FRAMEWORK_DELETE,
        # Documents can be viewed and deleted: if the wrong internal policy gets
        # uploaded, someone must be able to take it down immediately. That is an
        # administrative action, not the professional act of "editing an
        # interpretation".
        DOCUMENT_READ, DOCUMENT_WRITE,
        MEMBER_READ, MEMBER_MANAGE, ROLE_GRANT, MODEL_READ, MODEL_WRITE,
        AUDIT_READ,
    }),
    # The people doing the work. May spend money drafting, may edit fields, may not sign.
    "author": frozenset({
        CONTENT_READ, REPORT_EXPORT, FRAMEWORK_IMPORT,
        INTERPRETATION_DRAFT, INTERPRETATION_WRITE, ASSESSMENT_WRITE,
        DOCUMENT_READ, DOCUMENT_WRITE,
        MEMBER_READ, MODEL_READ,
    }),
    # The person who owns this passage. May edit (a sentence always needs a quick
    # fix before signing), may not spend money drafting.
    "approver": frozenset({
        CONTENT_READ, REPORT_EXPORT,
        INTERPRETATION_WRITE, INTERPRETATION_CONFIRM, ASSESSMENT_WRITE,
        # May view but not upload: before signing, one must be able to check
        # "which passage of which of our documents this sentence rests on".
        DOCUMENT_READ,
        MEMBER_READ,
    }),
    # For external audit, for management, for brand-new hires.
    # **No document:read**: this tier is reserved for people on the outside, and
    # what gets uploaded is the internal policy in full.
    "viewer": frozenset({CONTENT_READ, REPORT_EXPORT, MEMBER_READ}),
}


def permissions_of(roles) -> frozenset[str]:
    """A person's permissions = the union of the permissions of all his roles."""
    out: set[str] = set()
    for role in roles:
        out |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(out)


def allows(roles, permission: str) -> bool:
    return permission in permissions_of(roles)

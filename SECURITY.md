# Security Policy

## Supported versions

This project ships a single rolling version: the latest tagged release on
[GitHub Releases](https://github.com/pz1130/framework-reader/releases) is the
supported one. There are no LTS branches; `main` tracks current development
and may contain unreleased changes.

## Reporting a vulnerability

Please use **GitHub's private vulnerability reporting** (Security tab →
"Report a vulnerability") rather than a public issue, so fixes can land
before the details are public.

Include what you can of: the affected component or route, a minimal
reproduction, and the impact you see. A first response within 7 days is the
goal; the fix timeline depends on severity. Credit in the release notes is
yours unless you ask otherwise.

## Scope

**In scope:**

- `framework_reader.web` — authentication and session handling (password
  login, one-time invite links, session cookies), the RBAC route guards
  (`@needs(...)` and the default-refuse behaviour for unlabelled routes),
  and the Entra ID OIDC flow (PKCE, state handling, role mapping).
- API key storage — encryption under the `FR_SECRET_KEY` master key, what
  is echoed back and written to the audit log.
- Document/spreadsheet upload handling (file type enforcement, path
  handling) in `framework_reader.web.uploads` and `...images`.
- Insecure defaults shipped in the server itself (e.g. listening address,
  cookie flags).

**Out of scope:**

- A self-hosted deployment's own misconfiguration — binding to a public
  interface without TLS, a weak `FR_BOOTSTRAP_ADMIN_PASSWORD`, sharing
  `.env`. The README marks these choices where they exist.
- Vulnerabilities in third-party LLM vendors or in content fetched by
  `scripts/fetch_sources.sh` from NIST.
- The interpretive accuracy of the content pack. That is data, not code —
  disagreements belong in issues, and AI-drafted fields are marked
  `state=draft` throughout.

## Design notes you may want before reporting

- Passwords are hashed with scrypt (OWASP-recommended parameters, per-hash
  salt, constant-time comparison). Session tokens and invite tokens are
  stored as SHA-256 hashes only — a database leak does not yield usable
  tokens.
- API keys are encrypted with a master key that lives only in the
  environment (`FR_SECRET_KEY`); it is never written to the database.
  Without a master key, key writes are refused rather than falling back to
  plaintext.
- Every route must declare its role guard; unlabelled routes are refused,
  and a generated test walks every route × every role to catch drift
  (`tests/web/test_authorization.py`).
- The `admin` role can run the system but cannot confirm or edit content;
  granting yourself a role is refused by default and every attempt lands
  in the audit log.
- The content pack contains no copyrighted standard text: NIST material is
  U.S. government work (public domain) and is fetched separately; ISO/IEC
  27002 appears as control numbers plus self-written labels.

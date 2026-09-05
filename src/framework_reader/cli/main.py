"""Disposable CLI shell. Phase B replaces it with the web UI - hence no business logic or bare SQL in here."""
from pathlib import Path

import typer

from framework_reader.pack.build import build_content_db
from framework_reader import paths
from framework_reader.query.api import QueryAPI

app = typer.Typer(help="Framework Reader")
DEFAULT_DB = paths.content_db()
BLINDTEST_DIR = Path("build/blindtest")


@app.callback()
def _log_every_call(ctx: typer.Context) -> None:
    """Append one self-usage log line per invocation. Main spec §7.3.1 - the only validation signal so far.

    `usage` does not log itself: a report command is not usage, and logging it just pads the counter.
    """
    from framework_reader import usage

    command = ctx.invoked_subcommand
    if not command or command == "usage":
        return
    import sys

    usage.record(command, target=usage.target_from_argv(sys.argv, command))


@app.command()
def usage(days: int = 0, note: str = "") -> None:
    """Self-usage log: print a report; `--note` appends a manual entry. Main spec §7.3.1"""
    from framework_reader import usage as usage_log

    if note:
        usage_log.record("usage", note=note)
        typer.echo("Noted.")
        return
    entries = usage_log.load()
    if days:
        entries = usage_log.within_days(entries, days)
    typer.echo(usage_log.render_report(entries))


@app.command()
def build(out: Path = DEFAULT_DB) -> None:
    """Run the full import, validation, and build assertions."""
    out.parent.mkdir(parents=True, exist_ok=True)
    typer.echo(f"built {build_content_db(out)}")


@app.command()
def show(control_id: str, db: Path = DEFAULT_DB) -> None:
    """Show one control with its neighbours and its interpretation."""
    api = QueryAPI(db)
    ctl = api.get_control(control_id)
    if ctl is None:
        typer.echo(f"Not found: {control_id}")
        raise typer.Exit(1)
    suffix = "  [deprecated]" if ctl.status == "deprecated" else ""
    typer.echo(f"{ctl.id}  [{ctl.framework_id}]  {ctl.label}{suffix}")
    for s in api.superseded_by(control_id):
        typer.echo(f"  ⇒ superseded by {s.control_id}  {s.label}  [{s.relation}]")
    for s in api.supersedes(control_id):
        typer.echo(f"  ⇐ supersedes {s.control_id}  {s.label}  [{s.relation}]")
    for n in api.neighbors(control_id):
        flag = "exportable" if n.exportable else "not exportable"
        typer.echo(f"  → {n.control_id}  {n.label}  [{n.level} · {n.source} · {flag}]")

    fields = api.interpretation(control_id)
    if fields:
        from framework_reader.interpret.model import Basis, Field, Interpretation
        from framework_reader.interpret.render import render_interpretation

        state = api.interpretation_state(control_id)
        if state != "confirmed":
            # Drafts ship so the pack is usable, not to pass them off as final.
            # The state banner goes before the body, not in a footnote.
            typer.echo(f"\n[AI draft, not yet confirmed by the author · state={state}]")
        else:
            typer.echo("")
        typer.echo(render_interpretation(Interpretation(
            control_id=control_id,
            fields={
                name: Field(value=f["value"], basis=Basis(f["basis"]))
                for name, f in fields.items()
            },
        )))


@app.command()
def search(keyword: str, limit: int = 20, db: Path = DEFAULT_DB) -> None:
    """Find controls by keyword. Searches titles and interpretation bodies."""
    hits = QueryAPI(db).search(keyword, limit)
    if not hits:
        typer.echo(f'No control found containing "{keyword}"')
        raise typer.Exit(1)
    for ctl in hits:
        typer.echo(f"{ctl.id}  [{ctl.framework_id}]  {ctl.label}")


# "Implemented by a third party" must stay separate from "not applicable". A control
# implemented by your cloud vendor or landlord **is applicable** - someone else does the
# work, and the evidence is their contract or SOC 2 / ISO report. Marking it N/A is
# exactly the mistake auditors look for.
SOA_STATUS = {"0": "not started", "1": "in progress", "2": "implemented", "3": "implemented by a third party"}


@app.command()
def assess(
    control_id: str = typer.Argument("", help="Leave empty to walk the whole framework control by control"),
    framework: str = "NIST-CSF-2.0",
    function: str = "",
    scope: str = "default",
    redo: bool = False,
    db: Path = DEFAULT_DB,
) -> None:
    """Record current state control by control: what level are we at. Skips already-assessed ones; --redo re-asks."""
    from framework_reader.assess.store import AssessStore

    api = QueryAPI(db)
    store = AssessStore()
    if control_id:
        targets = [control_id]
    else:
        # Prefix match, not split-on-first-dot: CSF wants DE, ISO wants A.7 -
        # splitting A.5.1 on dots yields "A", which would match all 93 controls.
        targets = [
            c.id for c in api.list_controls(framework, leaf_only=True)
            if not function or c.id.split(":", 1)[-1].startswith(function)
        ]
    if not targets:
        typer.echo("No controls to assess")
        raise typer.Exit(1)

    todo = [
        cid for cid in targets if redo or store.get(cid, scope) is None
    ]
    done = 0
    for index, cid in enumerate(todo, start=1):
        ctl = api.get_control(cid)
        if ctl is None:
            continue
        typer.echo(f"\n{'─' * 60}\n[{index}/{len(todo)}] {cid}  {ctl.label}")
        fields = api.interpretation(cid)
        for name in ("intent", "plain_zh"):
            value = (fields.get(name) or {}).get("value")
            if value:
                typer.echo(f"  {value}")
        practice = (fields.get("practice") or {}).get("value") or {}
        if practice:
            typer.echo("")
            for rung in sorted(practice):
                typer.echo(f"  Level {rung}: {practice[rung]}")
            question = "\nWhat level are you at now? 0=not doing it, 1/2/3=level, n=not applicable, Enter to skip"
        else:
            # Without the three practice rungs there is no level to ask for. Frameworks
            # like ISO Annex A want a Statement of Applicability instead: applicable?
            # implemented? where is the evidence? Main spec §7.3.3
            from framework_reader.assess.soa import fill_hints

            for hint in fill_hints(api, cid):
                typer.echo(f"  {hint}")
            question = (
                "\nImplementation status? 0=not started, 1=in progress, 2=implemented, 3=by a third party,"
                "n=not applicable, Enter to skip"
            )
        answer = typer.prompt(question, default="", show_default=False).strip()
        if not answer:
            continue
        if answer.lower() == "n":
            reason = typer.prompt("Reason it is not applicable", default="").strip()
            store.record(cid, scope=scope, applicable=False, reason=reason)
        elif practice:
            try:
                level = int(answer)
            except ValueError:
                typer.echo("Did not understand that; skipping this control")
                continue
            note = typer.prompt("Current state / where is the evidence (optional)", default="").strip()
            store.record(cid, scope=scope, level=level, note=note)
        else:
            status = SOA_STATUS.get(answer)
            if status is None:
                typer.echo("Did not understand that; skipping this control")
                continue
            note = typer.prompt("Implementation notes / where is the evidence (optional)", default="").strip()
            store.record(cid, scope=scope, status=status, note=note)
        done += 1
    left = sum(1 for cid in targets if store.get(cid, scope) is None)
    typer.echo(f"\nRecorded this round: {done} control(s) → {store.path}")
    # A 93-control walk takes several sittings. Always show what is left, or you
    # cannot tell how long this will take.
    typer.echo(f"Remaining: {left} of {len(targets)} controls to assess")


@app.command()
def gap(
    framework: str = "NIST-CSF-2.0",
    scope: str = "default",
    out: Path | None = None,
    db: Path = DEFAULT_DB,
) -> None:
    """Gap report: what is missing, weakest first, the next step, and what serves as evidence."""
    from framework_reader.assess.report import build_gap, render_gap
    from framework_reader.assess.store import AssessStore

    api = QueryAPI(db)
    controls = api.list_controls(framework, leaf_only=True)
    content = {}
    for ctl in controls:
        fields = api.interpretation(ctl.id)
        content[ctl.id] = {
            "label": ctl.label,
            "practice": (fields.get("practice") or {}).get("value") or {},
            "evidence": (fields.get("evidence") or {}).get("value") or "",
        }
    entries = [
        a for a in AssessStore().all(scope) if a.control_id in content
    ]
    text = render_gap(build_gap(entries, content, total=len(controls)))
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        typer.echo(f"Gap report → {out}")
        return
    typer.echo(text)


@app.command()
def soa(
    framework: str = "ISO-27002-2022",
    scope: str = "default",
    out: Path | None = None,
    db: Path = DEFAULT_DB,
) -> None:
    """Export a Statement of Applicability. All 93 controls, unfilled ones marked TBD."""
    from framework_reader.assess.soa import (
        build_soa, render_soa_csv, render_soa_markdown,
    )
    from framework_reader.assess.store import AssessStore

    controls = [
        (c.id, c.label) for c in QueryAPI(db).list_controls(framework, leaf_only=True)
    ]
    rows = build_soa(controls, AssessStore().all(scope))
    as_csv = bool(out and out.suffix.lower() == ".csv")
    text = render_soa_csv(rows) if as_csv else render_soa_markdown(rows)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text if as_csv else text + "\n", encoding="utf-8")
        pending = sum(1 for r in rows if r.applicable is None)
        typer.echo(f"SoA {len(rows)} control(s) → {out} ({pending} to fill)")
        return
    typer.echo(text)


@app.command()
def publish(
    out: Path = Path("build/site/index.html"),
    framework: str = "",
    db: Path = DEFAULT_DB,
) -> None:
    """Render interpretations into one publishable HTML page. Empty --framework includes every framework."""
    from framework_reader.publish.site import FRAMEWORKS, collect, render_multi

    api = QueryAPI(db)
    wanted = [framework] if framework else list(FRAMEWORKS)
    groups = [(fw, collect(api, fw)) for fw in wanted]
    groups = [(fw, entries) for fw, entries in groups if entries]
    if not groups:
        typer.echo("No interpretations to publish")
        raise typer.Exit(1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_multi(groups), encoding="utf-8")
    for fw, entries in groups:
        edges = sum(len(e.mappings) for e in entries)
        typer.echo(f"{fw}  {len(entries)} interpretations, {edges} official mappings")
    typer.echo(f"→ {out}")


@app.command("frameworks")
def frameworks(db: Path = DEFAULT_DB) -> None:
    """List every framework: built-in and imported."""
    from framework_reader.userframework.store import UserFrameworkStore

    api = QueryAPI(db)
    mine = {f.id for f in UserFrameworkStore().list_frameworks()}
    for view in sorted(api.list_frameworks(), key=lambda f: f.id):
        count = len(api.list_controls(view.id, leaf_only=True))
        kind = "imported" if view.id in mine else "built-in"
        typer.echo(f"{view.id:22} {kind}  {count:>4} controls  {view.name}")


@app.command("import")
def import_framework(
    path: Path,
    framework_id: str = typer.Option(..., "--id", help="Unique id, e.g. ACME-SEC-2026"),
    name: str = typer.Option(..., "--name", help="Display name"),
    version: str = "",
) -> None:
    """Import your own framework (.csv / .xlsx; requires ID and Title columns, Parent optional)."""
    from framework_reader.userframework.importer import ImportError_, parse_table, read_rows
    from framework_reader.userframework.store import UserFrameworkStore

    try:
        controls = parse_table(read_rows(path))
    except ImportError_ as exc:
        typer.echo(f"Import failed: {exc}")
        raise typer.Exit(1) from exc
    store = UserFrameworkStore()
    result = store.add_framework(
        framework_id=framework_id, name=name, controls=controls,
        version=version, source_file=str(path),
    )
    typer.echo(f"Imported {result.controls} control(s) → {framework_id} ({store.path})")
    typer.echo(f"Next, run: fr draft --framework-id {framework_id} --full")
    typer.echo("Or open this framework in the web workbench (fr serve) and click \"Draft interpretation\".")


@app.command("migrate-drafts")
def migrate_drafts(
    force: bool = typer.Option(
        False, "--force", help="Overwrite the copy already in your library (it may be the one you edited)"
    ),
    delete: bool = typer.Option(
        False, "--delete", help="Delete the originals under content/ after moving; they are git-tracked, so think before deleting"
    ),
) -> None:
    """Move imported-framework interpretations that landed in content/interpretations/ into the user library.

    Before b971e12, drafting an imported framework wrote its YAML into the product
    content repo, which the query layer never reads - after drafting, the page still
    said "no interpretation yet". The storage layer is fixed; this command moves the
    old entries.
    """
    from framework_reader.interpret.migrate import migrate_user_drafts

    report = migrate_user_drafts(force=force, delete=delete)
    if not report.moved and not report.skipped:
        typer.echo("Nothing to move: the content repo holds no interpretations for the frameworks you imported.")
        return
    typer.echo(f"Moved into your library: {len(report.moved)} control(s)")
    for control_id in report.moved:
        typer.echo(f"  {control_id}")
    if report.deleted:
        typer.echo(f"Deleted originals: {len(report.deleted)} file(s)")
    if report.skipped:
        typer.echo(f"Skipped {len(report.skipped)} control(s): ")
        for what, why in report.skipped:
            typer.echo(f"  {what}  {why}")


account_app = typer.Typer(help="Accounts and roles. The first admin enters here.")
app.add_typer(account_app, name="account")


def _identity():
    from framework_reader.identity.store import IdentityStore

    return IdentityStore()


@account_app.command("bootstrap")
def account_bootstrap(
    email: str = typer.Option(..., "--email"),
    password: str = typer.Option(..., "--password"),
) -> None:
    """Create the first operator if the identity store is empty. Idempotent.

    Used by the Docker entrypoint. Does not print the password.
    """
    from framework_reader.identity.store import IdentityError, IdentityStore

    try:
        account = IdentityStore().bootstrap(email=email, password=password)
    except IdentityError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    if account is None:
        typer.echo("Accounts already exist; bootstrap skipped.")
        return
    typer.echo(
        f"Bootstrapped {account.email} with roles: "
        + ", ".join(sorted(account.roles))
        + ". The workbench now requires login."
    )


@account_app.command("invite")
def account_invite(
    email: str,
    role: str = typer.Option("viewer", "--role", help="admin | author | approver | viewer"),
    base_url: str = typer.Option("http://127.0.0.1:8765", "--url"),
) -> None:
    """Issue a one-time invite link. The link is printed once; only its hash is stored."""
    from framework_reader.identity.store import IdentityError

    try:
        token = _identity().invite(email=email, role=role, by="cli")
    except IdentityError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    typer.echo(f"Invite for {email} (role {role}): ")
    typer.echo(f"  {base_url.rstrip('/')}/invite/{token}")
    typer.echo("Valid for seven days, single use. This token is shown only once.")
    typer.echo("Note: the web workbench requires login from now on, not only after the invite is accepted.")


@account_app.command("list")
def account_list() -> None:
    """List all accounts and their roles."""
    accounts = _identity().list_accounts()
    if not accounts:
        typer.echo("No accounts yet, so the web workbench does not require login.")
        typer.echo("Create the first account: fr account invite you@company.cn --role admin")
        return
    for a in accounts:
        mark = "" if a.active else " (disabled)"
        typer.echo(f"{a.email:32} {', '.join(sorted(a.roles)) or '(no roles)':28}{mark}")


@account_app.command("grant")
def account_grant(email: str, role: str) -> None:
    """Grant someone a role. Roles add up; no inheritance tree - design §1.1."""
    _role_change(email, role, revoke=False)


@account_app.command("revoke")
def account_revoke(email: str, role: str) -> None:
    """Revoke someone's role. Revoking the last admin is refused."""
    _role_change(email, role, revoke=True)


def _role_change(email: str, role: str, *, revoke: bool) -> None:
    from framework_reader.identity.store import IdentityError

    store = _identity()
    account = store.by_email(email)
    if account is None:
        typer.echo(f"No such account: {email}")
        raise typer.Exit(1)
    try:
        if revoke:
            store.revoke(account.id, role, by="cli")
        else:
            store.grant(account.id, role, by="cli")
    except IdentityError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    store.log("role.revoke" if revoke else "role.grant", actor="cli",
              detail=f"{email} {role}")
    typer.echo(f"{email} now has: {', '.join(sorted(store.by_email(email).roles)) or '(no roles)'}")


@account_app.command("disable")
def account_disable(email: str) -> None:
    """Disable an account and kill its sessions immediately."""
    _status_change(email, "disabled")


@account_app.command("enable")
def account_enable(email: str) -> None:
    """Re-enable a disabled account."""
    _status_change(email, "active")


def _status_change(email: str, status: str) -> None:
    from framework_reader.identity.store import IdentityError

    store = _identity()
    account = store.by_email(email)
    if account is None:
        typer.echo(f"No such account: {email}")
        raise typer.Exit(1)
    try:
        store.set_status(account.id, status)
    except IdentityError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    store.log(f"account.{status}", actor="cli", detail=email)
    typer.echo(f"{email} → {status}")


secret_app = typer.Typer(help="Server master key. API keys stored in the DB are encrypted with it.")
app.add_typer(secret_app, name="secret")


@secret_app.command("new")
def secret_new() -> None:
    """Generate a master key. Printed once and never stored - storing it would mean nothing is encrypted."""
    from framework_reader import crypto

    typer.echo(crypto.new_master_key())
    typer.echo("")
    typer.echo(f"Set it as the environment variable {crypto.MASTER_ENV} before starting the server:")
    typer.echo(f"  export {crypto.MASTER_ENV}='the string above'")
    typer.echo("In production inject it from a secrets manager; never write it into any file or repo.")
    typer.echo("Rotating this key makes already-stored API keys undecryptable; re-enter them on the models page.")


@secret_app.command("status")
def secret_status() -> None:
    """Is the master key configured, and which vendor keys are stored (last four chars only)."""
    from framework_reader import crypto
    from framework_reader.llm.config import ModelConfig

    if crypto.configured():
        typer.echo(f"{crypto.MASTER_ENV}: configured")
    else:
        typer.echo(f"{crypto.MASTER_ENV}: **NOT configured**. Until it is, "
                   "API keys entered on the models page are never persisted.")
    stored = ModelConfig().masked()
    if not stored:
        typer.echo("No API keys stored yet, so drafting falls back to environment variables.")
        return
    for provider, row in sorted(stored.items()):
        typer.echo(f"  {provider:14} {row['masked']:12} "
                   f"{row['set_by'] or '-':24}{(row['set_at'] or '')[:10]}")


entra_app = typer.Typer(help="Entra ID (AAD) single sign-on. Configured via environment variables.")
app.add_typer(entra_app, name="entra")

ENTRA_ENV = """Set these five environment variables (never write the client secret into any file):
  FR_ENTRA_TENANT_ID      the id of your company's own tenant
  FR_ENTRA_CLIENT_ID      the client id of the app registration
  FR_ENTRA_CLIENT_SECRET  the confidential client's secret (a certificate beats a secret; secrets expire)
  FR_ENTRA_REDIRECT_URI   https://your-domain/auth/entra/callback
  FR_ENTRA_AUTHORITY      optional; defaults to https://login.microsoftonline.com"""


@entra_app.command("check")
def entra_check() -> None:
    """Fetch the discovery document once and point at the wrong config item. Makes a real network call."""
    from framework_reader.identity.entra import EntraClient, EntraConfig, EntraError

    config = EntraConfig.from_env()
    if not config.configured():
        typer.echo("Entra is not configured. Only email passcode sign-in works right now.\n")
        typer.echo(ENTRA_ENV)
        raise typer.Exit(1)

    problems = []
    if not config.client_secret:
        problems.append("FR_ENTRA_CLIENT_SECRET missing - the token exchange will fail")
    if not config.redirect_uri:
        problems.append("FR_ENTRA_REDIRECT_URI missing - it must match the redirect URI registered in Entra exactly")
    elif not config.redirect_uri.startswith("https://"):
        problems.append(f"Redirect URI is not https: {config.redirect_uri}"
                        " - session cookies would cross the network unencrypted")
    elif not config.redirect_uri.endswith("/auth/entra/callback"):
        problems.append(f"Redirect URI should end with /auth/entra/callback: {config.redirect_uri}")

    try:
        document = EntraClient(config).discovery()
    except EntraError as exc:
        typer.echo(f"Cannot fetch the discovery document: {exc}")
        raise typer.Exit(1) from exc
    typer.echo(f"tenant  {config.tenant_id}")
    typer.echo(f"issuer  {document['issuer']}")
    typer.echo(f"redirect  {config.redirect_uri or '(not set)'}")
    typer.echo("")
    typer.echo("Double-check these three in the Entra app registration:")
    typer.echo("  1. Account type is **single tenant**. Multi-tenant would let any Entra user reach your redirect URI")
    typer.echo("  2. The App Roles values are exactly admin / author / approver / viewer")
    typer.echo("  3. User assignment is on in the enterprise application - otherwise the whole company can sign in as viewer")
    if problems:
        typer.echo("")
        for line in problems:
            typer.echo(f"  ✗ {line}")
        raise typer.Exit(1)
    typer.echo("")
    typer.echo("The configuration looks complete. The real test is signing in once with a company account,"
               "then checking the roles in fr account list.")


@account_app.command("audit")
def account_audit(limit: int = 40) -> None:
    """Audit log: who did what when. Append-only; nothing is edited or deleted."""
    entries = _identity().audit(limit)
    if not entries:
        typer.echo("No entries yet.")
        return
    for e in entries:
        when = e["at"][:19].replace("T", " ")
        typer.echo(f"{when}  {e['event']:18} {e['actor'] or '-':28} {e['detail']}")


@app.command()
def serve(
    port: int = 8765,
    host: str = "127.0.0.1",
    reload: bool = False,
    db: Path = DEFAULT_DB,
) -> None:
    """Run the local workbench. Listens on 127.0.0.1 by default - data never leaves this machine.

    Use --reload while changing code: without it the process keeps running the code
    from startup time, new routes all 404, and it looks like "clicking does nothing".
    """
    import uvicorn

    typer.echo(f"Framework Workbench → http://{host}:{port}")
    if reload:
        typer.echo("Hot reload is on: no restart needed after code changes.")
    else:
        typer.echo("Note: restart this process after code changes - "
                   "uvicorn does not hot-reload (or pass --reload).")
    # Report on both paths. These lines used to print only on the non---reload path,
    # yet code changes are exactly when --reload is on: the run that most needs the
    # status said nothing.
    _startup_report(host, port)

    if reload:
        # Watch the source directory only. Without watchfiles, uvicorn falls back to
        # StatReload: pure-Python recursive stat of the cwd every second - tens of
        # thousands of files across .venv/build/.worktrees, measured at a steady
        # 40-60% CPU for nothing. Code changes happen in src/; watching that is enough.
        src_dir = str(Path(__file__).resolve().parent.parent)
        uvicorn.run(
            "framework_reader.web.app:create_app", factory=True,
            host=host, port=port, reload=True, log_level="warning",
            reload_dirs=[src_dir],
        )
        return
    from framework_reader.web.app import create_app

    uvicorn.run(create_app(db), host=host, port=port, log_level="warning")


def _startup_report(host: str, port: int) -> None:
    """The things worth seeing at a glance on startup: is the door locked, does SSO work, is the master key set.

    What they share: each one surfaces somewhere else as a bizarre symptom. An unlocked
    door shows up on the members page, unconfigured SSO on the login page, and a missing
    master key as "keys entered on the models page cannot be stored" - three steps away
    from the cause ("environment variable not loaded"). One line at startup is cheaper
    than half an hour staring at a 400.
    """
    from framework_reader import crypto
    from framework_reader.identity.entra import EntraConfig

    config = EntraConfig.from_env()
    if config.configured():
        typer.echo(f"Entra SSO: configured (tenant {config.tenant_id[:8]}...). "
                   "Roles come from App Roles; `fr entra check` verifies the setup.")
    elif _identity().configured():
        typer.echo("Account sign-in is enabled. Entra is not configured - `fr entra check` says what to set.")
    else:
        typer.echo("No accounts yet, so the workbench does not require login (single-user local use). "
                   f"To invite others, first create the first admin at http://{host}:{port}/members "
                   "(or `fr account invite you@company.cn --role admin`) - "
                   "once it exists, the door locks.")
        in_docker = Path("/.dockerenv").exists()
        if host not in ("127.0.0.1", "localhost", "::1") and not in_docker:
            # Open door + non-localhost bind = anyone on the network can race you to
            # the first admin, and once they create it the door locks - on you.
            # Inside Docker the bind must be 0.0.0.0 for compose's published port to
            # reach it; the real exposure is the host's FR_HTTP_BIND (default 127.0.0.1).
            typer.secho(
                f"⚠ Bound to {host} with no accounts yet: anyone who can reach this port "
                "can race you to the first admin and lock you out. "
                "Create the admin before exposing the port, or bind only 127.0.0.1.",
                fg=typer.colors.RED)

    if crypto.configured():
        return
    # No automatic .env loading here: auto-loading .env is a bad habit in production,
    # and this hint is enough to point at the cause.
    typer.secho(
        f"⚠ Missing {crypto.MASTER_ENV}: API keys entered on the models page cannot be stored "
        "(the app refuses rather than store them in the clear). Already-stored keys cannot be decrypted."
        f"For local development put it in the .env at the project root; before starting the server: `set -a; . ./.env; set +a`; "
        "generate one first with `fr secret new` if you do not have one yet.",
        fg=typer.colors.RED)


@app.command()
def stats(db: Path = DEFAULT_DB) -> None:
    """Print graph statistics."""
    for k, v in QueryAPI(db).stats().items():
        typer.echo(f"{k:24} {v}")


@app.command("sample-derived")
def sample_derived(
    n: int = 30, seed: int = 42,
    out: Path = Path("build/r7_sample.csv"), db: Path = DEFAULT_DB,
) -> None:
    """R7: sample derived edges for human accuracy review."""
    from framework_reader.query.sample import sample_derived_edges, write_review_sheet

    samples = sample_derived_edges(db, n=n, seed=seed)
    path = write_review_sheet(samples, out)
    typer.echo(f"Extracted {len(samples)} control(s) → {path}; fill in the verdict column (correct/wrong/partial)")


@app.command("golden")
def golden(action: str = typer.Argument(..., help="validate | diff")) -> None:
    """Golden samples: validate checks them, diff compares against interview output."""
    from framework_reader.interpret.compare import diff_against_golden, render_diff_table
    from framework_reader.interpret.golden import GOLDEN_CONTROLS, load_golden
    from framework_reader.interpret.store import InterpretationStore

    if action not in ("validate", "diff"):
        typer.echo(f"Unknown action: {action}")
        raise typer.Exit(2)

    store = InterpretationStore()
    for control_id in GOLDEN_CONTROLS:
        gold = load_golden(control_id)
        if action == "validate":
            filled = [
                n for n in ("common_myth", "auditor_asks", "regional_note")
                if gold.fields[n].value
            ]
            typer.echo(
                f"{control_id}  signed by={gold.provenance.confirmed_by}  differentiating fields filled={filled}"
            )
            continue
        if not store.exists(control_id):
            typer.echo(f"{control_id}  no interview output yet - run fr interview first")
            continue
        typer.echo(f"\n## {control_id}")
        typer.echo(render_diff_table(diff_against_golden(gold, store.load(control_id))))


@app.command("lint")
def lint(action: str = typer.Argument(..., help="calibrate | citations")) -> None:
    """calibrate: fit the extraction-fidelity threshold. citations: list clause numbers/percentages/years to verify."""
    if action == "citations":
        from framework_reader.interpret.lint import citation_flags
        from framework_reader.interpret.store import InterpretationStore

        total = 0
        for interp in InterpretationStore().iter_all():
            for name, hits in citation_flags(interp.fields).items():
                total += len(hits)
                typer.echo(f"{interp.control_id:26} {name:14} {', '.join(hits)}")
        typer.echo(
            f"\nTotal {total} citations to verify. Prompts forbid inventing clause numbers/percentages/years, but a ban cannot stop the model;"
            f"\nAfter checking, fix or delete them in place - this text is what compliance teams pay for."
        )
        raise typer.Exit(0)

    from framework_reader.interpret.golden import GOLDEN_CONTROLS
    from framework_reader.interpret.lint import field_scores, suggest_threshold
    from framework_reader.interpret.store import InterpretationStore

    if action != "calibrate":
        typer.echo(f"Unknown action: {action}")
        raise typer.Exit(2)

    store = InterpretationStore()
    scores: list[float] = []
    for control_id in GOLDEN_CONTROLS:
        if not store.exists(control_id):
            continue
        interp = store.load(control_id)
        per_field = field_scores(interp.fields, interp.interview.raw)
        for name, score in sorted(per_field.items()):
            typer.echo(f"{control_id:26} {name:14} {score:.3f}")
            scores.append(score)
    if not scores:
        typer.echo("No interview output available to calibrate")
        raise typer.Exit(1)
    typer.echo(
        f"\nAfter eyeballing each one as a faithful extraction, set content/lint.yaml "
        f"bigram_threshold to {suggest_threshold(scores):.2f}"
    )


@app.command("llm")
def llm(action: str = typer.Argument(..., help="check")) -> None:
    """Probe every vendor preset. Sends real requests; run manually."""
    import os

    from framework_reader.llm.client import Message
    from framework_reader.llm.guard import PayloadGuard
    from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry

    if action != "check":
        typer.echo(f"Unknown action: {action}")
        raise typer.Exit(2)

    from framework_reader.llm.config import ModelConfig

    registry = LLMRegistry.load(DEFAULT_REGISTRY_PATH)
    guard = PayloadGuard([])
    # Check admin-configured keys in the store first, then fall back to environment
    # variables - otherwise the web says "configured" while this says "not set".
    key_lookup = ModelConfig().key_lookup()
    for preset in registry.providers:
        if not key_lookup(preset.api_key_env):
            typer.echo(f"{preset.id:14} skipped ({preset.api_key_env} not set, and none in the store)")
            continue
        probe = LLMRegistry(
            providers=[preset],
            roles={"probe": {"provider": preset.id, "model": preset.default_model}},
        ).build("probe", guard=guard, key_lookup=key_lookup)
        try:
            probe.complete(
                "", [Message(role="user", content="ping")],
                model=preset.default_model, max_tokens=8,
            )
            typer.echo(f"{preset.id:14} OK   {preset.default_model}")
        except Exception as exc:  # one vendor failing must not stop the others
            typer.echo(f"{preset.id:14} FAIL {type(exc).__name__}: {exc}")


@app.command("draft")
def draft(
    framework_id: str = "NIST-CSF-2.0",
    jobs: int = 4,
    force: bool = False,
    all_: bool = typer.Option(
        False,
        "--all",
        help="Drafts the whole framework by default; a no-op kept for Makefile compatibility",
    ),
    only: list[str] = typer.Option(
        None,
        "--only",
        help="Draft only these control_ids, repeatable. Before a vendor is chosen, draft the ones you need",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Route B: write all seven fields in one pass (including the three differentiating fields), all marked inferred",
    ),
    db: Path = DEFAULT_DB,
) -> None:
    """Batch-draft the four non-differentiating fields. Runs concurrently, offline."""
    from framework_reader.interpret.run import UnknownFrameworkError, draft_framework

    _ = all_  # no-op: draft already batches the whole framework

    try:
        report = draft_framework(
            db, framework_id, jobs=jobs, force=force,
            only=list(only) if only else None, full=full,
        )
    except UnknownFrameworkError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    typer.echo(f"Drafted: {len(report.written)} control(s)")
    if report.failed:
        typer.echo(f"Failed: {len(report.failed)} control(s) (raw responses in build/draft_failures/):")
        for failure in report.failed:
            typer.echo(f"  {failure.control_id}  {failure.reason[:110]}")
        typer.echo("Re-run without --force to fill in just these.")


def _short(value, limit: int = 90) -> str:
    """Collapse a field value onto one line for terminal comparison."""
    if value is None:
        return "(empty)"
    if isinstance(value, list):
        text = " / ".join(str(v) for v in value)
    elif isinstance(value, dict):
        text = " / ".join(f"{k}.{v}" for k, v in sorted(value.items()))
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


@app.command("proofread")
def proofread(
    framework_id: str = "NIST-CSF-2.0",
    only: list[str] = typer.Option(None, "--only", help="Proofread only these control_ids, repeatable"),
    jobs: int = 4,
    dry_run: bool = typer.Option(False, "--dry-run", help="Report only, write nothing"),
    db: Path = DEFAULT_DB,
) -> None:
    """Proofread pass: language only, never content. Suspicious edits are blocked and reported, not written."""
    from concurrent.futures import ThreadPoolExecutor

    from framework_reader.interpret.proofread import proofread_fields
    from framework_reader.interpret.user_store import store_for
    from framework_reader.llm.guard import PayloadGuard
    from framework_reader.llm.registry import (
        DEFAULT_REGISTRY_PATH,
        LLMRegistry,
        MissingApiKeyError,
    )

    # Pick the store per framework. Hardwiring InterpretationStore makes proofreading
    # an imported framework always report "nothing to proofread" - its interpretations
    # never live in the content repo.
    store = store_for(QueryAPI(db).get_framework(framework_id))
    targets = [
        i for i in store.iter_all()
        if i.control_id.startswith(f"{framework_id}:")
        and (not only or i.control_id in set(only))
    ]
    if not targets:
        typer.echo("No interpretations to proofread")
        raise typer.Exit(0)

    registry = LLMRegistry.load(DEFAULT_REGISTRY_PATH)
    try:
        client = registry.build("drafter", guard=PayloadGuard([]))
    except MissingApiKeyError as exc:
        typer.echo(str(exc))
        raise typer.Exit(2)
    model = registry.role("drafter").model

    def one(interp):
        try:
            fields, flags = proofread_fields(
                client, control_id=interp.control_id, fields=interp.fields, model=model
            )
        except Exception as exc:
            return interp.control_id, None, [], f"{type(exc).__name__}: {exc}"
        diffs = [
            (n, _short(interp.fields[n].value), _short(fields[n].value))
            for n in fields
            if fields[n].value != interp.fields[n].value
        ]
        if diffs and not dry_run:
            interp.fields = fields
            store.save(interp)
        return interp.control_id, diffs, flags, None

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        results = list(pool.map(one, targets))

    changed_total = flagged_total = failed_total = 0
    for control_id, diffs, flags, error in sorted(results):
        if error:
            failed_total += 1
            typer.echo(f"[FAILED] {control_id}  {error[:100]}")
            continue
        changed_total += bool(diffs)
        if dry_run:
            for name, before, after in diffs:
                typer.echo(f"[WOULD CHANGE] {control_id}  {name}")
                typer.echo(f"        before: {before}")
                typer.echo(f"        after: {after}")
        for flag in flags:
            flagged_total += 1
            typer.echo(f"[BLOCKED] {control_id}  {flag.field}  {flag.reason}")
            typer.echo(f"        before: {flag.before[:70]}")
            typer.echo(f"        after: {flag.after[:70]}")
    verb = "would change" if dry_run else "changed"
    typer.echo(
        f"\nTotal {len(targets)} control(s): {verb} {changed_total} control(s), "
        f"blocked {flagged_total} suspicious edits, failed {failed_total} control(s)"
    )
    if flagged_total:
        typer.echo("Blocked edits were not written to disk. To adopt them, edit the files by hand; otherwise ignore them.")


def _refuse_imported(control_id: str | None) -> None:
    """Interviews are the author's tool; running one on an imported framework would write the user's interpretations into the content repo.

    Blocked before assembly - blocking after the model call means the money is already
    spent. The write-side gate (InterpretationStore's content root guard) would also
    catch it, but by then the user has answered all three questions for nothing.
    """
    if not control_id:
        return
    from framework_reader.userframework.store import UserFrameworkStore

    framework_id = control_id.split(":", 1)[0]
    if framework_id not in {f.id for f in UserFrameworkStore().list_frameworks()}:
        return
    typer.echo(
        f"{framework_id} is a framework you imported; interviews do not work on it - "
        "interviews write to content/interpretations/, which is content the product publishes.\n"
        "edit your framework in the web workbench: fr serve → open the control → the \"Edit\" button next to each field,"
        "\nthen hit \"I confirm this control\" to record the signer and the time."
    )
    raise typer.Exit(2)


@app.command("interview")
def interview(
    control_id: str | None = typer.Argument(None),
    next_: bool = typer.Option(False, "--next", help="Pick up the next draft automatically"),
    force: bool = typer.Option(
        False, "--force", help="Allow re-running the interview and extraction on already interviewed/confirmed controls"
    ),
    signer: str = typer.Option(
        "", "--signer", help="Signer name; defaults to your local username - whoever runs it signs"
    ),
    db: Path = DEFAULT_DB,
) -> None:
    """Interview one control: three questions, extraction, sign-off in $EDITOR.

    This is the **author's signing tool for built-in frameworks**: it writes to
    content/interpretations/, which goes into git and gets reviewed. Imported
    frameworks do not go through here - see the guard below.
    """
    import getpass
    import sqlite3
    from datetime import datetime, timezone

    from prompt_toolkit import prompt as ptk_prompt

    from framework_reader.cli.interview import (
        already_done_message,
        default_editor,
        render_header,
        run_editor,
        run_interview,
    )
    from framework_reader.interpret.compare import LintConfig
    from framework_reader.interpret.model import InterpretationState
    from framework_reader.interpret.session import InterviewSession
    from framework_reader.interpret.store import InterpretationStore
    from framework_reader.llm.guard import PayloadGuard, forbidden_texts_from_db
    from framework_reader.llm.registry import (
        DEFAULT_REGISTRY_PATH,
        LLMRegistry,
        MissingApiKeyError,
    )
    from framework_reader.prompts import PROMPT_VERSIONS, full_drafter_version

    signer = signer or getpass.getuser()
    _refuse_imported(control_id)
    store = InterpretationStore()
    if next_ or control_id is None:
        drafts = store.by_state(InterpretationState.DRAFT)
        if not drafts:
            typer.echo("No drafts waiting for an interview")
            raise typer.Exit(0)
        control_id = drafts[0].control_id

    if store.exists(control_id):
        msg = already_done_message(store.load(control_id), force=force)
        if msg:
            typer.echo(msg)
            raise typer.Exit(1)

    registry = LLMRegistry.load(DEFAULT_REGISTRY_PATH)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    guard = PayloadGuard(forbidden_texts_from_db(conn))
    conn.close()

    api = QueryAPI(db)
    try:
        questioner = registry.build("questioner", guard=guard)
        extractor = registry.build("extractor", guard=guard)
    except MissingApiKeyError as exc:
        # W3 runs this command 106 times; a missing key must not dump a screen of traceback.
        typer.echo(f"{exc}\nExport that variable first, or change the roles in content/llm_providers.yaml")
        raise typer.Exit(2)

    session = InterviewSession(
        store,
        questioner,
        extractor,
        outcome_lookup=lambda cid: (api.get_control(cid).label if api.get_control(cid) else ""),
        questioner_model=registry.role("questioner").model,
        extractor_model=registry.role("extractor").model,
        extractor_provider=registry.role("extractor").provider,
        extractor_prompt_version=PROMPT_VERSIONS["extractor"],
    )

    control = api.get_control(control_id)
    leaves = [c.id for c in api.list_controls(control_id.split(":", 1)[0], leaf_only=True)]
    index = leaves.index(control_id) + 1 if control_id in leaves else 0
    typer.echo(
        render_header(store.load(control_id), control.label if control else "",
                      index, len(leaves))
    )

    try:
        elapsed = run_interview(
            store, session, control_id,
            ask=lambda q: ptk_prompt(f" [{q.n}/3] {q.text}\n ▸ ", multiline=False),
            edit=lambda path: run_editor(path, default_editor()),
            signer=signer,
            now=lambda: datetime.now(timezone.utc),
            threshold=LintConfig.load().bigram_threshold,
        )
    except KeyboardInterrupt:
        typer.echo(f"\nAnswers so far are saved to disk; re-run fr interview {control_id} to continue")
        raise typer.Exit(130)

    typer.echo(f"{control_id} signed · this control took {elapsed / 60:.1f} minutes")


@app.command("blindtest")
def blindtest(
    action: str = typer.Argument(..., help="prepare | repacket | tally | report"),
    seed: int = 42,
    judge: str = "",
    picks: str = "",
    note: str = "",
    n: int = 10,
    db: Path = DEFAULT_DB,
) -> None:
    """Blind test: prepare builds the packet, repacket re-renders it, tally records verdicts, report concludes."""
    import json

    from framework_reader.blindtest.packet import (
        AnswerKey, PacketItem, build_packet, load_cached_items,
    )
    from framework_reader.blindtest.sample import (
        eligible_for_sample, frame_drift, frame_fingerprint, stratified_sample,
    )
    from framework_reader.blindtest.tally import (
        Verdict, build_report, parse_picks, render_report, safe_judge_filename,
    )
    from framework_reader.blindtest.variants import (
        MappingRef, leak_hits, render_bare, render_original, render_product,
    )
    from framework_reader.interpret.store import InterpretationStore
    from framework_reader.llm.guard import PayloadGuard
    from framework_reader.llm.registry import DEFAULT_REGISTRY_PATH, LLMRegistry
    from framework_reader.prompts import PROMPT_VERSIONS

    room = BLINDTEST_DIR / str(seed)
    key_path = room / "answer_key.json"

    if action == "prepare":
        store = InterpretationStore()
        api = QueryAPI(db)
        ids = [i.control_id for i in store.iter_all()]
        outcomes = {}
        for cid in ids:
            control = api.get_control(cid)
            outcomes[cid] = control.label if control else ""
        frame = eligible_for_sample(ids, outcomes)
        eligible = set(frame)
        skipped = {
            cid: leak_hits(outcomes.get(cid, "")) for cid in ids if cid not in eligible
        }
        for cid, hits in skipped.items():
            typer.echo(f"Excluded from sampling: {cid} - the original text contains leak words {hits}", err=True)
        if key_path.exists():
            drift = frame_drift(
                AnswerKey(**json.loads(key_path.read_text(encoding="utf-8"))), frame
            )
            if drift:
                # Quietly drawing a different question set is the hardest cheat to
                # notice yourself. Stop instead. spec §3
                typer.echo(f"{drift}; to regenerate anyway, delete {room}")
                raise typer.Exit(1)
        picked = stratified_sample(frame, n, seed)
        registry = LLMRegistry.load(DEFAULT_REGISTRY_PATH)
        model = registry.role("drafter").model

        # Exportable edges only. L2 derived edges sampled 17% correct in R7; they
        # never reach the judges. Main spec §3.3
        names: dict[str, str] = {}

        def mappings_for(control_id: str) -> list[MappingRef]:
            refs = []
            for neighbor in api.neighbors(control_id, exportable_only=True):
                framework_id = neighbor.control_id.split(":", 1)[0]
                if framework_id not in names:
                    view = api.get_framework(framework_id)
                    names[framework_id] = view.name if view else framework_id
                refs.append(MappingRef(
                    control_id=neighbor.control_id, label=neighbor.label,
                    framework=names[framework_id], relation=neighbor.relation,
                    source=neighbor.source, level=neighbor.level,
                ))
            return refs

        room.mkdir(parents=True, exist_ok=True)
        raw_path = room / "variants.json"
        client = registry.build("drafter", guard=PayloadGuard([]))
        items = []
        for control_id in picked:
            outcome = outcomes.get(control_id, "")
            items.append(PacketItem(
                control_id=control_id,
                product=render_product(
                    store.load(control_id), mappings=mappings_for(control_id)
                ),
                bare=render_bare(
                    client, control_id=control_id, outcome=outcome, model=model
                ),
                original=render_original(outcome),
            ))
            # Persist after each item: if build_packet's leak assertion fails, the
            # model calls already paid for must not be wasted.
            raw_path.write_text(
                json.dumps([i.model_dump() for i in items], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        text, key = build_packet(
            items, seed,
            bare_model=model, bare_prompt_version=PROMPT_VERSIONS["bare_llm"],
            excluded=skipped, frame_fingerprint=frame_fingerprint(frame),
        )
        (room / "packet.md").write_text(text, encoding="utf-8")
        key_path.write_text(
            key.model_dump_json(indent=2), encoding="utf-8"
        )
        typer.echo(f"Wrote {len(items)} control(s) → {room / 'packet.md'} (this one goes to the judges)")
        typer.echo(f"Answer key → {key_path} (do NOT send this one)")
        raise typer.Exit(0)

    if not key_path.exists():
        typer.echo(f"No items for seed={seed} - run fr blindtest prepare --seed {seed}")
        raise typer.Exit(1)
    key = AnswerKey(**json.loads(key_path.read_text(encoding="utf-8")))

    if action == "repacket":
        # Re-render the questionnaire only (e.g. changed instructions): no resampling,
        # no model calls. Resampling swaps the questions, re-calling the model swaps
        # the bare-LLM text - either way it is a different exam.
        items = load_cached_items(room / "variants.json", key.order)
        if items is None:
            typer.echo(
                f"{room / 'variants.json'} is missing or does not match answer_key; cannot re-render"
            )
            raise typer.Exit(1)
        text, fresh = build_packet(
            items, seed,
            bare_model=key.bare_model, bare_prompt_version=key.bare_prompt_version,
            excluded=key.excluded,
        )
        if fresh.mapping != key.mapping:
            # Once the letters in the judges' hands change, every returned verdict is
            # void. Prefer producing nothing.
            typer.echo("Re-rendering would change the A/B/C-to-variant mapping; stopped - answer_key untouched")
            raise typer.Exit(1)
        (room / "packet.md").write_text(text, encoding="utf-8")
        typer.echo(f"Re-rendered {len(items)} control(s) → {room / 'packet.md'} (answer key unchanged)")
        raise typer.Exit(0)

    if action == "tally":
        if not judge or not picks:
            typer.echo("Need --judge and --picks, e.g. --picks \"1=A,2=C\"")
            raise typer.Exit(2)
        verdict = Verdict(judge=judge, picks=parse_picks(picks), note=note)
        out = room / "verdicts"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{safe_judge_filename(judge)}.json").write_text(
            verdict.model_dump_json(indent=2), encoding="utf-8"
        )
        typer.echo(f"Recorded {judge}'s {len(verdict.picks)} verdicts")
        raise typer.Exit(0)

    if action == "report":
        files = sorted((room / "verdicts").glob("*.json")) if (room / "verdicts").exists() else []
        if not files:
            typer.echo("No judge verdicts yet")
            raise typer.Exit(1)
        verdicts = [
            Verdict(**json.loads(p.read_text(encoding="utf-8"))) for p in files
        ]
        typer.echo(render_report(build_report(key, verdicts)))
        raise typer.Exit(0)

    typer.echo(f"Unknown action: {action}")
    raise typer.Exit(2)


if __name__ == "__main__":
    app()

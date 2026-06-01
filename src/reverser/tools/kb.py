"""Read-side and editorial tools the LLM uses to inspect/annotate the KB."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from claude_agent_sdk import tool

from ..kb import (
    AuthorizationError,
    FindingFact,
    for_target,
    list_targets,
    require_pentest_auth,
)
from ._common import format_error, format_tool_result
from ..schemas.models import (
    FindingModel,
    HypothesisModel,
    HypothesisUpdateModel,
    ReportModel,
)
from ..schemas.validation import validate_args, tool_input_schema
from ..adversary import run_adversary_validation


def _resolve_target(target: str | None):
    """Return a normalized target name or an MCP error result.

    If `target` is provided, return it. If `target` is None and exactly one
    target subdir exists, return it. Otherwise return (None, error_dict).
    """
    if target:
        return target
    candidates = list_targets()
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None, format_error(
            "No targets found in REVERSER_TARGETS_DIR. "
            "Provide a target argument explicitly."
        )
    return None, format_error(
        "Multiple targets present — pass `target` explicitly. "
        "Available: " + ", ".join(candidates)
    )


def _check_auth() -> dict | None:
    try:
        require_pentest_auth()
        return None
    except AuthorizationError as e:
        return format_error(str(e))


@tool(
    "kb_show",
    "Single-screen overview of the per-target knowledge base: hosts (count and "
    "OS breakdown), top 10 ports, valid credentials count + most recent, finding "
    "count by severity. If `target` is omitted and exactly one target has been "
    "started, defaults to it; otherwise errors with the available list.",
    {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Normalized target identifier (IP/hostname/CIDR). Optional.",
                "default": "",
            },
        },
    },
)
async def kb_show(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    target_arg = args.get("target", "") or None
    resolved = _resolve_target(target_arg)
    if isinstance(resolved, tuple):
        return resolved[1]
    target = resolved

    kb = for_target(target)
    hosts = kb.get_hosts()
    services = kb.get_services()
    creds = kb.get_credentials()
    valid_creds = [c for c in creds if c.status == "valid"]
    findings = kb.get_findings()
    artifacts = kb.get_artifacts()
    notes = kb.get_notes()

    port_counter = Counter(s.port for s in services)
    top_ports = port_counter.most_common(10)
    os_counter = Counter((h.os or "unknown") for h in hosts)
    sev_counter = Counter(f.severity for f in findings)

    lines = [
        f"# KB summary — {target}",
        "",
        f"Hosts: {len(hosts)}",
    ]
    for os_name, n in os_counter.most_common():
        lines.append(f"  - {os_name}: {n}")
    if hosts:
        lines.append("Recorded hosts:")
        for h in hosts[:10]:
            details = []
            if h.hostname:
                details.append(f"hostname={h.hostname}")
            if h.domain:
                details.append(f"domain={h.domain}")
            if h.os:
                details.append(f"os={h.os}")
            if h.is_dc:
                details.append("dc=yes")
            suffix = f" ({', '.join(details)})" if details else ""
            lines.append(f"  - {h.ip}{suffix}")
        if len(hosts) > 10:
            lines.append(f"  - ... {len(hosts) - 10} more")
    lines.append("")
    lines.append(f"Services: {len(services)}")
    if top_ports:
        lines.append("Top ports:")
        for port, count in top_ports:
            lines.append(f"  - {port}: {count}")
    lines.append("")
    lines.append(f"Credentials: {len(creds)} total, {len(valid_creds)} valid")
    if valid_creds:
        most_recent = valid_creds[-1]
        lines.append(
            f"  - Most recent valid: {most_recent.username}"
            f" (source: {most_recent.source_tool or '?'})"
        )
    lines.append("")
    lines.append(f"Findings: {len(findings)}")
    for sev in ("critical", "high", "medium", "low", "info"):
        if sev_counter.get(sev):
            lines.append(f"  - {sev}: {sev_counter[sev]}")

    lines.append("")
    lines.append(f"Artifacts: {len(artifacts)}")
    for artifact in artifacts[:5]:
        sha = f" sha256={artifact.sha256[:12]}..." if artifact.sha256 else ""
        source = f" source={artifact.source_tool}" if artifact.source_tool else ""
        lines.append(f"  - {artifact.kind}: {artifact.path}{sha}{source}")
    if len(artifacts) > 5:
        lines.append(f"  - ... {len(artifacts) - 5} more")

    if notes:
        lines.append("")
        lines.append("Recent notes:")
        for note in notes[-3:]:
            one_line = " ".join(note.split())
            lines.append(f"  - {one_line[:240]}")

    return format_tool_result("\n".join(lines))


TOOLS = [kb_show]


@tool(
    "kb_list_hosts",
    "List every host in the KB for `target`: ip, hostname, OS, domain, "
    "is_dc, smb_signing.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
        },
        "required": ["target"],
    },
)
async def kb_list_hosts(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args["target"]
    kb = for_target(target)
    hosts = kb.get_hosts()
    if not hosts:
        return format_tool_result(f"No hosts recorded for {target} (0 rows)")
    lines = [f"# Hosts for {target} ({len(hosts)} rows)", ""]
    lines.append(f"{'IP':<18}{'HOSTNAME':<28}{'OS':<32}{'DOMAIN':<20}{'DC':<5}SIGNING")
    lines.append("-" * 110)
    for h in hosts:
        lines.append(
            f"{h.ip:<18}{(h.hostname or '-'):<28}{(h.os or '-')[:31]:<32}"
            f"{(h.domain or '-'):<20}{('yes' if h.is_dc else 'no'):<5}{h.smb_signing or '-'}"
        )
    return format_tool_result("\n".join(lines))


TOOLS.append(kb_list_hosts)


@tool(
    "kb_list_services",
    "List every service in the KB for `target`. Optional `host` and `port` filters.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
            "host": {"type": "string", "description": "Filter by host IP.", "default": ""},
            "port": {"type": "integer", "description": "Filter by port.", "default": 0},
        },
        "required": ["target"],
    },
)
async def kb_list_services(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args["target"]
    host = args.get("host", "") or None
    port = args.get("port", 0) or None
    kb = for_target(target)
    services = kb.get_services(host_ip=host, port=port)
    if not services:
        return format_tool_result(f"No services match for {target} (0 rows)")
    lines = [f"# Services for {target} ({len(services)} rows)", ""]
    lines.append(f"{'HOST':<18}{'PORT':<6}{'PROTO':<6}{'SERVICE':<20}{'VERSION':<40}SOURCE")
    lines.append("-" * 100)
    for s in services:
        lines.append(
            f"{s.host_ip:<18}{s.port:<6}{s.proto:<6}{(s.service or '-'):<20}"
            f"{(s.version or '-')[:39]:<40}{s.scan_source or '-'}"
        )
    return format_tool_result("\n".join(lines))


TOOLS.append(kb_list_services)


@tool(
    "kb_list_creds",
    "List credentials in the KB for `target`. Optional `status` filter "
    "(untested|invalid|valid). For each cred, shows username, status, "
    "source tool, and the services where it has been validated.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
            "status": {
                "type": "string",
                "description": "Filter by status.",
                "enum": ["untested", "invalid", "valid"],
                "default": "",
            },
        },
        "required": ["target"],
    },
)
async def kb_list_creds(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args["target"]
    status = args.get("status", "") or None
    kb = for_target(target)
    creds = kb.get_credentials(status=status)
    if not creds:
        return format_tool_result(f"No credentials match for {target} (0 rows)")

    rows_with_id = []
    with kb._connect() as conn:
        cursor = conn.execute(
            "SELECT id, username, password, nt_hash, kerberos_ticket, domain, "
            "source_tool, source_context, status FROM credentials WHERE target_id = ?"
            + (" AND status = ?" if status else "") + " ORDER BY id",
            ([kb.target_id, status] if status else [kb.target_id]),
        )
        rows_with_id = cursor.fetchall()

    lines = [f"# Credentials for {target} ({len(rows_with_id)} rows)", ""]
    lines.append(f"{'USER':<24}{'DOMAIN':<16}{'STATUS':<10}{'MATERIAL':<14}"
                 f"{'SOURCE':<18}WORKS-ON")
    lines.append("-" * 110)
    for row in rows_with_id:
        cid, user, pw, nt, krb, domain, source_tool, _source_ctx, st = row
        material = "password" if pw else ("nt_hash" if nt else ("krb" if krb else "-"))
        results = kb.get_cred_results(cid)
        works = ", ".join(
            f"{r.service_kind}@{r.target_host}{'+' if r.success else '-'}"
            for r in results
        ) or "-"
        lines.append(
            f"{user[:23]:<24}{(domain or '-')[:15]:<16}{st:<10}{material:<14}"
            f"{(source_tool or '-')[:17]:<18}{works}"
        )
    return format_tool_result("\n".join(lines))


TOOLS.append(kb_list_creds)


def _finding_tool_schema() -> dict:
    """Build the @tool input schema for kb_add_finding by prepending `target`
    to the FindingModel-derived schema."""
    base = tool_input_schema(FindingModel)
    props = {"target": {"type": "string", "description": "Normalized target identifier."}}
    props.update(base["properties"])
    return {
        "type": "object",
        "properties": props,
        "required": ["target", *base["required"]],
    }


@tool(
    "kb_add_finding",
    "Record a new finding in the KB. Requires evidence_paths (>=1) OR an "
    "evidence_blocker explaining why none exist, plus reproduction, confidence "
    "(0-100), and reachability (demonstrated|likely|theoretical|unknown).",
    _finding_tool_schema(),
)
async def kb_add_finding(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args.get("target")
    if not target:
        return format_error("target is required.")
    model_args = {k: v for k, v in args.items() if k != "target"}
    outcome = validate_args(FindingModel, model_args)
    if not outcome.ok:
        return format_error(outcome.error_text)
    m = outcome.value
    finding = FindingFact(
        title=m.title,
        severity=m.severity.value,
        description=m.description,
        evidence_paths=m.evidence_paths,
        cvss=m.cvss,
        reproduction=m.reproduction,
        reachability=m.reachability.value,
        confidence=m.confidence,
        evidence_blocker=m.evidence_blocker,
        validated=m.validated,
    )
    fid = for_target(target).record_finding(finding)
    from ..gui_service.kb_emitter import emit_recorded_finding
    emit_recorded_finding("create", fid, finding)
    suffix = "" if m.validated else " (stored UNVALIDATED — evidence_blocker set)"
    return format_tool_result(f"Finding added: id={fid} title={finding.title!r}{suffix}")


TOOLS.append(kb_add_finding)


@tool(
    "kb_add_note",
    "Append a free-form note to the KB scratchpad for `target`. Use for "
    "hypotheses, leads, observations, methodology decisions.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
            "body": {"type": "string", "description": "Note body (any length)."},
        },
        "required": ["target", "body"],
    },
)
async def kb_add_note(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    for_target(args["target"]).record_note(args["body"])
    return format_tool_result("Note recorded.")


TOOLS.append(kb_add_note)


@tool(
    "kb_add_hypothesis",
    "Add a new hypothesis to the engagement's attack tree. Returns the new id. "
    "Use parent_id to link to a parent hypothesis you're refining. confidence is "
    "0-100. tags is a list of free-form labels.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
            "statement": {"type": "string", "description": "What you're hypothesizing."},
            "parent_id": {"type": "integer", "description": "Parent hypothesis id (optional)."},
            "rationale": {"type": "string", "description": "Why you're proposing this."},
            "confidence": {"type": "integer", "description": "0-100 confidence."},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["target", "statement", "rationale", "confidence"],
    },
)
async def kb_add_hypothesis(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    model_args = {k: v for k, v in args.items() if k != "target"}
    outcome = validate_args(HypothesisModel, model_args)
    if not outcome.ok:
        return format_error(outcome.error_text)
    m = outcome.value
    h = for_target(args["target"]).add_hypothesis(
        statement=m.statement,
        parent_id=m.parent_id,
        rationale=m.rationale,
        confidence=m.confidence,
        tags=m.tags,
    )
    from ..gui_service.kb_emitter import emit_hypothesis
    if h is not None:
        emit_hypothesis("create", h)
    return format_tool_result(
        f"Hypothesis #{h.id} added (status={h.status}, confidence={h.confidence}): "
        f"{h.statement}"
    )


TOOLS.append(kb_add_hypothesis)


@tool(
    "kb_get_hypothesis",
    "Fetch a single hypothesis with its full record and a list of its child "
    "hypothesis ids. Use this to inspect a specific node of the attack tree.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "id": {"type": "integer", "description": "Hypothesis id."},
        },
        "required": ["target", "id"],
    },
)
async def kb_get_hypothesis(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    kb = for_target(args["target"])
    h = kb.get_hypothesis(args["id"])
    if h is None:
        return format_tool_result(f"No hypothesis with id={args['id']}.")
    children = kb.list_hypotheses(parent_id=h.id)
    lines = [
        f"# Hypothesis #{h.id}",
        f"**Statement:** {h.statement}",
        f"**Status:** {h.status}",
        f"**Confidence:** {h.confidence if h.confidence is not None else '—'}",
        f"**Parent:** {h.parent_id if h.parent_id else '—'}",
        f"**Dispatched to:** {h.dispatched_to or '—'}",
        f"**Dispatch count:** {h.dispatch_count}",
        f"**Tags:** {', '.join(h.tags) if h.tags else '—'}",
    ]
    if h.rationale:
        lines.append(f"**Rationale:** {h.rationale}")
    if h.evidence_refs:
        lines.append(f"**Evidence refs:** {h.evidence_refs}")
    if children:
        lines.append("")
        lines.append(f"**Children (ids):** {[c.id for c in children]}")
    return format_tool_result("\n".join(lines))


TOOLS.append(kb_get_hypothesis)


def _serialize_evidence_for_validation(kb, hypothesis, evidence_refs) -> str:
    """Compact text of a hypothesis + its dereferenced evidence for the adversary."""
    lines = [f"Hypothesis: {getattr(hypothesis, 'statement', '')}"]
    if getattr(hypothesis, "rationale", None):
        lines.append(f"Rationale: {hypothesis.rationale}")
    refs = list(evidence_refs or [])
    if getattr(hypothesis, "evidence_refs", None):
        refs = refs + list(hypothesis.evidence_refs)
    seen = set()
    for item in kb.resolve_evidence_refs(refs):
        key = (item["kind"], item["id"])
        if key in seen:
            continue
        seen.add(key)
        data = item.get("data") or {}
        if item["kind"] == "finding":
            lines.append(f"- finding: {str(data.get('title', ''))[:120]} "
                         f"[{data.get('severity', '')}] {str(data.get('description', ''))[:300]}")
        elif item["kind"] == "note":
            body = data.get("body") if isinstance(data, dict) else str(data)
            lines.append(f"- note: {str(body)[:300]}")
        elif item["kind"] == "credential":
            lines.append(f"- cred: {data.get('username', '')}@{data.get('domain', '') or '-'} "
                         f"({data.get('status', '')})")
        elif item["kind"] == "service":
            lines.append(f"- service: {data.get('host_ip', '')}:{data.get('port', '')} "
                         f"{data.get('service', '')}")
    return "\n".join(lines)[:4000]


@tool(
    "kb_update_hypothesis",
    "Update fields on an existing hypothesis. Pass only the fields you want to "
    "change. Common transitions: status='testing' when dispatching, "
    "status='confirmed'/'refuted' when a dispatch returns (or 'abandoned' to "
    "drop a line of inquiry, 'blocked' when you cannot proceed). "
    "evidence_refs is a list of {kind, id} dicts pointing into the KB.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "id": {"type": "integer"},
            "status": {
                "type": "string",
                "enum": ["proposed", "testing", "confirmed", "refuted", "abandoned", "blocked"],
            },
            "rationale": {"type": "string"},
            "confidence": {"type": "integer"},
            "dispatched_to": {"type": "string"},
            "evidence_refs": {
                "type": "array",
                "items": {"type": "object"},
                "description": "List of {kind: 'finding'|'note'|'credential'|'service', id: int}",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["target", "id"],
    },
)
async def kb_update_hypothesis(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    kb = for_target(args["target"])
    current = kb.get_hypothesis(args["id"])
    if current is None:
        return format_error(f"No hypothesis with id={args['id']}.")
    new_status = args.get("status", current.status)
    outcome = validate_args(HypothesisUpdateModel, {
        "from_status": current.status,
        "to_status": new_status,
        "rationale": args.get("rationale", "") or "",
        "confidence": args.get("confidence"),
        "evidence_refs": args.get("evidence_refs", []) or [],
    })
    if not outcome.ok:
        return format_error(outcome.error_text)
    _validation_suffix = ""
    # ── Adversarial validation gate (opt-in) ─────────────────────────
    # Before promoting to 'confirmed', a read-only second-model skeptic tries to
    # REFUTE the hypothesis from the KB evidence. 'refuted' hard-blocks (not
    # persisted); otherwise the verdict is recorded and we proceed. Skipped if no
    # validator configured; fails open on adversary error.
    if new_status == "confirmed":
        try:
            from ..sessions import current_session
            sess = current_session.get()
        except Exception:
            sess = None
        vbackend = getattr(getattr(sess, "config", None), "validation_backend", None)
        if vbackend:
            vmodel = getattr(sess.config, "validation_model", None)
            vmodel_label = vmodel or "(backend default)"
            vapi = getattr(sess.config, "validation_api_base", None)
            evidence_text = _serialize_evidence_for_validation(
                kb, current, args.get("evidence_refs"))
            try:
                verdict = await run_adversary_validation(
                    claim=current.statement, evidence_text=evidence_text,
                    backend_name=vbackend, model=vmodel, api_base=vapi)
            except Exception as e:
                kb.record_note(
                    f"Adversarial validation unavailable for hyp #{args['id']} "
                    f"({type(e).__name__}: {e}); confirmed without it.")
                verdict = None
            if verdict is not None and verdict.verdict == "refuted":
                kb.record_note(
                    f"Adversarial validation REFUTED hyp #{args['id']} "
                    f"(model={vmodel_label}): {verdict.reasoning}")
                return format_error(
                    "Adversarial validation refused the 'confirmed' transition: "
                    f"{verdict.reasoning}. Revise the hypothesis/evidence, gather more, "
                    "or use status='testing'/'inconclusive'.")
            if verdict is not None:
                kb.record_note(
                    f"Adversarial validation {verdict.verdict} hyp #{args['id']} "
                    f"(model={vmodel_label}): {verdict.reasoning}")
                args["evidence_refs"] = list(args.get("evidence_refs") or []) + [{
                    "kind": "validation", "verdict": verdict.verdict,
                    "model": vmodel, "reasoning": verdict.reasoning}]
                _validation_suffix = f" (adversary: {verdict.verdict})"
    update_kwargs = {
        k: args[k]
        for k in ("status", "rationale", "confidence", "dispatched_to",
                  "evidence_refs", "tags")
        if k in args
    }
    kb.update_hypothesis(args["id"], **update_kwargs)
    # Emit a hypothesis WS frame so the renderer's Hypotheses pane
    # updates live. Best-effort; no-op when no current session.
    from ..gui_service.kb_emitter import emit_hypothesis
    updated = kb.get_hypothesis(args["id"])
    if updated is not None:
        emit_hypothesis("update", updated)
    return format_tool_result(
        f"Hypothesis #{args['id']} updated: {sorted(update_kwargs.keys())}{_validation_suffix}"
    )


TOOLS.append(kb_update_hypothesis)


@tool(
    "kb_list_hypotheses",
    "List hypotheses for the target, optionally filtered by status or parent_id. "
    "Set include_tree=True to render a hierarchical view (recommended for the "
    "manager profile's status checks).",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "status": {"type": "string"},
            "parent_id": {"type": "integer"},
            "include_tree": {"type": "boolean", "default": False},
        },
        "required": ["target"],
    },
)
async def kb_list_hypotheses(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    kb = for_target(args["target"])
    if args.get("include_tree"):
        return format_tool_result(_render_hypothesis_tree(kb))
    hypotheses = kb.list_hypotheses(
        status=args.get("status"),
        parent_id=args.get("parent_id"),
    )
    if not hypotheses:
        return format_tool_result("No hypotheses match.")
    lines = ["| id | status | conf | parent | statement |",
             "|---|---|---|---|---|"]
    for h in hypotheses:
        lines.append(
            f"| {h.id} | {h.status} | {h.confidence or '—'} | "
            f"{h.parent_id or '—'} | {h.statement} |"
        )
    return format_tool_result("\n".join(lines))


TOOLS.append(kb_list_hypotheses)


_STATUS_GLYPH = {
    "proposed": "💭",
    "testing": "🔄",
    "confirmed": "✅",
    "refuted": "❌",
    "abandoned": "🗑️",
    "blocked": "⛔",
}


def _render_hypothesis_tree(kb) -> str:
    """Markdown-bullet rendering of the hypothesis tree."""
    branches = kb.hypothesis_tree()
    if not branches:
        return "(no hypotheses)"
    lines = []

    def walk(branch, depth: int):
        h = branch["hypothesis"]
        glyph = _STATUS_GLYPH.get(h.status, "•")
        conf = f", {h.confidence}%" if h.confidence is not None else ""
        prefix = "  " * depth
        lines.append(
            f"{prefix}- {glyph} **{h.statement}** "
            f"({h.status}{conf}, id={h.id})"
        )
        for child in branch["children"]:
            walk(child, depth + 1)

    for b in branches:
        walk(b, 0)
    return "\n".join(lines)


def _render_report(kb, executive_summary: str = "") -> str:
    """Render a markdown report from KB contents in the project house style."""
    hosts = kb.get_hosts()
    services = kb.get_services()
    creds = kb.get_credentials()
    findings = kb.get_findings()
    artifacts = kb.get_artifacts()
    notes = kb.get_notes()

    lines = [
        f"# Penetration Test Report — {kb.target_id}",
        "",
        f"**Generated by:** kb_export_report",
        f"**Target:** {kb.target_id}",
        "",
    ]

    if executive_summary:
        lines.append("## Executive Summary")
        lines.append("")
        lines.append(executive_summary)
        lines.append("")

    lines += [
        "## Engagement Statistics",
        "",
        f"Recorded {len(hosts)} host(s), {len(services)} service(s), "
        f"{len(creds)} credential(s) ("
        f"{sum(1 for c in creds if c.status == 'valid')} valid), "
        f"{len(findings)} finding(s), {len(artifacts)} artifact(s).",
        "",
    ]

    lines.append("## Hosts")
    lines.append("")
    if hosts:
        lines.append("| IP | Hostname | OS | Domain | DC | SMB Signing |")
        lines.append("|---|---|---|---|---|---|")
        for h in hosts:
            lines.append(
                f"| {h.ip} | {h.hostname or ''} | {h.os or ''} | "
                f"{h.domain or ''} | {'yes' if h.is_dc else 'no'} | "
                f"{h.smb_signing or ''} |"
            )
    else:
        lines.append("_No hosts recorded._")
    lines.append("")

    lines.append("## Services")
    lines.append("")
    if services:
        lines.append("| Host | Port | Proto | Service | Version | Source |")
        lines.append("|---|---|---|---|---|---|")
        for s in services:
            lines.append(
                f"| {s.host_ip} | {s.port} | {s.proto} | {s.service or ''} "
                f"| {s.version or ''} | {s.scan_source or ''} |"
            )
    else:
        lines.append("_No services recorded._")
    lines.append("")

    lines.append("## Credentials")
    lines.append("")
    if creds:
        lines.append("| User | Domain | Status | Source | Material |")
        lines.append("|---|---|---|---|---|")
        for c in creds:
            mat = "password" if c.password else ("nt_hash" if c.nt_hash else
                  ("kerberos_ticket" if c.kerberos_ticket else "-"))
            lines.append(
                f"| {c.username} | {c.domain or ''} | {c.status} | "
                f"{c.source_tool or ''} | {mat} |"
            )
    else:
        lines.append("_No credentials recorded._")
    lines.append("")

    lines.append("## Findings")
    lines.append("")
    if findings:
        for f in findings:
            lines.append(f"### [{f.severity.upper()}] {f.title}")
            if f.cvss is not None:
                lines.append(f"_CVSS: {f.cvss}_")
            lines.append("")
            lines.append(f.description or "_(no description)_")
            if f.evidence_paths:
                lines.append("")
                lines.append("**Evidence:**")
                for p in f.evidence_paths:
                    lines.append(f"- `{p}`")
            lines.append("")
    else:
        lines.append("_No findings recorded._")
    lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    if artifacts:
        lines.append("| Kind | Path | Source | SHA-256 |")
        lines.append("|---|---|---|---|")
        for a in artifacts:
            lines.append(
                f"| {a.kind} | `{a.path}` | {a.source_tool or ''} | "
                f"{a.sha256 or ''} |"
            )
    else:
        lines.append("_No artifacts recorded._")
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    if notes:
        for n in notes:
            lines.append(f"- {n}")
    else:
        lines.append("_No notes recorded._")
    lines.append("")

    # Attack tree (only if hypotheses exist)
    branches = kb.hypothesis_tree()
    if branches:
        lines.append("## Attack tree")
        lines.append("")
        lines.append(_render_hypothesis_tree(kb))
        lines.append("")

    return "\n".join(lines)


@tool(
    "kb_export_report",
    "Render the KB for `target` as a markdown report. Default output path "
    "is `targets/<target>/report.md`. Returns the absolute output path. "
    "Requires executive_summary: a 1-3 sentence engagement summary.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
            "executive_summary": {
                "type": "string",
                "description": "1-3 sentence engagement summary prepended to the report.",
            },
            "output_path": {
                "type": "string",
                "description": "Optional override path. Defaults to "
                               "<target_root>/report.md.",
                "default": "",
            },
        },
        "required": ["target", "executive_summary"],
    },
)
async def kb_export_report(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args["target"]
    # Validate the report boundary via ReportModel (single source of truth).
    # Strip first so a whitespace-only summary is rejected by min_length. We do
    # NOT route KB findings through FindingModel here — the report is rendered
    # deterministically from KB rows so legacy findings (pre-v3) stay readable.
    report_outcome = validate_args(ReportModel, {
        "target": target,
        "executive_summary": (args.get("executive_summary") or "").strip(),
    })
    if not report_outcome.ok:
        return format_error(report_outcome.error_text)
    summary = report_outcome.value.executive_summary
    kb = for_target(target)
    body = _render_report(kb, executive_summary=summary)
    out_path = args.get("output_path") or str(kb.root / "report.md")
    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(body)
    return format_tool_result(
        f"Report written to {out_p} ({len(body)} bytes)\n\n--- preview ---\n"
        + body[:2000]
        + ("\n[truncated]" if len(body) > 2000 else "")
    )


TOOLS.append(kb_export_report)


@tool(
    "kb_refocus_target",
    "Re-point the engagement at a new IP for `target` (e.g. after an HTB reset). "
    "Promotes the new address, remaps host/service KB rows old->new, and refocuses "
    "the current session so subsequent tool calls use the new IP. Optionally updates "
    "/etc/hosts when `hostname` is given and update_etc_hosts is true.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target name/identifier."},
            "new_ip": {"type": "string", "description": "The target's new IP address."},
            "hostname": {"type": "string", "description": "Optional hostname (e.g. box.htb)."},
            "update_etc_hosts": {"type": "boolean", "default": False},
            "force_scope": {"type": "boolean", "default": False},
        },
        "required": ["target", "new_ip"],
    },
)
async def kb_refocus_target(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args.get("target")
    new_ip = (args.get("new_ip") or "").strip()
    if not target:
        return format_error("target is required.")
    if not new_ip:
        return format_error("new_ip is required (the target's new IP address).")
    from ..refocus import refocus_target, RefocusError
    try:
        result = refocus_target(
            target, new_ip,
            update_etc_hosts=bool(args.get("update_etc_hosts", False)),
            hostname=args.get("hostname"),
            force_scope=bool(args.get("force_scope", False)),
        )
    except RefocusError as e:
        return format_error(f"Refocus failed: {e}")
    # refocus the live session (if this tool runs inside one) using the REAL,
    # persisted address object — do NOT fabricate an Address.
    note = ""
    try:
        from ..sessions import current_session
        from ..targets import load_target
        sess = current_session.get()
        if sess is not None and getattr(sess, "active_address", None) is not None:
            addr = load_target(target).primary_address
            note = sess.refocus_address(addr)
    except Exception:
        note = ""
    lines = [
        f"Refocused {result.target}: {result.old_ip} -> {result.new_ip}",
        f"Remapped: {result.rows_remapped}",
    ]
    if result.scope_warning:
        lines.append(f"Scope warning: {result.scope_warning}")
    if result.hostname_updated:
        lines.append("/etc/hosts updated.")
    if note:
        lines.append(note)
    return format_tool_result("\n".join(lines))


TOOLS.append(kb_refocus_target)

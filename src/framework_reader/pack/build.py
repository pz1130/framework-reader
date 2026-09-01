"""内容库构建入口。spec §4.2⑤"""
import sqlite3
import sys
from pathlib import Path

from framework_reader.ingest.cprt import parse_cprt_mappings
from framework_reader.ingest.derive import derive_two_hop
from framework_reader.ingest.ids import rewrite_mapping_80053_ids
from framework_reader.ingest.iso import parse_800_53_to_iso, parse_iso_skeleton
from framework_reader.ingest.oscal import (
    parse_oscal_catalog,
    parse_supersessions,
    unified_controls_from_csf,
)
from framework_reader.pack.db import (
    create_schema,
    insert_controls,
    insert_frameworks,
    insert_mappings,
    insert_supersessions,
    insert_unified,
)
from framework_reader.pack.id_baseline import write_baseline
from framework_reader.pack.validate import (
    BuildAssertionError,
    assert_build_invariants,
    validate_graph,
)
from framework_reader.schema.sources import SourceRegistry

VENDOR = Path("vendor/nist")
REGISTRY_PATH = Path("content/allowed_sources.yaml")
ISO_SKELETON = Path("content/iso27002_2022_skeleton.csv")
BASELINE_PATH = Path("content/published_control_ids.json")

CSF_PREFIX = "NIST-CSF-2.0:"
C53_PREFIX = "NIST-800-53-R5:"
ISO_PREFIX = "ISO-27002-2022:"


# 800-53 有近千条 enhancement 从未出现在任何交叉表里，逐条打印会把真正的问题淹掉。
_WARN_SAMPLE = 5


def _report(issues: list) -> None:
    """按 kind 汇总，每类只举几例——警告要能读，否则等于没有。"""
    from collections import defaultdict

    grouped: dict[str, list[str]] = defaultdict(list)
    for issue in issues:
        grouped[issue.kind].append(issue.detail)
    for kind in sorted(grouped):
        details = grouped[kind]
        head = ", ".join(details[:_WARN_SAMPLE])
        more = f", plus {len(details) - _WARN_SAMPLE} more" if len(details) > _WARN_SAMPLE else ""
        print(f"[warn] {kind} × {len(details)}: {head}{more}", file=sys.stderr)


def build_content_db(out: Path) -> Path:
    registry = SourceRegistry.load(REGISTRY_PATH)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    conn = sqlite3.connect(out)
    create_schema(conn)

    catalog_53 = VENDOR / "sp800-53r5-catalog.json"
    catalog_csf = VENDOR / "csf-2.0.json"
    fw_53, ctl_53 = parse_oscal_catalog(catalog_53, framework_id="NIST-800-53-R5")
    fw_csf, ctl_csf = parse_oscal_catalog(catalog_csf, framework_id="NIST-CSF-2.0")
    fw_iso, ctl_iso = parse_iso_skeleton(ISO_SKELETON)
    insert_frameworks(conn, [fw_53, fw_csf, fw_iso])
    insert_controls(conn, ctl_53 + ctl_csf + ctl_iso)
    insert_unified(conn, unified_controls_from_csf(ctl_csf))
    insert_supersessions(
        conn,
        parse_supersessions(catalog_53, framework_id="NIST-800-53-R5")
        + parse_supersessions(catalog_csf, framework_id="NIST-CSF-2.0"),
    )

    l1_edges = rewrite_mapping_80053_ids(
        parse_cprt_mappings(
            VENDOR / "csf-2.0-to-sp800-53r5-mappings.xlsx",
            sheet="Relationships",
            header_row=1,
        )
        + parse_800_53_to_iso(VENDOR / "sp800-53r5-to-iso-27001-mapping.xlsx")
    )

    derived = derive_two_hop(
        l1_edges, via_prefix=C53_PREFIX, from_prefix=CSF_PREFIX, to_prefix=ISO_PREFIX
    )
    insert_mappings(conn, l1_edges + derived, registry)

    issues = validate_graph(conn)
    dangling = [
        i for i in issues
        if i.kind in ("dangling_mapping_endpoint", "dangling_supersession_endpoint")
    ]
    _report(issues)
    if dangling:
        raise BuildAssertionError(
            f"{len(dangling)} dangling endpoints after 800-53 ID normalization; "
            f"first: {dangling[0].kind} {dangling[0].detail}"
        )

    from framework_reader.interpret.model import InterpretationState
    from framework_reader.interpret.store import InterpretationStore
    from framework_reader.pack.db import insert_interpretations
    from framework_reader.pack.glossary import Glossary
    from framework_reader.pack.validate import (
        assert_glossary_clean,
        assert_only_confirmed,
        assert_signature_matches_content,
    )

    interp_store = InterpretationStore()
    all_items = list(interp_store.iter_all())
    confirmed = [i for i in all_items if i.state is InterpretationState.CONFIRMED]
    # 签过字的那批，闸门一个都不松：真签了、签完没被改过、术语表干净。
    assert_only_confirmed(confirmed)
    assert_signature_matches_content(confirmed)
    assert_glossary_clean(confirmed, Glossary.load(Path("content/glossary.zh.yaml")))
    # 草稿也进包（主 spec §7.3.1 自用降级），但 state 跟着进，读的人看得见成色。
    # 术语表对草稿只报数不拦：check_text 是子串匹配，分不清「地域差异」和
    # 「差距分析」，拿它拦住整个构建会让工具对作者自己也是空的。
    drafts = [i for i in all_items if i.state is not InterpretationState.CONFIRMED]
    _report_draft_glossary(drafts, Glossary.load(Path("content/glossary.zh.yaml")))
    insert_interpretations(conn, all_items)

    assert_build_invariants(conn, registry, BASELINE_PATH)
    write_baseline(conn, BASELINE_PATH)
    conn.close()
    return out


def _report_draft_glossary(drafts, glossary) -> None:
    """草稿里的术语表命中只报数，不抛。主 spec §7.3.1"""
    hits = 0
    controls = set()
    for interp in drafts:
        for name, field in sorted(interp.fields.items()):
            value = field.value
            if value is None:
                continue
            if isinstance(value, dict):
                text = " ".join(str(v) for v in value.values())
            elif isinstance(value, list):
                text = " ".join(str(v) for v in value)
            else:
                text = str(value)
            if glossary.check_text(text):
                hits += 1
                controls.add(interp.control_id)
    if hits:
        print(
            f"Draft glossary hit {hits} times across {len(controls)} controls"
            f" (not blocked; must be handled before signing)"
        )


if __name__ == "__main__":
    path = build_content_db(Path("build/content.sqlite"))
    print(f"built {path}")

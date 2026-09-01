"""两跳传递推导。spec §4.3

CSF 2.0 →(L1) 800-53 Rev5 →(L1) ISO 27001 ⇒ CSF ↔ ISO 标 L2_DERIVED。
产出仅作为人工确认的候选，不可直接导出（spec §3.3）。
"""
from collections import defaultdict

from framework_reader.schema.mapping import (
    Mapping,
    Provenance,
    ProvenanceLevel,
    Relation,
)

DERIVED_SOURCE = "derived:two-hop"


def _undirected_l1(edges: list[Mapping]) -> list[tuple[str, str]]:
    """L1 边视为无向——官方对照表不区分方向。"""
    pairs: list[tuple[str, str]] = []
    for e in edges:
        if e.provenance.level is not ProvenanceLevel.L1_OFFICIAL:
            continue
        pairs.append((e.from_id, e.to_id))
        pairs.append((e.to_id, e.from_id))
    return pairs


def derive_two_hop(
    edges: list[Mapping], via_prefix: str, from_prefix: str, to_prefix: str
) -> list[Mapping]:
    pairs = _undirected_l1(edges)

    # from_prefix 节点 → 中间节点
    left: dict[str, set[str]] = defaultdict(set)
    # 中间节点 → to_prefix 节点
    right: dict[str, set[str]] = defaultdict(set)
    for a, b in pairs:
        if a.startswith(from_prefix) and b.startswith(via_prefix):
            left[a].add(b)
        if a.startswith(via_prefix) and b.startswith(to_prefix):
            right[a].add(b)

    paths: dict[tuple[str, str], set[str]] = defaultdict(set)
    for src, mids in left.items():
        for mid in mids:
            for dst in right.get(mid, ()):
                if src == dst:
                    continue
                paths[(src, dst)].add(mid)

    out: list[Mapping] = []
    for (src, dst), mids in sorted(paths.items()):
        out.append(
            Mapping(
                from_id=src,
                to_id=dst,
                relation=Relation.RELATED,
                provenance=Provenance(
                    level=ProvenanceLevel.L2_DERIVED,
                    source=DERIVED_SOURCE,
                    source_version="1",
                    derived_via=sorted(mids),
                ),
                note=f"Derived through {len(mids)} intermediate controls; requires human confirmation",
            )
        )
    return out

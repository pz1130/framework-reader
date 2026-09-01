"""SQLite 构建。spec §6.2：SQLite 是构建产物，YAML 才是真相源。"""
import sqlite3

from framework_reader.interpret.model import Interpretation
from framework_reader.schema.entities import (
    Framework,
    FrameworkControl,
    Supersession,
    UnifiedControl,
)
from framework_reader.schema.mapping import Mapping
from framework_reader.schema.sources import SourceRegistry

DDL = """
CREATE TABLE framework (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    tier TEXT NOT NULL,
    source_url TEXT NOT NULL,
    license_note TEXT NOT NULL
);

CREATE TABLE framework_control (
    id TEXT PRIMARY KEY,
    framework_id TEXT NOT NULL REFERENCES framework(id),
    parent_id TEXT,
    label TEXT NOT NULL,
    label_is_original INTEGER NOT NULL,
    status TEXT NOT NULL
);

-- 废止控制的去向。多对多，故独立成表。spec §8②
CREATE TABLE control_supersession (
    old_id TEXT NOT NULL,
    new_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    PRIMARY KEY (old_id, new_id)
);

CREATE TABLE unified_control (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    locale TEXT NOT NULL
);

CREATE TABLE mapping (
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    source_version TEXT NOT NULL,
    confirmed_by TEXT,
    confirmed_at TEXT,
    derived_via TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (from_id, to_id, source)
);

-- 原文表：内容包中永远为空，仅供用户本地注入。spec §3.2②
CREATE TABLE original_text (
    control_id TEXT NOT NULL,
    locale TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (control_id, locale)
);

-- 解读。草稿也进包，但 state 必须跟着走，读的人要看得见成色。
-- 主 spec §7.3.1（2026-08-22 自用降级）；原「只有签过字的进包」见 W2 spec §4.3
CREATE TABLE interpretation (
    control_id TEXT NOT NULL,
    locale TEXT NOT NULL,
    field TEXT NOT NULL,
    value_json TEXT NOT NULL,
    basis TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (control_id, locale, field)
);

CREATE INDEX idx_mapping_from ON mapping(from_id);
CREATE INDEX idx_mapping_to ON mapping(to_id);
CREATE INDEX idx_control_framework ON framework_control(framework_id);
CREATE INDEX idx_supersession_new ON control_supersession(new_id);
"""


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def insert_frameworks(conn: sqlite3.Connection, items: list[Framework]) -> None:
    conn.executemany(
        "INSERT INTO framework (id, name, version, tier, source_url, license_note) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(f.id, f.name, f.version, f.tier.value, f.source_url, f.license_note) for f in items],
    )
    conn.commit()


def insert_controls(conn: sqlite3.Connection, items: list[FrameworkControl]) -> None:
    conn.executemany(
        "INSERT INTO framework_control "
        "(id, framework_id, parent_id, label, label_is_original, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (c.id, c.framework_id, c.parent_id, c.label,
             int(c.label_is_original), c.status.value)
            for c in items
        ],
    )
    conn.commit()


def insert_supersessions(conn: sqlite3.Connection, items: list[Supersession]) -> None:
    conn.executemany(
        "INSERT INTO control_supersession (old_id, new_id, relation) VALUES (?, ?, ?)",
        [(s.old_id, s.new_id, s.relation.value) for s in items],
    )
    conn.commit()


def insert_unified(conn: sqlite3.Connection, items: list[UnifiedControl]) -> None:
    conn.executemany(
        "INSERT INTO unified_control (id, label, locale) VALUES (?, ?, ?)",
        [(u.id, u.label, u.locale) for u in items],
    )
    conn.commit()


def insert_mappings(
    conn: sqlite3.Connection, items: list[Mapping], registry: SourceRegistry
) -> None:
    # 先全量断言，再写库——任何一条不合规则整批不写。spec §10.A
    for m in items:
        registry.assert_allowed(m.provenance.source)
    conn.executemany(
        "INSERT INTO mapping "
        "(from_id, to_id, relation, level, source, source_version, "
        " confirmed_by, confirmed_at, derived_via, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                m.from_id, m.to_id, m.relation.value, m.provenance.level.value,
                m.provenance.source, m.provenance.source_version,
                m.provenance.confirmed_by,
                m.provenance.confirmed_at.isoformat() if m.provenance.confirmed_at else None,
                ",".join(m.provenance.derived_via),
                m.note,
            )
            for m in items
        ],
    )
    conn.commit()


def insert_interpretations(
    conn: sqlite3.Connection, items: list[Interpretation]
) -> None:
    import json

    rows = [
        (
            i.control_id, i.locale, name,
            json.dumps(f.value, ensure_ascii=False), f.basis.value, i.state.value,
        )
        for i in items
        for name, f in sorted(i.fields.items())
    ]
    conn.executemany(
        "INSERT INTO interpretation "
        "(control_id, locale, field, value_json, basis, state) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()

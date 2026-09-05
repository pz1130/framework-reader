"""R7: derived-edge accuracy sampling. spec §11 R7"""
import csv
import random
import sqlite3
from pathlib import Path

from pydantic import BaseModel


class DerivedSample(BaseModel):
    from_id: str
    from_label: str
    to_id: str
    to_label: str
    via: str
    verdict: str | None = None
    comment: str = ""


def sample_derived_edges(db_path: Path, n: int, seed: int) -> list[DerivedSample]:
    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT m.from_id, f.label AS from_label, m.to_id, t.label AS to_label,
               m.derived_via
        FROM mapping m
        JOIN framework_control f ON f.id = m.from_id
        JOIN framework_control t ON t.id = m.to_id
        WHERE m.level = 'L2_DERIVED'
        ORDER BY m.from_id, m.to_id
        """
    ).fetchall()
    conn.close()

    rng = random.Random(seed)
    picked = rows if n >= len(rows) else rng.sample(rows, n)
    picked = sorted(picked, key=lambda r: (r["from_id"], r["to_id"]))
    return [
        DerivedSample(
            from_id=r["from_id"], from_label=r["from_label"],
            to_id=r["to_id"], to_label=r["to_label"], via=r["derived_via"],
        )
        for r in picked
    ]


def write_review_sheet(samples: list[DerivedSample], out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["from_id", "from_label", "to_id", "to_label", "via", "verdict", "comment"]
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for s in samples:
            row = s.model_dump()
            row["verdict"] = ""      # filled by a human: correct / wrong / partial
            row["comment"] = ""
            w.writerow(row)
    return out

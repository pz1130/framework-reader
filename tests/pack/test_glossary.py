from pathlib import Path

from framework_reader.pack.glossary import Glossary

GLOSSARY = Path("content/glossary.zh.yaml")


def test_preferred_term_passes():
    g = Glossary.load(GLOSSARY)
    assert g.check_text("本控制措施要求对网络进行监控。") == []


def test_banned_synonym_is_flagged():
    g = Glossary.load(GLOSSARY)
    hits = g.check_text("本管控项要求对网络进行监控。")
    assert "管控项" in hits


def test_multiple_banned_terms_all_reported():
    g = Glossary.load(GLOSSARY)
    hits = g.check_text("该管控项与控制点均需举证。")
    assert set(hits) >= {"管控项", "控制点"}


def test_every_entry_has_preferred_and_rationale():
    g = Glossary.load(GLOSSARY)
    assert g.entries
    for e in g.entries:
        assert e.preferred
        assert e.rationale, f"{e.preferred} 缺 rationale——术语选择的理由必须写下来"


def test_preferred_terms_are_not_themselves_banned():
    """防止术语表自相矛盾。"""
    g = Glossary.load(GLOSSARY)
    preferred = {e.preferred for e in g.entries}
    banned = {b for e in g.entries for b in e.banned}
    assert preferred & banned == set()

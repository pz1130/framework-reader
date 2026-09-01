"""Wheel must ship non-.py files that Path(__file__).parent opens at runtime.

Docker does `pip install .` (a wheel). `pip install -e .` in CI does not
catch a missing package-data declaration — the files are simply on disk.
"""
from pathlib import Path

import tomllib

from framework_reader.assess.store import SCHEMA as USER_SCHEMA
from framework_reader.identity.store import SCHEMA
from framework_reader.prompts import PROMPT_DIR, load_prompt


def test_identity_schema_sql_is_next_to_the_module():
    assert SCHEMA.is_file(), SCHEMA


def test_user_schema_sql_is_inside_the_pack_package():
    assert USER_SCHEMA.is_file(), USER_SCHEMA


def test_prompt_markdown_is_next_to_the_module():
    assert load_prompt("drafter").strip()
    assert (PROMPT_DIR / "drafter.md").is_file()


def test_pyproject_declares_sql_and_prompts_as_package_data():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    patterns = data["tool"]["setuptools"]["package-data"]["framework_reader"]
    joined = " ".join(patterns)
    assert "sql" in joined
    assert "prompts" in joined

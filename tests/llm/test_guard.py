import sqlite3

import pytest

from framework_reader.llm.client import FakeClient, Message
from framework_reader.llm.guard import (
    GuardedClient,
    OutboundTextError,
    PayloadGuard,
    forbidden_texts_from_db,
)

ISO_BODY = "组织应定义并实施过程以管理与供方相关的信息安全风险并按约定频率复核"


def test_guard_blocks_forbidden_text_in_user_message():
    guard = PayloadGuard([ISO_BODY])
    with pytest.raises(OutboundTextError):
        guard.check(f"请解读这段：{ISO_BODY}")


def test_guard_blocks_forbidden_text_in_system_prompt():
    guard = PayloadGuard([ISO_BODY])
    with pytest.raises(OutboundTextError):
        guard.check(ISO_BODY, "无害的用户消息")


def test_guard_allows_short_incidental_overlap():
    """「组织应定义」这类短语到处都是，按整段比对才有意义。"""
    guard = PayloadGuard([ISO_BODY], min_chunk=24)
    guard.check("组织应定义安全职责")


def test_guard_ignores_forbidden_entries_shorter_than_min_chunk():
    guard = PayloadGuard(["短句"], min_chunk=24)
    guard.check("这里出现了短句也不该报")


def test_guarded_client_raises_before_calling_inner():
    inner = FakeClient(["不该被用到"])
    client = GuardedClient(inner, PayloadGuard([ISO_BODY]))
    with pytest.raises(OutboundTextError):
        client.complete("sys", [Message(role="user", content=ISO_BODY)], model="m")
    assert inner.calls == [], "红线断言必须在调用发生之前拦住"


def test_guarded_client_passes_clean_payload_through():
    inner = FakeClient(["ok"])
    client = GuardedClient(inner, PayloadGuard([ISO_BODY]))
    out = client.complete("sys", [Message(role="user", content="CSF 是公共领域")], model="m")
    assert out == "ok"


def test_forbidden_texts_come_from_the_original_text_table():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE original_text (control_id TEXT, locale TEXT, body TEXT)"
    )
    conn.execute(
        "INSERT INTO original_text VALUES (?, ?, ?)",
        ("ISO-27002-2022:A.5.22", "zh-CN", ISO_BODY),
    )
    assert forbidden_texts_from_db(conn) == [ISO_BODY]


def test_empty_original_text_table_yields_no_forbidden_texts():
    """构建产物里该表恒为空——用户本地注入后才有内容。主 spec §3.2②"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE original_text (control_id TEXT, locale TEXT, body TEXT)")
    assert forbidden_texts_from_db(conn) == []

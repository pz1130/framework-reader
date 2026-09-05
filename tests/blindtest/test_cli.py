import json

from typer.testing import CliRunner

from framework_reader.cli.main import app


def test_blindtest_help_lists_every_action():
    result = CliRunner().invoke(app, ["blindtest", "--help"])
    assert result.exit_code == 0
    for word in ("prepare", "repacket", "tally", "report"):
        assert word in result.stdout


def test_unknown_action_exits_nonzero():
    result = CliRunner().invoke(app, ["blindtest", "nope"])
    assert result.exit_code != 0


# ---------- repacket：只重渲，不重抽、不调模型 ----------

def _room(tmp_path, seed=7):
    from framework_reader.blindtest.packet import PacketItem, build_packet

    items = [
        PacketItem(
            control_id=f"NIST-CSF-2.0:GV.OC-0{i}",
            product=f"产品解读 {i}", bare=f"裸问回答 {i}", original=f"Outcome {i}",
        )
        for i in (1, 2, 3)
    ]
    text, key = build_packet(items, seed)
    room = tmp_path / "build" / "blindtest" / str(seed)
    room.mkdir(parents=True)
    (room / "variants.json").write_text(
        json.dumps([i.model_dump() for i in items], ensure_ascii=False), encoding="utf-8"
    )
    (room / "answer_key.json").write_text(key.model_dump_json(indent=2), encoding="utf-8")
    (room / "packet.md").write_text("旧的问卷", encoding="utf-8")
    return room


def test_repacket_rewrites_the_packet_without_touching_the_answer_key(tmp_path, monkeypatch):
    """改答题说明不该重抽一批题，也不该再花一次钱调模型。"""
    room = _room(tmp_path)
    before = (room / "answer_key.json").read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["blindtest", "repacket", "--seed", "7"])
    assert result.exit_code == 0, result.output
    assert "which one helps you most" in (room / "packet.md").read_text(encoding="utf-8")
    assert (room / "answer_key.json").read_text(encoding="utf-8") == before


def test_repacket_without_variants_exits_nonzero(tmp_path, monkeypatch):
    room = _room(tmp_path)
    (room / "variants.json").unlink()
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["blindtest", "repacket", "--seed", "7"])
    assert result.exit_code != 0
    assert (room / "packet.md").read_text(encoding="utf-8") == "旧的问卷"


def test_repacket_refuses_when_it_would_change_the_answer_key(tmp_path, monkeypatch):
    """评委手上的甲乙丙一旦变了，已收回的判定就全废了。宁可不产出。"""
    room = _room(tmp_path)
    key = json.loads((room / "answer_key.json").read_text(encoding="utf-8"))
    first = key["order"][0]
    # 把这条的甲乙丙轮换一位，保证与重渲出来的对不上
    letters = ["A", "B", "C"]
    was = key["mapping"][first]
    key["mapping"][first] = {
        letters[i]: was[letters[i - 1]] for i in range(len(letters))
    }
    (room / "answer_key.json").write_text(
        json.dumps(key, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["blindtest", "repacket", "--seed", "7"])
    assert result.exit_code != 0
    assert (room / "packet.md").read_text(encoding="utf-8") == "旧的问卷"


def test_repacket_carries_the_frame_fingerprint_through(tmp_path, monkeypatch):
    """重渲不能把出题时记下的抽样框指纹弄丢——丢了就再也验不了复现。"""
    room = _room(tmp_path)
    key = json.loads((room / "answer_key.json").read_text(encoding="utf-8"))
    assert key["frame_fingerprint"] == ""
    key["frame_fingerprint"] = "abc123"
    (room / "answer_key.json").write_text(
        json.dumps(key, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["blindtest", "repacket", "--seed", "7"])
    assert result.exit_code == 0, result.output
    after = json.loads((room / "answer_key.json").read_text(encoding="utf-8"))
    assert after["frame_fingerprint"] == "abc123"

# -*- coding: utf-8 -*-
"""Agent 侧契约测试：多轮指代消解行为 + 工具参数 schema/枚举。

由 _tools/ 下的自检脚本迁入（原先只在本地手跑，现在进 pytest 受 CI/gate 约束）。
"""
import pytest


# ====== 多轮指代消解（app/agent/coref.py）======
@pytest.fixture(scope="module")
def coref():
    from app.agent import coref as module
    return module


HIST = [{"user": "库塔尔是谁？", "assistant": "库塔尔即愚人众执行官「少女」哥伦比娅。"}]


class FakeLLM:
    def __init__(self, out=None, error=None):
        self.out = out
        self.error = error
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.error:
            raise self.error
        return type("Resp", (), {"content": self.out})()


def _state(question, history=None):
    return {"user_query": question, "conversation_history": history or [], "run_id": "pytest"}


def test_coref_skipped_without_history(coref, monkeypatch):
    fake = FakeLLM(out="不该被使用")
    monkeypatch.setattr(coref, "coref_llm", fake)
    text, changed = coref.resolve_coreference(_state("她是谁？"))
    assert (text, changed, fake.calls) == ("她是谁？", False, 0)


def test_coref_skipped_without_pronoun(coref, monkeypatch):
    fake = FakeLLM(out="不该被使用")
    monkeypatch.setattr(coref, "coref_llm", fake)
    text, changed = coref.resolve_coreference(_state("胡桃的传说任务叫什么", HIST))
    assert changed is False and fake.calls == 0


def test_coref_resolves_pronoun(coref, monkeypatch):
    fake = FakeLLM(out="库塔尔成为月神之前的身份是什么？")
    monkeypatch.setattr(coref, "coref_llm", fake)
    text, changed = coref.resolve_coreference(_state("她成为月神之前的身份是什么？", HIST))
    assert changed is True and text.startswith("库塔尔") and fake.calls == 1


@pytest.mark.parametrize("bad", [None, "   ", "她" * 400])
def test_coref_falls_back_on_bad_output(coref, monkeypatch, bad):
    monkeypatch.setattr(coref, "coref_llm", FakeLLM(out=bad))
    text, changed = coref.resolve_coreference(_state("她是谁？", HIST))
    assert (text, changed) == ("她是谁？", False)


def test_coref_falls_back_on_error(coref, monkeypatch):
    monkeypatch.setattr(coref, "coref_llm", FakeLLM(error=RuntimeError("boom")))
    text, changed = coref.resolve_coreference(_state("她是谁？", HIST))
    assert (text, changed) == ("她是谁？", False)


def test_coref_takes_first_line(coref, monkeypatch):
    monkeypatch.setattr(coref, "coref_llm", FakeLLM(out="库塔尔是谁？\n（解释：她指上一轮）"))
    text, changed = coref.resolve_coreference(_state("她是谁？", HIST))
    assert text == "库塔尔是谁？" and changed is True


# ====== 工具参数 schema 与枚举（app/tools/vocab.py）======
ENUM_CASES = [
    ("list_characters_by_element", "element", "火", {}),
    ("list_characters_by_region", "region", "璃月", {}),
    ("list_characters_by_weapon", "weapon_type", "长柄武器", {}),
    ("list_characters_by_rarity", "rarity", "5", {}),
    ("list_collectibles_by_region", "region", "蒙德", {}),
    ("query_character", "section", "语音", {"name": "胡桃"}),
]


@pytest.fixture(scope="module")
def tools():
    from app.tools import tools_by_name
    return tools_by_name


def _schema(tool):
    sch = tool.args_schema
    return sch.model_json_schema() if hasattr(sch, "model_json_schema") else sch.schema()


def test_every_tool_has_schema_and_description(tools):
    assert len(tools) >= 34
    for name, tool in tools.items():
        assert tool.args_schema is not None, f"{name} 缺少结构化参数定义"
        assert (tool.description or "").strip(), f"{name} 缺少描述"


def test_enum_params_match_vocab(tools):
    from app.tools import vocab
    mapping = {
        ("list_characters_by_element", "element"): vocab.ELEMENTS,
        ("list_characters_by_region", "region"): vocab.CHARACTER_REGIONS,
        ("list_characters_by_weapon", "weapon_type"): vocab.WEAPON_TYPES,
        ("list_characters_by_rarity", "rarity"): vocab.RARITIES,
        ("list_collectibles_by_region", "region"): vocab.COLLECTIBLE_REGIONS,
        ("query_character", "section"): ("", "语音"),
    }
    for name, param, _, _ in ENUM_CASES:
        enum = _schema(tools[name])["properties"][param].get("enum")
        assert enum, f"{name}.{param} 的 schema 缺少 enum"
        assert tuple(enum) == mapping[(name, param)], f"{name}.{param} 枚举与 vocab 不一致"


def test_invalid_enum_rejected_before_call(tools):
    for name, param, _, extra in ENUM_CASES:
        args = dict(extra)
        args[param] = "不存在的取值"
        with pytest.raises(Exception):
            tools[name].invoke(args)


@pytest.mark.parametrize(
    "name,args",
    [
        ("list_characters_by_element", {"element": "火"}),
        ("list_characters_by_rarity", {"rarity": "5"}),
        ("list_collectibles_by_region", {"region": "蒙德"}),
    ],
)
def test_valid_enum_call_returns_content(tools, name, args):
    out = tools[name].invoke(args)
    assert isinstance(out, str) and out.strip() and "出错" not in out[:20]

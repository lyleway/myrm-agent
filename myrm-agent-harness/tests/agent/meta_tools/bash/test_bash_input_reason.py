"""BashInput reason validation contract tests."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from myrm_agent_harness.agent.meta_tools.bash.bash_code_execute_tool import create_bash_code_execute_tool


@pytest.fixture(scope="module")
def tool_schema() -> type[BaseModel]:
    """Resolve the active tool args schema so assertions stay coupled to the registered tool."""
    schema = create_bash_code_execute_tool().args_schema
    assert isinstance(schema, type)
    assert issubclass(schema, BaseModel)
    return schema


def test_reason_schema_does_not_advertise_length_floor(tool_schema: type[BaseModel]) -> None:
    desc = tool_schema.model_fields["reason"].description or ""
    assert desc
    assert "≥10" not in desc
    assert "10 char" not in desc.lower()


@pytest.mark.parametrize(
    ("raw_reason", "expected"),
    [
        ("ls", "ls"),
        ("run", "run"),
        ("查看pod", "查看pod"),
        ("查询余票", "查询余票"),
        ("列出函数", "列出函数"),
        ("检查日志", "检查日志"),
        ("查一下ls", "查一下ls"),
        ("run tests", "run tests"),
        ("проверить", "проверить"),
        ("  List workspace files for review  ", "List workspace files for review"),
        ("0", "0"),
    ],
)
def test_reason_accepts_concise_multilingual_intents(
    tool_schema: type[BaseModel],
    raw_reason: str,
    expected: str,
) -> None:
    model = tool_schema(reason=raw_reason, command="ls -la")
    assert model.model_dump()["reason"] == expected


@pytest.mark.parametrize(
    "bad_reason",
    [
        "",
        " ",
        "\n\t ",
        None,
        ".",
        "...",
        "..........",
        "。",
        "。。。。",
        "---",
        "???",
        "___",
        "👍🔥🚀✅",
    ],
)
def test_reason_rejects_empty_and_symbol_only_placeholders(
    tool_schema: type[BaseModel],
    bad_reason: object,
) -> None:
    with pytest.raises(ValidationError, match="reason must describe why this command runs"):
        tool_schema(reason=bad_reason, command="echo hi")


def test_reason_enforces_max_length(tool_schema: type[BaseModel]) -> None:
    model = tool_schema(reason=f"  {'a' * 500}  ", command="echo hi")
    assert len(model.model_dump()["reason"]) == 500

    with pytest.raises(ValidationError):
        tool_schema(reason="a" * 501, command="echo hi")

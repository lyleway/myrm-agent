"""Claude Code's command line: how it is told to resume a conversation, limit turns, stream text and take images."""

from __future__ import annotations

import json

import pytest

from myrm_agent_harness.toolkits.acp.runtime.cli import claude
from myrm_agent_harness.toolkits.acp.runtime.image_blocks import ImageBlock

_ID = "0198a7c3-5d2e-7c41-9b8e-1f2a3b4c5d6e"
_BLOCK = ImageBlock("image/png", "AAEC")
_BLOCK_JPEG = ImageBlock("image/jpeg", "/9j/")


class TestSessionId:
    def test_a_canonical_uuid_is_a_session_id(self) -> None:
        assert claude.is_session_id(_ID)
        assert claude.is_session_id(_ID.upper())

    @pytest.mark.parametrize(
        "value",
        ["", "abc", "deadbeef", _ID + "0", " " + _ID, _ID.replace("-", ""), "--help", "-x " + _ID, _ID + "\n--flag"],
    )
    def test_anything_else_is_never_replayed_to_a_cli(self, value: str) -> None:
        assert not claude.is_session_id(value)


class TestWhichCommandsAreClaude:
    @pytest.mark.parametrize("command", ["claude", "/usr/local/bin/claude", "claude-code"])
    def test_claude_commands_are(self, command: str) -> None:
        assert claude.is_claude(command)

    @pytest.mark.parametrize("command", ["codex", "/opt/bin/codex", "gemini", "qwen", "/opt/custom/agent", "", None])
    def test_other_commands_are_not(self, command: str | None) -> None:
        assert not claude.is_claude(command)


class TestResume:
    def test_resume_is_a_trailing_flag(self) -> None:
        assert claude.with_resume(["claude", "-p"], _ID) == ["claude", "-p", "--resume", _ID]

    def test_a_malformed_id_cannot_carry_a_resume(self) -> None:
        assert claude.with_resume(["claude", "-p"], "--dangerous") is None

    def test_configured_resume_args_are_left_alone(self) -> None:
        assert claude.is_resuming(["claude", "--resume", "x"])
        assert not claude.is_resuming(["claude", "-p"])

    def test_a_resumed_turn_keeps_the_input_format_before_the_resume_flag(self) -> None:
        args, _ = claude.with_image_blocks(["claude", "-p"], "look", [_BLOCK])

        assert claude.with_resume(args, _ID) == ["claude", "-p", "--input-format", "stream-json", "--resume", _ID]


class TestTurnLimitAndStreaming:
    def test_the_turn_limit_is_added_once_and_only_when_positive(self) -> None:
        assert claude.with_max_turns(["claude"], 7) == ["claude", "--max-turns", "7"]
        assert claude.with_max_turns(["claude"], 0) == ["claude"]
        assert claude.with_max_turns(["claude", "--max-turns", "3"], 7) == ["claude", "--max-turns", "3"]

    def test_partial_messages_are_asked_for_only_in_stream_json(self) -> None:
        streaming = ["claude", "--output-format", "stream-json"]
        assert claude.with_partial_messages(streaming) == [*streaming, "--include-partial-messages"]
        assert claude.with_partial_messages([*streaming, "--include-partial-messages"]) == [
            *streaming,
            "--include-partial-messages",
        ]
        assert claude.with_partial_messages(["claude", "--output-format", "json"]) == [
            "claude",
            "--output-format",
            "json",
        ]


class TestEnvDefaults:
    def test_claude_runs_headless_without_background_tasks(self) -> None:
        assert dict(claude.ENV_DEFAULTS) == {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}


class TestImages:
    def test_claude_sends_the_images_and_the_prompt_as_one_stream_json_user_message(self) -> None:
        args = ["claude", "-p", "--output-format", "stream-json"]

        new_args, stdin = claude.with_image_blocks(args, "what is wrong?", [_BLOCK, _BLOCK_JPEG])

        assert new_args == [*args, "--input-format", "stream-json"]
        assert "\n" not in stdin
        assert json.loads(stdin) == {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAEC"}},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "/9j/"}},
                    {"type": "text", "text": "what is wrong?"},
                ],
            },
        }

    def test_a_prompt_with_newlines_and_non_ascii_text_stays_one_line_and_survives_intact(self) -> None:
        prompt = '第一行\n"quoted" \\ [Image: /Users/张 三/a.png]\r\n'

        _, stdin = claude.with_image_blocks(["claude", "-p"], prompt, [_BLOCK])

        assert "\n" not in stdin
        assert json.loads(stdin)["message"]["content"][-1] == {"type": "text", "text": prompt}

    @pytest.mark.parametrize("prompt", ["", "  \n"])
    def test_a_blank_prompt_sends_the_images_alone(self, prompt: str) -> None:
        _, stdin = claude.with_image_blocks(["claude", "-p"], prompt, [_BLOCK])

        assert [block["type"] for block in json.loads(stdin)["message"]["content"]] == ["image"]

    def test_a_turn_without_images_keeps_a_plain_text_prompt(self) -> None:
        args = ["claude", "-p"]

        assert claude.with_image_blocks(args, "look  ", []) == (args, "look  ")

    def test_an_input_format_the_args_already_hold_is_not_repeated(self) -> None:
        args = ["claude", "-p", "--input-format", "stream-json"]

        new_args, _ = claude.with_image_blocks(args, "look", [_BLOCK])

        assert new_args == args


class TestPermissionBridge:
    def test_claude_is_pointed_at_the_tool_to_ask_and_the_config_that_connects_it(self) -> None:
        args = claude.with_permission_bridge(["claude", "-p"], "mcp__bridge__approve", "/tmp/bridge.json")

        assert args == [
            "claude",
            "-p",
            "--permission-prompt-tool",
            "mcp__bridge__approve",
            "--mcp-config",
            "/tmp/bridge.json",
        ]

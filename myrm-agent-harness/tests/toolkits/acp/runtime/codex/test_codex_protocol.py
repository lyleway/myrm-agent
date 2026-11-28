"""What the host says to Codex's app server: the policy a permission mode stands for, the shape of each request, the launch."""

from __future__ import annotations

import pytest

from myrm_agent_harness.toolkits.acp.client_identity import ClientIdentity
from myrm_agent_harness.toolkits.acp.drivers import resolve_app_server_args
from myrm_agent_harness.toolkits.acp.mcp_servers import HttpMcpServer, McpServerConfig, StdioMcpServer
from myrm_agent_harness.toolkits.acp.runtime.codex.protocol import (
    OPT_OUT_NOTIFICATIONS,
    WorkspaceWriteSettings,
    config_read_params,
    initialize_params,
    login_params,
    resume_params,
    server_version,
    thread_params,
    turn_params,
    turn_policy,
)
from myrm_agent_harness.toolkits.acp.runtime.codex.turn_events import READ_NOTIFICATIONS

_READ_ONLY = {"type": "readOnly", "networkAccess": False}
_CLIENT = ClientIdentity()
_ASKING = turn_policy("safe", can_ask=True, workspace_write=WorkspaceWriteSettings())


# -- the policy of a turn ------------------------------------------------------------------------------------------------


def test_a_safe_turn_with_somebody_to_ask_runs_under_the_read_only_preset() -> None:
    policy = turn_policy("safe", can_ask=True, workspace_write=WorkspaceWriteSettings(("/data",), True))

    assert (policy.approval, policy.sandbox, policy.sandbox_policy) == ("on-request", "read-only", _READ_ONLY)


def test_a_safe_turn_with_nobody_to_ask_may_only_read() -> None:
    policy = turn_policy("safe", can_ask=False, workspace_write=WorkspaceWriteSettings(("/data",), True))

    assert (policy.approval, policy.sandbox, policy.sandbox_policy) == ("never", "read-only", _READ_ONLY)


@pytest.mark.parametrize("can_ask", [True, False])
def test_allow_all_acts_in_the_workspace_sandbox_with_the_users_settings_and_asks_nothing(can_ask: bool) -> None:
    settings = WorkspaceWriteSettings(("/data",), True, True, False)

    policy = turn_policy("allow_all", can_ask=can_ask, workspace_write=settings)

    assert (policy.approval, policy.sandbox) == ("never", "workspace-write")
    assert policy.sandbox_policy == {
        "type": "workspaceWrite",
        "writableRoots": ["/data"],
        "networkAccess": True,
        "excludeTmpdirEnvVar": True,
        "excludeSlashTmp": False,
    }


def test_the_sandbox_settings_of_the_users_config_are_read_defensively() -> None:
    config = {
        "sandbox_workspace_write": {
            "writable_roots": ["/a", 3, None, "/b"],
            "network_access": True,
            "exclude_tmpdir_env_var": "yes",
        }
    }

    assert WorkspaceWriteSettings.of(config) == WorkspaceWriteSettings(("/a", "/b"), network_access=True)
    assert WorkspaceWriteSettings.of({}) == WorkspaceWriteSettings()
    assert WorkspaceWriteSettings.of({"sandbox_workspace_write": "nonsense"}) == WorkspaceWriteSettings()


# -- the requests --------------------------------------------------------------------------------------------------------------


def test_the_handshake_names_the_client_and_leaves_out_the_notifications_nobody_reads() -> None:
    params = initialize_params(_CLIENT)

    assert params["clientInfo"] == {"name": "myrm_agent_harness", "title": "Myrm Agent", "version": _CLIENT.version}
    assert params["capabilities"] == {"optOutNotificationMethods": list(OPT_OUT_NOTIFICATIONS)}


def test_a_host_names_itself_in_the_handshake_and_reads_the_server_version_after_that_name() -> None:
    host = ClientIdentity(name="acme.runner-2", title="Acme Runner", version="4.5.6")

    assert initialize_params(host)["clientInfo"] == {
        "name": "acme.runner-2",
        "title": "Acme Runner",
        "version": "4.5.6",
    }
    assert server_version({"userAgent": "acme.runner-2/0.172.0 (Linux; x86_64) (acme.runner-2; 4.5.6)"}, host) == (
        0,
        172,
        0,
    )
    # The default name is not this host's: a greeting that repeats it is not one this host was answered with.
    assert server_version({"userAgent": "myrm_agent_harness/0.172.0 (Linux; x86_64)"}, host) is None


def test_no_notification_the_host_reads_is_opted_out_of() -> None:
    assert len(set(OPT_OUT_NOTIFICATIONS)) == len(OPT_OUT_NOTIFICATIONS)
    assert not set(OPT_OUT_NOTIFICATIONS) & READ_NOTIFICATIONS


def test_the_version_of_a_server_is_read_from_its_user_agent() -> None:
    greeting = {"userAgent": "myrm_agent_harness/0.172.0 (Mac OS 26.5.2; arm64) unknown (myrm_agent_harness; 1.0.0)"}

    assert server_version(greeting, _CLIENT) == (0, 172, 0)


def test_a_prerelease_server_is_read_as_the_release_it_leads_to() -> None:
    assert server_version({"userAgent": "myrm_agent_harness/0.173.0-alpha.2 (Linux; x86_64)"}, _CLIENT) == (0, 173, 0)


@pytest.mark.parametrize(
    "greeting",
    [
        {},
        {"userAgent": None},
        {"userAgent": "mock"},
        {"userAgent": "other_client/0.172.0 (Linux; x86_64)"},
        {"userAgent": "myrm_agent_harness/dev (Linux; x86_64)"},
        {"userAgent": "myrm_agent_harness/0.172 (Linux; x86_64)"},
    ],
)
def test_a_server_that_does_not_say_its_version_has_none(greeting: dict[str, object]) -> None:
    assert server_version(greeting, _CLIENT) is None


def test_the_login_and_the_config_read_requests() -> None:
    assert login_params("sk-1") == {"type": "apiKey", "apiKey": "sk-1"}
    assert config_read_params("/w") == {"includeLayers": False, "cwd": "/w"}


def test_a_thread_is_rooted_in_the_workspace_under_the_policy() -> None:
    params = thread_params("/w", _ASKING, mcp_servers=[], ephemeral=False)

    assert params == {"cwd": "/w", "approvalPolicy": "on-request", "sandbox": "read-only"}


def test_an_ephemeral_thread_says_so() -> None:
    assert thread_params("/w", _ASKING, mcp_servers=[], ephemeral=True)["ephemeral"] is True


def test_a_stdio_server_has_an_environment_only_when_it_has_one() -> None:
    servers = [
        StdioMcpServer(name="my-server_1", command="mcp", args=["--x"], env={"A": "b"}),
        StdioMcpServer(name="plain", command="other"),
    ]

    config = thread_params("/w", _ASKING, mcp_servers=servers, ephemeral=False)["config"]

    assert config == {
        "mcp_servers": {
            "my-server_1": {"command": "mcp", "args": ["--x"], "required": True, "env": {"A": "b"}},
            "plain": {"command": "other", "args": [], "required": True},
        }
    }


def test_an_http_server_is_a_url_with_its_headers() -> None:
    servers = [
        HttpMcpServer(name="memory", url="http://127.0.0.1:9/mcp", headers={"Authorization": "Bearer t"}),
        HttpMcpServer(name="open", url="https://example.test/mcp"),
    ]

    config = thread_params("/w", _ASKING, mcp_servers=servers, ephemeral=False)["config"]

    assert config == {
        "mcp_servers": {
            "memory": {
                "url": "http://127.0.0.1:9/mcp",
                "required": True,
                "http_headers": {"Authorization": "Bearer t"},
            },
            "open": {"url": "https://example.test/mcp", "required": True},
        }
    }


def test_the_tools_of_a_read_only_server_are_approved_in_advance_and_those_of_any_other_are_asked_about() -> None:
    servers: list[McpServerConfig] = [
        HttpMcpServer(name="memory", url="http://127.0.0.1:9/mcp", read_only=True),
        StdioMcpServer(name="local", command="mcp", read_only=True),
        StdioMcpServer(name="other", command="mcp"),
    ]

    config = thread_params("/w", _ASKING, mcp_servers=servers, ephemeral=False)["config"]
    assert isinstance(config, dict)
    entries = config["mcp_servers"]

    assert [name for name, entry in entries.items() if entry.get("default_tools_approval_mode") == "approve"] == [
        "memory",
        "local",
    ]


def test_a_resumed_thread_is_given_the_servers_again() -> None:
    servers = [HttpMcpServer(name="memory", url="http://127.0.0.1:9/mcp")]

    config = resume_params("t-1", "/w", _ASKING, mcp_servers=servers)["config"]

    assert config == {"mcp_servers": {"memory": {"url": "http://127.0.0.1:9/mcp", "required": True}}}


def test_a_resumed_thread_is_asked_for_without_its_history() -> None:
    params = resume_params("t-1", "/w", _ASKING, mcp_servers=[])

    assert params["threadId"] == "t-1"
    assert params["excludeTurns"] is True
    assert "ephemeral" not in params


def test_a_turn_carries_the_prompt_and_the_images_as_input_not_as_arguments() -> None:
    params = turn_params("t-1", "hello", ["/a.png", "/b.png"], _ASKING)

    assert params["input"] == [
        {"type": "text", "text": "hello", "text_elements": []},
        {"type": "localImage", "path": "/a.png"},
        {"type": "localImage", "path": "/b.png"},
    ]
    assert (params["threadId"], params["approvalPolicy"], params["sandboxPolicy"]) == ("t-1", "on-request", _READ_ONLY)


# -- the launch ----------------------------------------------------------------------------------------------------------------


def test_the_app_server_starts_with_its_subcommand_and_the_overrides_the_config_carries() -> None:
    launch = resolve_app_server_args(
        "/usr/bin/codex",
        ["-c", "model_provider=x", "--config=a.b=1", "--enable", "f", "--strict-config"],
        "subscription",
    )

    assert launch.args == [
        "app-server",
        "-c",
        "model_provider=x",
        "--config",
        "a.b=1",
        "--enable",
        "f",
        "--strict-config",
    ]
    assert launch.dropped == []


def test_the_model_and_profile_flags_of_an_exec_config_become_the_overrides_they_stand_for() -> None:
    launch = resolve_app_server_args("codex", ["-m", "o3", "--model=gpt-5", "-p", "work"], "subscription")

    assert launch.args == ["app-server", "-c", 'model="o3"', "-c", 'model="gpt-5"', "-c", 'profile="work"']


def test_what_chooses_the_policy_or_belongs_to_exec_is_dropped_and_reported() -> None:
    launch = resolve_app_server_args(
        "codex", ["exec", "--json", "--full-auto", "--sandbox", "workspace-write", "-m"], "subscription"
    )

    assert launch.args == ["app-server"]
    assert launch.dropped == ["exec", "--json", "--full-auto", "--sandbox", "workspace-write", "-m"]


def test_an_api_key_login_is_kept_in_memory() -> None:
    launch = resolve_app_server_args("codex", ["-c", "a=1"], "api_key")

    assert launch.args == ["app-server", "-c", "a=1", "-c", 'cli_auth_credentials_store="ephemeral"']


def test_a_command_that_is_not_the_app_server_agent_keeps_its_args() -> None:
    launch = resolve_app_server_args("/opt/my-codex-wrapper", ["exec", "--json"], "api_key")

    assert (launch.args, launch.dropped) == (["exec", "--json"], [])

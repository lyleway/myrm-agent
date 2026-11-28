"""The contract of every known external agent: the one place its login, install, launch, transport and permission facts live.

Login, status, launch, install and the settings screens each used to keep their own copy of what a CLI is
(where its login is kept, which flags it takes, which package installs it). They all read ``DriverSpec`` now, so a
fact changes in one place and an agent is added by one entry.

[INPUT]
- toolkits.acp.drivers.spec::DriverSpec, LoginStrategy, PermissionFlags, Transport (POS: The shape of a known agent's contract)
- toolkits.acp.drivers.registry::all_drivers, cli_launch_args, command_driver, driver_for, effective_transport, known_backends (POS: The known agents and the lookups that find one by backend name or command)
- toolkits.acp.drivers.launch::AcpLaunch, AppServerLaunch, resolve_acp_args, resolve_app_server_args, resolve_cli_args (POS: The command line of a known agent: one resolver per transport)

[OUTPUT]
- DriverSpec, LoginStrategy, PermissionFlags, Transport, all_drivers, cli_launch_args, command_driver, driver_for, effective_transport, known_backends
- AcpLaunch, AppServerLaunch, resolve_acp_args, resolve_app_server_args, resolve_cli_args

[POS]
Public surface of the driver registry.
"""

from __future__ import annotations

from myrm_agent_harness.toolkits.acp.drivers.launch import (
    AcpLaunch,
    AppServerLaunch,
    resolve_acp_args,
    resolve_app_server_args,
    resolve_cli_args,
)
from myrm_agent_harness.toolkits.acp.drivers.registry import (
    all_drivers,
    cli_launch_args,
    command_driver,
    driver_for,
    effective_transport,
    known_backends,
)
from myrm_agent_harness.toolkits.acp.drivers.spec import DriverSpec, LoginStrategy, PermissionFlags, Transport

__all__ = [
    "AcpLaunch",
    "AppServerLaunch",
    "DriverSpec",
    "LoginStrategy",
    "PermissionFlags",
    "Transport",
    "all_drivers",
    "cli_launch_args",
    "command_driver",
    "driver_for",
    "effective_transport",
    "known_backends",
    "resolve_acp_args",
    "resolve_app_server_args",
    "resolve_cli_args",
]

"""A function proxy of the generated PTC client reads like a function to a script that introspects it.

Scripts label their results with ``fn.__name__``. A proxy object that lacks it raised ``AttributeError`` and
failed the whole script, which then had to be re-sent.
"""

from __future__ import annotations

import subprocess
import sys

from myrm_agent_harness.agent.skills.mcp.client_templates import generate_ipc_client_code


def _run_script(
    user_code: str,
    *,
    skill_functions: dict[str, list[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the generated client followed by the script in a fresh interpreter (the client installs import hooks)."""
    client = generate_ipc_client_code(
        "/tmp/unused.sock",
        auth_token="t",
        trace_nonce="n",
        skill_functions=skill_functions,
    )
    return subprocess.run(
        [sys.executable, "-c", f"{client}\n{user_code}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_skill_and_tool_proxies_expose_the_function_name() -> None:
    result = _run_script(
        "from skills.mcp_demo_skill import create_issue, list_issues\n"
        "from tools.web import search\n"
        "print([fn.__name__ for fn in (create_issue, list_issues, search)])\n"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['create_issue', 'list_issues', 'search']"


def test_star_import_binds_declared_skill_functions() -> None:
    result = _run_script(
        "import skills.mcp_demo_skill as mod\n"
        "from skills.mcp_demo_skill import *\n"
        "print(mod.__all__, [fn.__name__ for fn in (create_issue, list_issues)])\n",
        skill_functions={"mcp_demo_skill": ["create_issue", "list_issues"]},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['create_issue', 'list_issues'] ['create_issue', 'list_issues']"


def test_star_import_allows_declared_leading_underscore_but_rejects_dunder_and_undeclared() -> None:
    result = _run_script(
        "import skills.mcp_demo_skill as mod\n"
        "from skills.mcp_demo_skill import *\n"
        "assert _2d_plot.__name__ == '_2d_plot'\n"
        "assert list_issues.__name__ == 'list_issues'\n"
        "for attr in ('__nonexistent__', '_undeclared'):\n"
        "    try:\n"
        "        getattr(mod, attr)\n"
        "    except AttributeError:\n"
        "        pass\n"
        "    else:\n"
        "        raise AssertionError(f'expected AttributeError for {attr}')\n"
        "print('ok')\n",
        skill_functions={"mcp_demo_skill": ["_2d_plot", "list_issues"]},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_star_import_of_undeclared_skill_exports_empty_all() -> None:
    result = _run_script(
        "import skills.mcp_empty_skill as mod\nfrom skills.mcp_empty_skill import *\nprint(mod.__all__)\n",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"

"""The ``agent.security`` facade resolves every advertised name from where it is defined.

[INPUT]
- myrm_agent_harness.agent.security: the lazy public security facade
- myrm_agent_harness.agent.security.restrictions: where the restriction-layer helpers are defined

[OUTPUT]
- Regression guard for the facade's lazy resolution: every advertised name resolves, the restriction helpers
  are the objects their defining module holds, an unknown name raises AttributeError, and ``dir()`` lists
  every advertised name without the import helper.

[POS]
Unit tests mirroring src/myrm_agent_harness/agent/security/__init__.py.
"""

from __future__ import annotations

import pytest

import myrm_agent_harness.agent.security as security
from myrm_agent_harness.agent.security import restrictions


def test_every_advertised_name_resolves() -> None:
    missing = [name for name in security.__all__ if not hasattr(security, name)]
    assert missing == []


@pytest.mark.parametrize("name", ["restriction_layer", "disabled_permissions"])
def test_restriction_helpers_are_the_objects_their_module_defines(name: str) -> None:
    assert getattr(security, name) is getattr(restrictions, name)


def test_an_unknown_name_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="has no attribute 'no_such_name'"):
        security.__getattr__("no_such_name")


def test_dir_lists_every_advertised_name_and_hides_the_import_helper() -> None:
    listed = dir(security)
    assert set(security.__all__) <= set(listed)
    assert "_import_module" not in listed

"""YAML loading for documents from outside the process, whose strings must stay UTF-8 encodable.

``yaml.safe_load`` turns a double-quoted surrogate escape into a lone surrogate, which no JSON response, HTTP body or
SQLite bind can encode, so one such value in a SKILL.md frontmatter fails every request that echoes it. The loader here
replaces the surrogates where each string is built, so the parsed document is encodable at any depth without a second
walk over the result (which would not end on a self-referencing alias and would pay once per reference for an alias
fan-out).

[INPUT]
- utils.text_sanitizer::encodable_text (POS: repairs a text that only has to become UTF-8 encodable, replacing each lone surrogate with ``U+FFFD``)

[OUTPUT]
- load_clean_yaml: ``yaml.safe_load`` of one document whose every string, key or value at any depth is UTF-8 encodable

[POS]
Parse step for skill frontmatter and package manifests that come from a market repository or a local directory the user pointed at.
"""

from __future__ import annotations

import yaml

from myrm_agent_harness.utils.text_sanitizer import encodable_text

__all__ = ["load_clean_yaml"]


class _CleanTextLoader(yaml.SafeLoader):
    """``SafeLoader`` that makes every string it builds UTF-8 encodable."""


def _construct_encodable_str(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> str:
    return encodable_text(loader.construct_scalar(node))


_CleanTextLoader.add_constructor("tag:yaml.org,2002:str", _construct_encodable_str)


def load_clean_yaml(text: str) -> object:
    """Parse one YAML document like ``yaml.safe_load`` (same types, same errors), every string made encodable.

    A string is repaired once, where its node is built. An alias reuses the object its anchor built, so the cost does
    not grow with the number of references and a self-referencing document loads as it does under ``safe_load``.
    """
    loader = _CleanTextLoader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()

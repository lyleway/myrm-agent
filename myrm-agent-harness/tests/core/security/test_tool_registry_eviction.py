"""Tests for dynamic tool registry eviction & thread safety."""

import threading

from myrm_agent_harness.core.security.tool_registry.registry import (
    MCPAnnotations,
    SafetyMetadata,
    evict_ptc_safety_metadata_by_prefix,
    get_ptc_safety_metadata,
    register_ptc_safety_metadata,
    resolve_safety_metadata,
    unregister_ptc_safety_metadata,
)


def test_register_and_unregister_ptc_safety_metadata():
    tool_a = "test_tool_alpha"
    tool_b = "test_tool_beta"

    meta_a = SafetyMetadata(is_read_only=True, is_concurrent_safe=True)
    meta_b = SafetyMetadata(is_destructive=True)
    anno = MCPAnnotations(destructiveHint=True)

    register_ptc_safety_metadata(tool_a, meta_a, anno)
    register_ptc_safety_metadata(tool_b, meta_b, anno)

    assert get_ptc_safety_metadata(tool_a) is not None
    assert resolve_safety_metadata(tool_a).is_read_only is True
    assert resolve_safety_metadata(tool_b).is_destructive is True

    # Unregister tool_a only
    assert unregister_ptc_safety_metadata(tool_a) is True
    assert get_ptc_safety_metadata(tool_a) is None
    # tool_a now resolves to fail-closed defaults
    assert resolve_safety_metadata(tool_a).is_read_only is False

    # tool_b still present
    assert get_ptc_safety_metadata(tool_b) is not None
    assert resolve_safety_metadata(tool_b).is_destructive is True

    # Unregister non-existent
    assert unregister_ptc_safety_metadata("non_existent") is False
    assert unregister_ptc_safety_metadata(tool_a) is False


def test_registering_a_name_again_replaces_its_record():
    """One name has one record: the later registration is the one every consumer reads."""
    name = "mcp__demo__act"
    register_ptc_safety_metadata(name, SafetyMetadata(is_destructive=True), MCPAnnotations(destructiveHint=True))
    register_ptc_safety_metadata(name, SafetyMetadata(is_concurrent_safe=True), MCPAnnotations())

    assert get_ptc_safety_metadata(name) == (SafetyMetadata(is_concurrent_safe=True), MCPAnnotations())
    assert resolve_safety_metadata(name).is_destructive is False


def test_evict_by_prefix_drops_the_prefixed_tools_and_counts_them():
    meta = SafetyMetadata(is_concurrent_safe=True)
    anno = MCPAnnotations()
    register_ptc_safety_metadata("mcp__batch__one", meta, anno)
    register_ptc_safety_metadata("mcp__batch__two", meta, anno)

    assert resolve_safety_metadata("mcp__batch__one").is_concurrent_safe is True

    assert evict_ptc_safety_metadata_by_prefix("mcp__batch__") == 2

    assert get_ptc_safety_metadata("mcp__batch__one") is None
    assert get_ptc_safety_metadata("mcp__batch__two") is None
    assert resolve_safety_metadata("mcp__batch__one").is_concurrent_safe is False

    # Evicting again, or an unknown prefix, drops nothing
    assert evict_ptc_safety_metadata_by_prefix("mcp__batch__") == 0
    assert evict_ptc_safety_metadata_by_prefix("mcp__never_registered__") == 0


def test_evict_by_prefix_keeps_the_tools_of_other_prefixes():
    """The ``__`` that closes a server's prefix keeps ``a`` from taking ``a_b``'s tools with it."""
    meta = SafetyMetadata(is_concurrent_safe=True)
    anno = MCPAnnotations()
    register_ptc_safety_metadata("mcp__a__x", meta, anno)
    register_ptc_safety_metadata("mcp__a_b__x", meta, anno)
    register_ptc_safety_metadata("file_read_tool_like", meta, anno)

    assert evict_ptc_safety_metadata_by_prefix("mcp__a__") == 1

    assert get_ptc_safety_metadata("mcp__a__x") is None
    assert get_ptc_safety_metadata("mcp__a_b__x") is not None
    assert get_ptc_safety_metadata("file_read_tool_like") is not None


def test_ptc_registry_concurrent_thread_safety():
    meta = SafetyMetadata(is_read_only=True)
    anno = MCPAnnotations()

    def worker(idx: int):
        tname = f"mcp__stress__tool_{idx}"
        for _ in range(50):
            register_ptc_safety_metadata(tname, meta, anno)
            _ = resolve_safety_metadata(tname)
            unregister_ptc_safety_metadata(tname)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert evict_ptc_safety_metadata_by_prefix("mcp__stress__") == 0

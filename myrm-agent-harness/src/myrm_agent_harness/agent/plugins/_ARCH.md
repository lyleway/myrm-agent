# agent/plugins/

## Overview
Agent Plugins 1.0.0 standard parser and writer — a framework-level, client-agnostic tool
that resolves the portable plugin package model (https://agent-plugins.org/schemas/1.0.0)
into structured component records for skills, MCP servers, agent profiles and workspace
templates, and writes the strict inverse (deterministic ZIP verified by parsing it back).

This module ONLY **parses, validates and writes** plugin packages. It does NOT persist
anything. Persistence (SkillStore install, global `mcpServers` config, Agent profile
binding) is owned by the business layer (`myrm-agent-server`), which consumes the
[DATA](#output) records below and feeds `PluginBundleSpec` on export.

Client-specific data (agent profiles, workspace templates, entry agent) lives under the
`ai.myrm` namespace (spec §8): `ai.myrm/agents/<slug>.md`, `ai.myrm/workspace/`,
`extensions["ai.myrm"]`. The community top-level layout (`agents/`, `workspace/`,
`template_files/`, top-level `entry_agent`) is accepted for inbound packages only.

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| `__init__.py` | Package | Agent Plugins parsing & exporting module. | ✅ |
| `manifest.py` | Core | `AgentPluginManifest` + strict spec 1.0.0 validation (closed schema, name constraints, `$schema` version negotiation); `decode_manifest_json` refuses a plugin.json over 1 MiB before it decodes anything (`DocumentTooLargeError`, which refuses the whole package) and loads the rest through `load_clean_json`, so every string of the manifest is UTF-8 encodable and a manifest nested deeper than 64 levels is invalid JSON (`manifest_invalid_json`, fatal to the plugin). | ✅ |
| `mcp_config.py` | Core | `AgentPluginMcpConfig` + per-server variant parsing (stdio / streamable-http / sse), placeholder scanning and containment; `decode_mcp_json` refuses an mcp.json over 1 MiB before it decodes anything (`DocumentTooLargeError`, which refuses the whole package) and loads the rest through `load_clean_json`, so every string of the config is UTF-8 encodable and a config nested deeper than 64 levels is invalid JSON (`mcp_invalid_json`: MCP is off, the skills stay). | ✅ |
| `parser.py` | Core | `AgentPluginParser.parse_zip` orchestrating discovery (skills/, mcp.json, then client-specific agents/workspace via `agents.py`) with per-component failure isolation. Retains non-skill files on `PluginParseResult.files` for the business layer to persist into the plugin root; reports hidden files dropped by the archive filter as an INFO diagnostic. Reads both archive shapes: `plugin.json` at the archive root (flat) or one wrapper directory around the plugin (dropped). A document over its decode ceiling is not a component failure: `DocumentTooLargeError` leaves `parse_zip` and refuses the whole package, like an archive over the zip limits. | ✅ |
| `agents.py` | Core | `discover_agents` / `discover_workspace_files`: `ai.myrm/` namespace first, community layout as inbound fallback; entry agent from `extensions["ai.myrm"].entryAgent`. | ✅ |
| `frontmatter.py` | Core | `split_frontmatter`: `---` YAML header + body split shared by skill and agent files; a header over 64 Ki characters is refused before it is decoded (`DocumentTooLargeError`, which refuses the whole package) and the rest loads through `load_clean_yaml`, so every string of it is UTF-8 encodable; the caller names the file, so the refusal says which one. | ✅ |
| `rules.py` | Core | Packaging rules SSOT for parse and write sides: plugin name predicate (≤ 64), lossless ASCII `plugin_identity` (hash suffix for non-ASCII names), `is_excluded_path`, `ai.myrm` layout constants, `AGENT_STRUCTURAL_KEYS` (agent frontmatter keys owned by the format; any other key is client data), capacity ceilings (the upload and the template files, and the decode ceilings `MAX_PLUGIN_JSON_BYTES` / `MAX_FRONTMATTER_CHARS`), `DocumentTooLargeError` / `ensure_document_size` (the refusal of a document over its decode ceiling). | ✅ |
| `writer.py` | Core | `PluginBundleSpec` + `build_plugin_bundle`: strict inverse of the parser (manifest, `mcp.json`, skills, `ai.myrm/agents`, `ai.myrm/workspace`), deterministic ZIP (same input → same bytes), rejects instead of silently dropping. | ✅ |
| `roundtrip.py` | Core | `verify_bundle`: parse-back self-check run by the writer; any writer/parser asymmetry becomes a build error. | ✅ |
| `exporter.py` | Core | `AgentPluginPacker.package_skill_as_plugin`: single-skill convenience wrapper over the bundle writer (skill keeps its real directory name). | ✅ |
| `models.py` | Core | Shared dataclasses: `PluginCapabilityTier` (sandbox capability levels: read_only, fs_read, fs_write, network, shell_exec, destructive), `PluginSkill`, `PluginAgent`, `PluginMcpServer`, `PluginDiagnostic`, `PluginParseResult` (incl. `agents`, `workspace_files`, `aggregated_capabilities`, and non-skill `files`), `AgentPluginManifestMeta` (incl. validated `extensions`). | ✅ |
| `integrity.py` | Core | `verify_plugin_packaging_integrity`, `verify_mcp_server_artifacts`, & `infer_server_capabilities` ensuring stdio servers verify referenced build artifacts/entrypoints and infer granular sandbox capability tiers at parse time (prevents subprocess crashes on unbuilt packages and enforces zero-trust permissions). | ✅ |

## I/O

[PARSE]
- zip bytes → safe_extract_zip (framework `backends.skills.scanning`)
- plugin.json (root) → strict manifest validation
- skills/ (fixed location, non-recursive SKILL.md gate)
- mcp.json (root) → per-server variant validation

[OUTPUT]
- `PluginParseResult` (meta + skills + servers + agents + workspace files + diagnostics) —
  business layer consumes this to install skills and MCP servers, create agents and to
  surface component-level diagnostics.

[WRITE]
- `PluginBundleSpec` (business layer assembles: redacted skills, portable MCP servers,
  agent profiles, workspace templates) → `build_plugin_bundle` → deterministic ZIP,
  verified by `roundtrip.verify_bundle` (parse-back) before it is returned.

## Key Dependencies

- `backends.skills.scanning.zip_extract::safe_extract_zip` — secure extraction.
- `backends.skills.scanning.archive_security` — typed archive security errors.
- `backends.skills.types_enums` — `SkillTrust` (business maps INSTALLED trust layer).
- `utils.clean_json::load_clean_json` / `utils.clean_yaml::load_clean_yaml` — decode plugin.json, mcp.json and the YAML header of a skill or agent file so that every string is UTF-8 encodable; the JSON loader also refuses nesting deeper than `MAX_NESTING` (64) levels. `rules::ensure_document_size` keeps a document over its decode ceiling from reaching either loader.

## Design Principles (spec-conformant)

1. **Version negotiation**: recognize exact canonical `$schema` IDs; never fetch a
   schema at load time; reject unsupported versions (fatal to the plugin).
2. **Closed schema**: unknown top-level `plugin.json` fields and non-object
   `extensions` are non-fatal; any other manifest violation is fatal.
3. **Localized failures**: a bad skill is skipped; invalid/MCP-mismatched `mcp.json`
   disables MCP for the plugin but never other components; per-server variants isolate.
4. **No persistence / no LLM** — pure offline parse and write.
5. **Writer is the parser's inverse**: it never silently drops (invalid names, excluded
   paths, oversized files and non-portable MCP servers are errors), so what the caller
   asked to ship is exactly what ships; secrets are never carried (env/headers are
   placeholders supplied by the caller).
6. **Encodable text, bounded nesting**: an archive is text the user did not write, and its
   strings reach the preview response, the staged session and the stores. Every decoder
   (plugin.json, mcp.json, the YAML header of a skill or agent file) therefore turns a lone
   surrogate escape into `U+FFFD` at decode, so no record the parser returns can fail an
   encode later. The two JSON decoders also refuse a document nested deeper than 64 levels,
   as invalid input and not as an exception: the JSON parser reads about ten thousand levels,
   but the staged session is pickled and the result walked recursively, which gives out after
   a few thousand.
7. **Bounded documents**: the time and memory of a decoder grow with the text it reads, and
   the archive limits (20 MiB upload, 50 MiB extracted, 4096 entries) let one document take
   most of the archive. A plugin.json or mcp.json over 1 MiB and the YAML header of a skill
   or agent file over 64 Ki characters are therefore refused before any decoder reads them,
   and the refusal refuses the whole package: a capacity violation is archive-level, like the
   zip limits, whereas a document that is merely malformed stays a diagnostic of its own
   component (principle 3). The ceilings bound each document, not the sum of the documents
   of a package.

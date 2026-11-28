# humanize — FE SSOT for tool presentation

## Purpose

Single frontend module that turns raw `tool_name` + args into human-readable one-liners for:

- ProgressSteps titles
- Approval card headlines (SingleApprovalCard, PolymorphicApprovalCard)

## Public API (`index.ts`)

| Export                    | Role                                                                                                                                                                                          |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `humanizeProgressStep`    | Progress item title（`status=cancelled` → ask tense）                                                                                                                                         |
| `humanizeApprovalTitle`   | Pending approval headline                                                                                                                                                                     |
| `humanizeToolLine`        | One-liner for a tool in `progress` / `approval` / `ask` tense (the two titles above wrap it; blocked-action rows call it directly); the optional last parameter names MCP servers (see below) |
| `McpServerNameResolver`   | Type of that parameter: identity in a tool name → the name the user gave the server, `undefined` when no configured server has it                                                             |
| `readableMcpServerName`   | The server behind a tool name as the user knows it: the resolver's name, else an identity a person can read, else `undefined`; for surfaces that word "no name" themselves (see below)        |
| `classifyApprovalSurface` | `compact` vs `full` approval layout                                                                                                                                                           |
| `resolveScopeNote`        | Plain-words scope hint (local / external / connector)                                                                                                                                         |

## i18n

Namespace: `humanize.*` in both locales (`en`, `zh`).

Subtrees: `progress`, `approval`, `ask`, `scope`, `fallback`.

A tool with no dedicated line reads `fallback.used_tool` ("Used …"); in `ask` tense, where the action never ran, it reads `ask.tool_generic` ("Wanted to use …") instead, and a locale without that key keeps the past-tense line.

## MCP tool lines

The model calls an MCP tool as `mcp__{identity}__{tool}`; the identity is derived from the server's name (`lib/utils/mcp-config/mcpServerIdentity.ts`) and is not the name the user typed (`天气` is `unnamed_dd4acd`). `humanizeToolLine` words every `mcp__` tool first, before the keyword rules of the built-in tools, so a connector tool whose name shares a word with a built-in (`mcp__ops__run_bash`, `mcp__memory__create_entities`) is never worded as that built-in. The server part of the line is:

1. the name the resolver returns for the identity, else
2. the identity itself when a person can read it (`github`), else
3. `fallback.service` ("a connected service") for the opaque identities `unnamed` / `unnamed_{6 hex}`, or the identity when a locale lacks that key.

Steps 1 and 2 are `readableMcpServerName`, which answers `undefined` for an opaque identity; a surface with its own wording for "no name" (the MCP App viewer: an untitled app, a link prompt without a source) uses it directly.

The resolver is injected: this module reads no store. `hooks/shared/useMcpServerNameResolver.ts` builds it from the settings.

## Wiring

| Consumer                                  | Usage                                                                   |
| ----------------------------------------- | ----------------------------------------------------------------------- |
| `progress-steps/utils.ts`                 | `getStepTitle(..., tHumanize, serverName)` — humanize SSOT 唯一标题入口 |
| `ApprovalDetails.tsx`                     | title + scope note                                                      |
| `SingleApprovalCard.tsx`                  | compact payload hide                                                    |
| `PolymorphicApprovalCard.tsx`             | subagent tool call headers + scope (`ApprovalScopeNoteLine`)            |
| `features/cron/CronRunBlockedActions.tsx` | rows of the actions a run's safety rules stopped, in `ask` tense        |
| `artifacts/renderers/McpAppViewer.tsx`    | iframe title and the source named in the desktop link prompt            |

`ProgressSteps`, `ApprovalDetails`, `SubagentApprovalContent` and `CronRunBlockedActions` each call `useMcpServerNameResolver()` and pass the result on; `McpAppViewer` calls it and asks `readableMcpServerName` itself.

## Boundaries

- FE-only; no harness/server/prompt changes.
- External scope is exactly the tools in `EXTERNAL_TOOLS` (`channel_notify_tool`, `artifact_publish`): the tools of the backend's
  socially irreversible set (`_IRREVERSIBLE_SOCIAL_TOOLS`, harness `core/security/spend_governance.py`) that exist. Keep the two lists in step.
  `channel_notify_tool` names its `destination` parameter as given in the scope note and the approval title; `artifact_publish`
  reads `scope.external_unknown` because its hosting target is an id nobody can read. A colon in a `target` never makes a call external.
- `#8 save_skill` preview UI：`SaveSkillApprovalPreview` + `saveSkillApproval.ts`；humanize 仅负责 headline/scope。

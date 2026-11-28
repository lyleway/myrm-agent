# file_ops/

## Overview
File operations tool module.

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | File operations tool module. | — |
| constants.py | Core | Constants, including the read size limits: `MAX_FILE_READ_SIZE_BYTES` for a whole-file read, the higher `MAX_RANGED_READ_SIZE_BYTES` for a read with a line range (the way into a big file), and `REASON_FIELD_DESCRIPTION`, the `reason` parameter text the three file tools share. | ✅ |
| executor_storage_adapter.py | Core | CodeExecutor to StorageProvider adapter. | ✅ |
| file_edit_tool.py | Core | Batch atomic file edit via edits[]; LLM payload normalizer; whitespace-tolerant matching (a block whose lines differ is refused). A failed match carries the hint for its kind (`EditMatchError.kind`: not found, matches more than once, overlaps another edit), not one picked from the wording of the message. The parameter schema is one English text for every locale; only the description head is localized, and it states the read-before-edit rule with its exceptions | ✅ |
| file_read_tool.py | Core | File read tool factory: local/MCP/**vault://** paths, Pydantic alias mapping, pre-IO device and network-share protection (decided on the path text, before any filesystem call), Unicode auto-healing of a path that names nothing (`core.security.path.find_existing_unicode_path`; the permission engine judges the typed and the healed spelling alike, and the healer never lands on a device or on a credential file the typed text does not name), batch reads, line ranges, multimodal; the Fast-track scope allows UECD spill, uploads and the spilled long-paste directories (`context_guard` constants) and rejects `..` escapes (guards in `core/file_read/scope.py`). The parameter schema is one English text for every locale; only the description head is localized | ✅ |
| file_write_tool.py | Core | File write tool. Creates new files (missing parent directories included); rejects empty/whitespace-only content and paths that already exist. Same one-English-schema rule as the other two tools | ✅ |
| revert_service.py | Core | File revert service — undo AI file changes of a message or of a whole session; surfaces revertible/skip_reason; skips non-revertible snapshots and non-UTF-8 files on revert; records each restored file as modified and each created file it removed as deleted in the artifact trail of the session it is for (a revert runs outside any agent turn, so the session comes from the call, not from a bound chat). | ✅ |
| validators/ | Submodule | Pre/post write validators including vault markdown FM guard and Office text-write warnings. | ✅ |
| streaming.py | Core | File streaming reader. Two readers behind `stream` / `preview` mode: `read_file_chunked(max_chars=)` reads in chunks and stops exactly one character past the cap so a caller can tell the file goes on; `read_file_preview` returns the first lines plus a hint to read on. | ✅ |

| Submodule | Description |
|-----------|-------------|
| core/ | Text Editor core business logic module. |
| observers/ | Observers module. |
| strategies/ | File system strategy module. |
| utils/ | Utility functions module. |
| validators/ | Validators module. |

## Rules for changing the file tools

- **Wire budget and first-call retest.** A change to the schema or description of `file_read_tool`, `file_edit_tool` or `file_write_tool` passes `tests/architecture/test_tool_wire_budget_gate.py`, which holds the per-tool and first-turn token limits. Parameter semantics are written in the schema only; the description keeps the capability scope. After any change to the text the model sees on the first turn, wording-only changes included, measure the first call and the end-to-end pass rate again: swapping one description head moved the share of first calls that chose `file_read_tool` from 19/80 to 3/80 (p < 0.001), and a near-identical pair of prompts moved in opposite directions by about 30 points. These runs used a test sandbox with Python disabled, whereas production allows Python, so the production first-call split is not directly measured. Judge by the total over eight or more prompts and by the end-to-end pass rate, not by one prompt.
- **No command-shaped parameters.** `tests/architecture/test_file_tools_take_no_command_parameters.py` fails when the schema of a file tool carries one. A file write must not become a shell channel that skips approval; hooks declared by skills still go through approval and the code-execution policy.
- **Windows path pre-checks run in CI.** The `windows-path-security` job (`.github/workflows/harness-test.yml`, `windows-latest`) runs the native Win32 path functions and file-system probes, and requires every gate in front of the file system to refuse before the first file-system call. That is all it proves; it is not a real credential-exfiltration run.
- **Judge what is opened.** The permission engine judges the file a tool opens (`agent/security/SECURITY_SYSTEM.md`, the paragraph on judging what is opened). "The path that is opened" has one definition, `spelled_path` and `opened_path` in `core/security/path/typed_path.py`, shared by the engine, directory grants and the healing probe. A new place that rewrites path text (trimming, expansion, healing, aliases) must make the verdict cover every file it can open and grant only the file actually opened; measure the verdict against what the real read and grep tools open before changing it.

## Evaluated and not done

Each row is a decision, not a backlog: the last column says what evidence would reopen it.

| Idea | Why not | Reopen when |
|------|---------|-------------|
| TS/JS parser to close the regex outline's blind spots | Measured in a 10k-file TS/JS tree: arrow functions whose parameters wrap onto the next line (360 lines, documented in `outline.py`), generators (12), `declare`/`namespace`/`module` (11), `abstract class` (1). The outline is a navigation hint and a missed symbol is still found with `grep_tool`; closing the rest needs a new dependency or a hand-written parser | An outline blind spot causes a failed read, or the product takes a TS/JS parser dependency for another reason |
| Serialising the `warnings.catch_warnings` window in `outline.py` | It changes a process-wide filter, so concurrent outline parses can restore it early or leave it set; the cost is a few more or fewer `SyntaxWarning` lines, never a different read result | Concurrent outline parsing shows filter crosstalk that changes a read |
| An `unchanged` stub for a repeated read of an unchanged file | Every read returns the full content. A stub can go stale after an outside edit, which is why another agent product withdrew its own | Data shows repeated reads of unchanged files waste a meaningful share of tokens, together with an invalidation rule that cannot return a stale stub |
| Using `infra/atomic_write.py` for user files | Its default mode `0o600` replaces the permissions of the target file. An atomic single-file write for user files must keep the original mode and write bytes | Atomic writes for user files are implemented |
| Hash-anchored editing | It changes the whole read/write protocol | The read/write protocol is redesigned |
| A hint to switch to a patch on a whole-file overwrite | `file_write_tool` only creates; an existing path is refused at validation, so the waste does not occur at the entry | Whole-file rewrites of large existing files appear in real use, or the entry allows overwriting an existing path |
| Chunked append writes | The write size limit is 5 MB and the tool only creates; no data shows models failing on a single over-long output | Data shows failures from over-long single outputs |
| Cross-process write protection | The per-path lock is an in-process `asyncio.Lock`; two processes editing one file are covered only by the exact-and-unique `old_str` match and the hash check, a window of milliseconds in which no lost update has been reproduced | A lost update between processes is observed, or atomic writes for user files are implemented |
| Cell-level editing of `.ipynb` | Reading is supported through `IpynbParser`; editing would either grow the `file_edit_tool` schema (first-turn budget) or add an on-demand tool, and there is no usage data | Notebook usage data, or a product need to edit cells |
| Refusing Windows-unportable names on non-Windows hosts | The platform-aware design in `core/security/path/filesystem.py` is the opposite on purpose; changing it is a platform policy change | Deliverables are reported as unopenable on Windows |
| Windows long paths (over 260 characters) | No handling exists and there is no Windows machine to judge whether deep `node_modules` paths fail | A Windows machine is available and long paths fail |
| Aligning `.npmrc`, `.kube/config` and `.ssh/config` in a project directory across the three layers | The file tools and the engine allow them while the bash pre-check blocks them; the same names in the home directory are denied or asked. Adding them to `SENSITIVE_FILE_PATTERNS` would hard-block legitimate `.npmrc` edits | A leak or a false block is reported for these files in a project directory |
| Loading `file_edit_tool` on demand | It saves about 3% of the first-turn tokens but costs a discovery call for editing tasks, and any first-turn wording change makes the first-call choice unstable | The share of edit calls rises, or first-turn token headroom runs short |
| Matching a multi-line `old_str` against a file whose only line ending is a bare CR | The edit fails with a visible `text not found` and leaves the file untouched (measured on `apply_batch_str_replace`: LF and CRLF files match, a CR-only file does not); `detect_line_ending` in `utils/line_endings.py` knows LF and CRLF only, and only classic-Mac text files are affected | A user reports a CR-only file, or `detect_line_ending` gains a third style for another reason |

## Key Dependencies

- `backends`
- `toolkits`
- `utils`

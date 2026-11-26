# dataset_export/

## Overview
Export pipeline — convert ExecutionTrace into standard fine-tuning datasets (ShareGPT / Alpaca / OpenAI JSONL) with credential masking, PII redaction (task input and output only), quality filtering, whole-sample deduplication, and incremental export by session id.

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | Re-exports DatasetExporter, ExportConfig, ExportFormat, ExportReport | ✅ |
| protocols.py | Config | ExportFormat, ExportConfig, QualityThresholds, ExportReport type definitions | ✅ |
| quality_filter.py | Core | 3-dimension quality gate (task outcome, conversation depth, content integrity) | ✅ |
| format_converter.py | Core | 3-format conversion (ShareGPT/Alpaca/OpenAI), SHA-256 dedup over the whole sample (metadata such as the session id is part of the hash, so samples of different sessions never merge), credential masking (`redact_trace_credentials`: the task input, the output and every tool call's arguments, output summary and error; always applied, since a dataset has no use for a credential and the event log keeps tool arguments as the tool saw them), PII redaction (task input and output only) | ✅ |
| exporter.py | Core | DatasetExporter async orchestrator — streams traces, writes JSONL, tracks incremental state (ids of the exported sessions) | ✅ |

## Key Dependencies

- `event_log.trace_builder` — build_trace for trace construction; `task_input` comes from the `session_start` payload (`task_input` / `query` / `message`) and `output` from the `session_end` payload (`output` / `result`). `EventLogger` writes `session_start` with an empty payload and `session_end` with `summary` only, so the sessions the agent runtime records carry neither and fail the default quality gate (`min_content_length` 50)
- `event_log.protocols` — EventLogBackend protocol
- `core.security.redact` — `redact_nested`, `redact_field_text` and `redact_sensitive_text` for credential masking
- `security.detection.pii_redactor` — redact_pii for PII masking

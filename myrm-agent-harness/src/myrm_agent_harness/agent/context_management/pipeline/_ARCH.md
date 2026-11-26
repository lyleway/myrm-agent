# pipeline/

## Overview
Pipeline module.

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | Pipeline module. | — |
| base.py | Core | Pipeline processor base class. Defines the processor interface (BaseProcessor) and the context data structure (ProcessorContext), whose `tokens_saved_by` records what each processor saved on its own. | ✅ |
| engine.py | Core | Pipeline engine. Serializes chat-scoped context mutations with the session lock, then runs processors sequentially with per-processor failure isolation, crediting each processor's savings to its own name in `tokens_saved_by`. | ✅ |

| Submodule | Description |
|-----------|-------------|
| processors/ | Pipeline processors for filtering, active per-step tool-result pruning, cache-TTL pruning, mark-driven selective eviction, pre-compaction recall, compression, session notes, summarization, normalization, and explicit cache-control markers. |

## Key Dependencies

- `utils`
- `agent/context_management/infra/session_lock.py`

# tests/eval/

## Overview

Unit tests for `myrm_agent_harness.eval` — assertion engines (sandbox/file/command/json, state, semantic), task-native suite judge + reward/JUnit payload parsing, runner, loader, builder, and reporters.

## File Index

| File | Covers |
|------|--------|
| `conftest.py` | shared fixtures (executor bound to temp workspace) |
| `test_assertions.py` | sandbox/file/command/json assertion engine |
| `test_decontam.py` | evaluation decontamination (HuggingFace domain/query-marker filtering, answer normalization) |
| `test_matrix.py` | cross-profile matrix evaluation (MatrixRunner/MatrixResult) |
| `test_builder.py` | trajectory extraction & skill eval case generation |
| `test_trajectory_disclosure.py` | trajectory-disclosure limits / blocked counts / tool details through protocol, matrix cells and reporters |
| `test_runner.py` | EvalRunner single-turn execution, concurrency and edge cases (empty list, concurrency clamp, session failure, callback errors, abort before start, sandbox + state assertions); a failed case is logged with its stack, not printed |
| `test_runner_multi_turn.py` | EvalRunner multi-turn sessions: shared session, `on_turn_fail` strategies, error abort, concurrent sessions, abort |
| `test_runner_result.py` | EvalRunner result: `to_dict` / `to_json` / `summary` / `pass_rate`, the dict schema and the recorded timings |
| `test_loader.py` | JSON case loading |
| `test_reporters.py` | JSONL and Markdown reporters |
| `test_protocol.py` | EvalCase / AgentExecutor protocol types |
| `test_reward_payload.py` | reward/JUnit payload parsing unit tests |
| `test_suite_judge.py` | task-native test suite judging integration tests |
| `test_state_assertions.py` | state assertion engine (evaluate_state_assertions) |
| `test_semantic_assertions.py` | semantic (LLM-as-a-judge) assertion engine |
| `test_semantic_exact_match.py` | semantic assertion exact-match deterministic short-circuit pre-pass |

## Key Dependencies

- `myrm_agent_harness.eval`
- `tests/support/eval_mock_executor.py` (the executor double the three runner test files share)

---
name: Experiment 2b — Structured Test Feedback (Orchestrator)
description: "Explicit negative-feedback controller enforced via subagent orchestration"
---

## Control evolution
Rather than relying on a prompt alone, the feedback loop is encoded in the
interaction between an orchestrator primary agent and specialised subagents:

1. Orchestrator invokes `@exp3-coder` to implement a change
2. Orchestrator invokes `@exp3-tester` to run relevant tests
3. Orchestrator reads the test output; if failures exist, invokes `@exp3-fixer`
4. Loop back to step 2 until tests pass, then proceed to next unit

Each subagent has narrow permissions — `exp3-tester` cannot edit files,
`exp3-coder` cannot run arbitrary bash — enforcing the separation of concerns
at the tool level, not just the prompt level.

## Mechanism
Multiple agents defined in `opencode_config.agent`:
- `exp3-orchestrator`: primary agent, manages the loop via `task` tool
- `exp3-coder`: subagent, write/edit permissions only
- `exp3-tester`: subagent, read + test-run permissions only
- `exp3-fixer`: subagent, write/edit permissions for repair

## What is tested
- Can the orchestrator reliably delegate and read subagent results?
- Does tool-level permission separation prevent skipping steps?
- Does this outperform the prompt-only approach?

## Running
```
pier run \
  --config pier-configs/opencode-deepseek-exp2-orchestrator.yaml \
  --path deep-swe/tasks/adaptix-name-mapping-aliases \
  --env docker --yes
```

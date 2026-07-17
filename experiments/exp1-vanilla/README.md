---
name: Experiment 1 — Vanilla Agent
description: "Baseline: agent operates with default behaviour, no feedback loop enforced"
---

## Control evolution
Implicit feedback controller — the agent may test, analyse, and revise at its own
discretion. No particular workflow is prescribed.

## What is tested
Whether the underlying coding agent (OpenCode with deepseek-v4-flash-free) can
solve the task using only its built-in tool-use loop without external structure.

## Metrics
- Accuracy: verifier pass/fail on alias integration tests (44 nodes)
- Time: total wall-clock from trial start to finish
- Tokens: total input/output tokens and cost

## Running
```
pier run \
  --config pier-configs/opencode-deepseek.yaml \
  --path deep-swe/tasks/adaptix-name-mapping-aliases \
  --env docker --yes
```

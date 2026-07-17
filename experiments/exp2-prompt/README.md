---
name: Experiment 2a — Structured Test Feedback (Prompt)
description: "Explicit negative-feedback controller enforced via agent system prompt"
---

## Control evolution
The agent's system prompt mandates a rigid implement → test → observe → repair → repeat
cycle. Every change must be immediately tested; test failures become mandatory error
signals that must be addressed before proceeding.

## Mechanism
A custom OpenCode primary agent (`structured-dev`) defined in the Pier config's
`opencode_config.agent` section, with a detailed prompt describing the feedback loop.

## What is tested
- Does a sufficiently detailed system prompt cause the model to respect the cycle?
- Does this improve accuracy / reduce iterations vs vanilla?
- Token cost difference vs vanilla

## Running
```
pier run \
  --config pier-configs/opencode-deepseek-exp2-prompt.yaml \
  --path deep-swe/tasks/adaptix-name-mapping-aliases \
  --env docker --yes
```

# Cybernetic Loop Engineering Experiment

This project tests whether **control loop architecture** around an AI coding agent
matters more than the model itself. The model (deepseek-v4-flash-free) stays
constant; only the controller changes.

See [`EXPERIMENT`](./EXPERIMENT) for the full experimental philosophy.

## Prerequisites

- [Pier](https://github.com/anomalyco/datacurve-pier) — `pip install datacurve-pier`
  or `uv tool install datacurve-pier`
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) with Rosetta 2
  enabled on Apple Silicon
- An OpenCode API key in `~/.config/opencode/opencode.json` or
  `OPENCODE_ACCESS_TOKEN`

## The Three Control Approaches

| Exp | Config | Description | How the loop is enforced |
|---|---|---|---|
| **Exp1** — Vanilla | `opencode-deepseek.yaml` | Default OpenCode agent, no custom prompt. Baseline. | Nothing — agent decides when to test |
| **Exp2a** — Structured prompt | `opencode-deepseek-exp2-prompt.yaml` | System prompt telling the agent to implement → test → fix → repeat | **Prompt** — agent is asked politely; can still ignore |
| **Exp2b** — Orchestrator | `opencode-deepseek-exp2-orchestrator.yaml` | Role-split agents: coder (edit-only), tester (test-only), fixer (edit+test), orchestrator (delegates) | **Permissions** — coder can't run tests, tester can't edit, orchestrator can't do either |

Each has a `-dummy` variant (e.g. `opencode-deepseek-dummy.yaml`) that uses the
smaller dummy task for rapid iteration.

## Task Types

### Real DeepSWE Tasks

Located in `deep-swe/tasks/`. Each task has:
- A Docker agent environment with `instruction.md`, code snapshot, p2p tests
- A separate verifier image with hidden f2p tests
- Agents see p2p tests but NOT f2p tests (separate-verifier mode)

Run on any single task:
```bash
pier run --config pier-configs/opencode-deepseek.yaml \
  --path deep-swe/tasks/adaptix-name-mapping-aliases \
  --n-attempts 2 --yes
```

### Dummy Task

`deep-swe/tasks/dummy-adaptix-alias/` — a small 3-file package with 9 f2p tests
and 7 p2p tests. Takes ~2-5 minutes per run. Use this for quick iteration on
control loop changes:

```bash
pier run --config pier-configs/opencode-deepseek-dummy.yaml \
  --path deep-swe/tasks/dummy-adaptix-alias \
  --n-attempts 1 --yes
```

The dummy task also has separate-verifier mode: p2p tests are agent-accessible,
f2p tests run in a separate verifier container that the agent never sees.

## Quick Start: Run and Record Results

```bash
# Run Exp1 (vanilla) on the dummy task and save results
./experiments/run-task.sh opencode-deepseek-dummy \
  deep-swe/tasks/dummy-adaptix-alias 1

# Run Exp2a (structured prompt) on the same task
./experiments/run-task.sh opencode-deepseek-exp2-prompt-dummy \
  deep-swe/tasks/dummy-adaptix-alias 1

# Run Exp2b (orchestrator) on the same task
./experiments/run-task.sh opencode-deepseek-exp2-orchestrator-dummy \
  deep-swe/tasks/dummy-adaptix-alias 1
```

Each run:
1. Builds Docker images, runs the agent, runs the verifier
2. Outputs to `jobs/YYYY-MM-DD__HH-MM-SS/`
3. Appends results to `experiments/results.json`

## Viewing Results

```bash
# Check scores from the most recent job
cat jobs/*/result.json | jq '.stats.evals'

# View all historical results
cat experiments/results.json | jq '.[] | {experiment, f2p_ratio, p2p_ratio, input_tokens, exception}'
```

Each result entry records:
- `f2p_passed / f2p_total` — hidden test pass rate (the benchmark metric)
- `p2p_passed / p2p_total` — pre-existing regression test pass rate
- `input_tokens / output_tokens` — resource usage
- `reward` / `partial` — Pier reward signals
- `exception` — if the agent crashed

## Experimenting: The Development Cycle

1. **Modify a config** in `pier-configs/` or create a new one
2. **Run on the dummy task** for fast feedback
3. **Check results** in `jobs/` and `experiments/results.json`
4. If the change works, **run on a real task** to validate

### Adding a New Control Approach

Copy an existing config and change the `agent` / `opencode_config` section:

```yaml
# pier-configs/my-new-approach.yaml
agents:
  - name: opencode
    model_name: opencode/deepseek-v4-flash-free
    kwargs:
      agent: my-agent-name
      opencode_config:
        agent:
          my-agent-name:
            mode: primary
            prompt: |
              Your custom control instructions here...
          # Subagents go here if using orchestrator mode
```

Then run it:
```bash
./experiments/run-task.sh my-new-approach deep-swe/tasks/dummy-adaptix-alias 1
```

## Output Structure

```
jobs/
└── YYYY-MM-DD__HH-MM-SS/              # Job run
    ├── config.json                     # Resolved Pier config
    ├── result.json                     # Aggregate results across trials
    ├── job.log                         # Job-level build/debug log
    └── <task-name>__<trial-id>/        # Single trial
        ├── result.json                 # Trial results (f2p, p2p, reward)
        ├── trial.log                   # Trial execution log
        ├── agent/
        │   ├── opencode.txt            # Raw OpenCode event stream (JSON lines)
        │   ├── trajectory.json         # Step-by-step tool call history
        │   └── setup/                  # Agent environment setup
        └── verifier/                   # Hidden test runner output
```

To inspect a trial's trajectory for debugging:
```bash
cat jobs/<job>/<trial>/agent/trajectory.json | jq '.[] | {step, tool, input, output}'
```

## Config Reference

| Config file | Experiment | Mode | Notes |
|---|---|---|---|
| `opencode-deepseek.yaml` | Exp1 | Vanilla | Baseline, no custom prompt |
| `opencode-deepseek-dummy.yaml` | Exp1-dummy | Vanilla | Same but for dummy task |
| `opencode-deepseek-exp2-prompt.yaml` | Exp2a | Structured prompt | Prompt-only enforcement |
| `opencode-deepseek-exp2-prompt-dummy.yaml` | Exp2a-dummy | Structured prompt | Same for dummy task |
| `opencode-deepseek-exp2-orchestrator.yaml` | Exp2b | Orchestrator | Subagent role split |
| `opencode-deepseek-exp2-orchestrator-dummy.yaml` | Exp2b-dummy | Orchestrator | Same for dummy task |
| `opencode-deepseek-exp2.yaml` | — | — | Deprecated (use exp2-prompt) |

Real task configs allocate 4 CPUs / 16 GB RAM / 7200s timeout per trial.
Dummy configs use 1 CPU / 512 MB / 1200s.

## Troubleshooting

- **Docker Desktop on Apple Silicon**: Enable Rosetta 2 emulation (Settings →
  General → "Use Rosetta for x86/amd64 emulation on Apple Silicon")
- **Rate limits**: The free tier (deepseek-v4-flash-free) has aggressive rate
  limits. Wait between runs or use a paid OpenCode model.
- **Computer sleep pauses Docker**: Keep the machine awake during long runs.
- **Puzzling agent behavior**: Check `agent/trajectory.json` for the full
  step-by-step tool call history.

# Cybernetic Loop Engineering Experiment

This project tests whether **control loop architecture** around an AI coding agent
matters more than the model itself. The model (deepseek-v4-flash-free) stays
constant; only the controller changes.

See [`EXPERIMENT`](./EXPERIMENT) for the original experimental philosophy, and
[`docs/cybernetics-framing.rtf`](./docs/cybernetics-framing.rtf) for the full
cybernetic framing.

## Cybenetic Framing

We model autonomous software engineering through the lens of cybernetics.
The software repository and its execution environment form the **plant**, whose
state consists of source code, dependencies, documentation, and runtime behaviour.
The LLM acts as the **controller**, selecting actions (edit code, run tools,
terminate). The controller never directly observes the plant's true state —
it receives **observations** through repository files, test output, logs, and
other artefacts, and constructs an internal estimate before acting.

Loop engineering is therefore the engineering of the **observation and feedback
architecture** surrounding the controller. Rather than changing the model, we
modify what information it is required to observe, when those observations occur,
and how they influence subsequent decisions. The 7 experiments progressively
enrich this architecture:

| Exp | Cybernetic principle | Newly enforced observation |
|-----|---------------------|---------------------------|
| 1 | Baseline | None — default agent behaviour |
| 2 | Negative feedback | Execution and test results after every implementation cycle |
| 3 | Good Regulator Theorem | Repository analysis and planning before implementation |
| 4 | High-frequency observation | Verification after every implementation step |
| 5 | Persistent state estimation | Recording and reuse of previous observations |
| 6 | Independent observation | Reviewer-generated observations from a second agent |
| 7 | Hierarchical control | Whole-system observations before completion |

The controller (LLM) remains fixed throughout. The study investigates how
progressively enriching the observation architecture influences autonomous
software engineering performance.

**The experiments are cumulative.** Each experiment builds on the mechanisms
introduced previously. For example, step-level verification (Exp 4) depends on
having a structured plan (Exp 3) that defines what the steps are — you cannot
verify individual steps without first having a decomposed task. Similarly,
independent review (Exp 6) evaluates a plan-driven, step-verified implementation
(Exps 3-5), not an ad-hoc one.

## Prerequisites

- [Pier](https://github.com/anomalyco/datacurve-pier) — `pip install datacurve-pier`
  or `uv tool install datacurve-pier`
- A compute backend: either **Modal.com** (cloud, recommended) or **Docker Desktop**
  (local)
- An OpenCode API key in `~/.config/opencode/opencode.json` or
  `OPENCODE_ACCESS_TOKEN`

### Compute Backend

Pier supports two execution backends:

- **Modal** (this project's primary backend): Runs trials on Modal's cloud
  infrastructure. Configure via `pier login modal` and set `type: modal` in
  the environment config. No local Docker required.

- **Docker** (local fallback): Runs trials on your local machine via Docker
  Desktop. On Apple Silicon, enable Rosetta 2 (Settings → General → "Use
  Rosetta for x86/amd64 emulation on Apple Silicon"). Set `type: docker` in
  the environment config.

The `-dummy` configs default to Docker for fast local iteration. Real task
configs can target either backend.

## Approaches Tested

| Exp | Config | Description | How the loop is enforced | Result |
|---|---|---|---|---|
| **Exp1** — Vanilla | `opencode-deepseek.yaml` | Default agent, no custom prompt. Baseline. | Nothing — agent decides when to test | Baseline |
| **Exp2a** — Structured prompt | `opencode-deepseek-exp2-prompt.yaml` | System prompt: implement → test → fix → repeat | **Prompt** — agent is asked politely; can still ignore | ❌ Agent ignored the loop entirely |
| **Exp2b** — Orchestrator | `opencode-deepseek-exp2-orchestrator.yaml` | Role-split agents: coder (edit-only), tester (test-only), fixer (edit+test), orchestrator (delegates) | **Permissions** — coder can't run tests, tester can't edit | ✅ Delegation enforced |
| **Exp3** — Docs-first prompt | `opencode-deepseek-exp3-docs-dummy.yaml` | Prompt tells agent to document → plan → implement in phases | **Prompt** — same as Exp2a but with doc-writing steps | ❌ Agent skipped docs entirely |
| **Exp3b** — Two-phase (research → implement) | `...-research-dummy.yaml` + `...-implement-dummy.yaml` | Phase 1: research agent (can only write `docs/`). Phase 2: implementation agent with docs pre-populated. | **Permissions** — research agent has `edit: deny` on source, read-only bash; literally cannot write code | ✅ Research agent forced to produce docs |

Each has a `-dummy` variant that uses the smaller dummy task for rapid iteration.

## Key Learnings

### Permission-based enforcement > Prompt-based enforcement

Exp2a and Exp3 both used structured prompts that the agent **ignored entirely**.
The agent went straight to implementation as if the prompt wasn't there.

Exp2b and Exp3b used **open code permissions** (editing paths, bash command
allowlists) to structurally prevent the agent from doing the wrong thing:

- `edit: {"*": "deny", "docs/*": "allow"}` — agent physically cannot edit source files
- `bash: {"*": "deny", "git *": "allow", "ls *": "allow", ...}` — agent cannot run tests or write files via shell
- `task: deny` — agent cannot spawn subagents

The edit permission patterns match against **relative paths** from the worktree
(e.g. `/app/docs/plan.md` → `docs/plan.md`), so `"docs/*"` correctly allows only
the docs directory.

### The Two-Phase Pattern (Exp3b)

Exp3b enforces a research-first workflow by running two sequential Pier trials:

1. **Phase 1 — Research**: An agent with severely restricted permissions
   (no source edits, no test execution, no subagents). It can only read the
   codebase and write files to `docs/`. It commits the docs so they appear
   in `model.patch`.

2. **Phase 2 — Implementation**: A standard (vanilla) agent that starts with
   the docs pre-populated in its Docker image. The docs are part of the
   initial git commit, so the agent sees them as part of the task.

The wrapper script (`experiments/run-exp3b.py`):
- Creates a temp copy of the task
- Modifies `pre_artifacts.sh` to auto-commit uncommitted changes
- Runs Phase 1, extracts docs from `model.patch`
- Places docs in the task's `environment/` directory (so they're included
  when Pier copies `environment/` to the Docker build context)
- Modifies the Dockerfile to `COPY docs/ /app/docs/`
- Runs Phase 2 on the modified task copy

### Costs on the Dummy Task (9 F2P, 7 P2P)

| Experiment | Input tokens | Output tokens | Score | Notes |
|---|---|---|---|---|
| Exp1 — vanilla | 181K | 3.5K | 9/9 | Baseline |
| Exp2a — structured prompt | 314K | 5.2K | 9/9 | Prompt ignored |
| Exp2b — orchestrator | 568K | — | 9/9 | Most expensive; delegation overhead |
| Exp3 — docs prompt | 363K | 5.3K | 9/9 | Prompt ignored |
| Exp3b Phase 1 — research | 222K | 6.3K | — | Produced 335 lines of docs |
| Exp3b Phase 2 — impl w/ docs | 245K | 3.6K | 9/9 | Docs pre-populated |
| Exp3b combined | 467K | 9.9K | 9/9 | Two trials vs one |

On the dummy task all approaches score 9/9 (ceiling). The key differentiators
are: (1) whether the approach actually enforces its intended structure, and
(2) token cost. The real test will be on harder tasks where documentation
quality affects success.

### Research Agent Output Quality

The Exp3b research agent (Phase 1) produced genuinely useful documentation:

- **repository-analysis.md** (125 lines): Architecture diagram, data structures,
  file-by-file breakdown, 5 identified risks, two-pass resolution algorithm
- **plan.md** (210 lines): Step-by-step implementation plan with detailed code
  snippets, affected files, and verification methods

This is in contrast to Exp3 where the agent was asked to write docs but
refused to do so — the structural enforcement (can't edit source, can't run
tests) left the agent with no other way to make progress.

### Build Context Quirk

The task's `environment/` directory uses a **symlink** `src -> ../src` to
include the source code. Pier copies `environment/` to the Docker build
context via `shutil.copytree(symlinks=False)`, which follows the symlink
and materializes the source files. Any files that an experiment needs in
the build context must be placed **inside** `environment/`, not at the task
root.

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

`deep-swe/tasks/dummy-adaptix-alias/` — a small adaptix package with 9 f2p tests
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

### Single Task

```bash
# Run vanilla on one task
./experiments/run-task.sh opencode-deepseek \
  deep-swe/tasks/adaptix-name-mapping-aliases 2

# Run on the dummy task (fast, for iteration)
./experiments/run-task.sh opencode-deepseek-dummy \
  deep-swe/tasks/dummy-adaptix-alias 1

# Run a specific experiment on the dummy task
./experiments/run-task.sh opencode-deepseek-exp2-prompt-dummy \
  deep-swe/tasks/dummy-adaptix-alias 1
./experiments/run-task.sh opencode-deepseek-exp2-orchestrator-dummy \
  deep-swe/tasks/dummy-adaptix-alias 1

# Run Exp3b (two-phase research → implement)
python3 experiments/run-exp3b.py
```

### Batch: Multiple Tasks in One Command

Use a config with `datasets` to run the same agent on multiple tasks
sequentially:

```bash
# Vanilla on 3 tasks (dummy + 2 real)
pier run --config pier-configs/opencode-deepseek-batch.yaml --yes
```

The batch config (`pier-configs/opencode-deepseek-batch.yaml`) specifies
the task directory and task names under `datasets`, along with full resource
allocations (4 CPUs, 16 GB, 7200s timeout, 2 attempts). Tasks run one at a
time (`n_concurrent_trials: 1`).

To create your own batch run, copy the batch config and change `task_names`:

```yaml
datasets:
  - path: /Users/valdimareggertsson/Documents/Default Project/deep-swe/tasks
    task_names:
      - adaptix-name-mapping-aliases
      - adaptix-alias-simple
      - dummy-adaptix-alias
```

### Shell Loop (Separate Jobs Per Task)

If you want individual job directories per task (easier to inspect):

```bash
for task in adaptix-name-mapping-aliases adaptix-alias-simple dummy-adaptix-alias; do
  ./experiments/run-task.sh opencode-deepseek "deep-swe/tasks/$task" 2
done
```

Each single-phase run:
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

### Adding a New Agent Configuration

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
            permission:
              edit:
                "*": "deny"
                "docs/*": "allow"
              bash:
                "*": "deny"
                "git *": "allow"
                "ls *": "allow"
                "grep *": "allow"
                "mkdir *": "allow"
                "pwd": "allow"
            prompt: |
              Your custom instructions here...
```

Then run it:
```bash
./experiments/run-task.sh my-new-approach deep-swe/tasks/dummy-adaptix-alias 1
```

### Adding a Two-Phase Experiment

For a research → implement workflow, create two configs and a wrapper:

1. **Research config**: Restricted permissions (source edits denied, bash read-only)
2. **Implement config**: Standard agent
3. **Wrapper script**: Runs Phase 1, extracts docs from `model.patch`, places them
   in the task's `environment/docs/`, modifies Dockerfile to `COPY docs/`, runs Phase 2

Use `experiments/run-exp3b.py` as a template.

### Testing Permission Configurations

To test if a permission block works correctly:
1. Run the config on the dummy task
2. Check `agent/trajectory.json` to see which tools were used
3. For research agents: verify no edit/write calls to source files
4. For tester agents: verify no edit calls at all
5. Use `n_attempts: 1` for quick feedback

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
        ├── exception.txt               # Stack trace if trial crashed
        ├── agent/
        │   ├── opencode.txt            # Raw OpenCode event stream (JSON lines)
        │   ├── trajectory.json         # Step-by-step tool call history
        │   └── setup/                  # Agent environment setup
        ├── artifacts/
        │   └── model.patch             # Git diff of agent's changes
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
| `opencode-deepseek-exp3-docs-dummy.yaml` | Exp3 | Docs-first prompt | Prompt-only; agent ignored |
| `opencode-deepseek-exp3b-research-dummy.yaml` | Exp3b Phase 1 | Research (perm-locked) | Can only write `docs/` |
| `opencode-deepseek-exp3b-implement-dummy.yaml` | Exp3b Phase 2 | Vanilla implementation | Docs pre-populated in image |
| `opencode-deepseek-batch.yaml` | — | Batch vanilla | Runs vanilla on 3 tasks sequentially |
| `opencode-deepseek-exp2.yaml` | — | — | Deprecated (use exp2-prompt) |

Real task configs allocate 4 CPUs / 16 GB RAM / 7200s timeout per trial.
Dummy configs use 1 CPU / 512 MB / 600s.

## Troubleshooting

- **Modal credentials**: Run `pier login modal` before using Modal-backed configs.
- **Docker Desktop on Apple Silicon**: If using local Docker, enable Rosetta 2
  emulation (Settings → General → "Use Rosetta for x86/amd64 emulation on
  Apple Silicon")
- **Rate limits**: The free tier (deepseek-v4-flash-free) has aggressive rate
  limits. Wait between runs or use a paid OpenCode model.
- **Computer sleep pauses Docker**: If using local Docker, keep the machine
  awake during long runs.
- **Puzzling agent behavior**: Check `agent/trajectory.json` for the full
  step-by-step tool call history.
- **"docs/ not found" during Docker build**: Files in the task root don't
  automatically appear in the build context. Place them inside `environment/`
  or use the `environment/src -> ../src` symlink pattern.
- **save_results.py crashes**: The script expects `verifier_result` to be a
  dict, but it's `null` when the verifier is disabled. Recent versions handle
  this gracefully.

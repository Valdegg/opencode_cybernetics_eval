# Cybernetic Loop Engineering Experiment

This project tests how the **control and observation architecture** surrounding
an AI coding agent affects performance. The underlying language model
(deepseek-v4-flash-free) stays constant; only the imposed orchestration,
observation, memory, and feedback mechanisms change.

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
and how they influence subsequent decisions. We test three tiers of control loop:

| Tier | Name | Loop script |
|---|---|---|
| **A** | Preparation | `explore → document → plan` then implement |
| **B** | Task Decomposition | A + per-step `implement → verify → review → persist → repair` loop |
| **C** | System Convergence | B + whole-system `full_suite → review → repair` convergence loop |

The base LLM remains fixed throughout. The study investigates how
progressively enriching the observation architecture influences autonomous
software engineering performance.

Each tier enforces a longer script around the same model (`//` = outer orchestrator, `#` = inner agent behaviour within a single Pier run):

```python
# ─────────────────────────────────────────────
# Tier A: Preparation (Research + Planning)
# ─────────────────────────────────────────────

// 1. Run planning agent (permission-locked to docs/, verifier disabled)
explore(task)
document(repository-analysis.md)            # human-readable architecture analysis

// 2. Planning agent writes a **machine-parseable plan** with explicit
//    success criteria per step and a verification array that distinguishes
//    existing tests from tests that must be created (plan.json — parsed
//    by the orchestrator)
plan = create_plan(analysis) -> plan.json   # each step has: objective, files,
                                            #   success_criteria[],
                                            #   verification[] {type, command, reason},
                                            #   tests_to_create[]

// 3. Run implement agent (docs baked into Docker image, prompted to read them)
implement(plan)


# ─────────────────────────────────────────────
# Tier B: Task Decomposition Loop
# ─────────────────────────────────────────────

// Phase 0 — same as Tier A step 1+2: planner produces plan.json
plan = run_planning_agent(task) -> plan.json

// Phase 1-N — per-step loop, orchestrated by run-tierB.py
for step in plan:                          # each step parsed from plan.json

  # --- Inner repair loop (same step, retry on failure) ---
  repeat at most N times:

    # Inject step context into the task directory
    write(current-step.json)               # step.id, .objective, .files,
                                           #   .success_criteria[],
                                           #   .verification[] {type, command, reason},
                                           #   .tests_to_create[]

    write(repair-feedback.json)            # only on retry: why the last attempt failed

    // Generate a **step-specific verifier** — processes each entry in
    // step.verification[]. For each entry:
    //   - existing_test: runs the command; may also run pre-patch for p2p regression
    //   - new_test:      verifies the test function now exists (agent created it),
    //                    then runs the command post-patch
    //   - typecheck/build/execution: runs the command as-is
    inject_step_verifier(step.verification)

    // The orchestrator also checks tests_to_create: it parses the agent's
    // model.patch and confirms each listed test function was actually written.
    verify_tests_created(step.tests_to_create, model.patch)

    // Run one Pier trial: implement agent + step-specific verifier
    run implement(step)                    # agent reads current-step.json
    results = parse_verifier(reward.json)  # did verification[] pass?

    if results.passed:                     # all step-specific tests green
      break                                # exit repair loop, move to review
    else:
      write(repair-feedback.json, results.failures)
      # loop back for another attempt (up to N)

  # --- Independent review (separate Pier run, read-only agent) ---
  // Creates a fresh copy of the task dir, applies the implement patch
  // so the reviewer sees the code post-changes, then runs the reviewer
  // agent (permission: read-only bash + docs/ write for review.json)
  apply_patch(accumulated_patch)
  run review(step)                         # evaluates success_criteria[],
                                           #   examines git diff, reads code
  review = parse(review.json)              # {approved, requires_rework,
                                           #   feedback, plan_updates}

  if not review.approved:
    update_plan(plan.json, review.plan_updates)
    if review.requires_rework:
      # re-enter the repair loop for this same step
      write(repair-feedback.json, review.feedback)
      continue(repeat)

  # --- Persist step learnings ---
  append(docs/learnings.md, step.id, results, review)


# ─────────────────────────────────────────────
# Tier C: System Convergence Loop
# ─────────────────────────────────────────────

// Tier A + Tier B executed above — all steps implemented

// Final convergence: apply all accumulated patches, run the FULL test suite,
// then independent system-level review. Loop until both pass.
repeat until all_tests_pass and review.approved:

  run_all_tests()                          # full suite — including held-out tests
  if any test fails:
    repair(failure_details)
    continue

  review = independent_review(system)      # agent evaluates the integrated system
  if not review.approved:
    repair(review.feedback)
    continue

  persist(learnings)
```

Key invariants:
- **Success criteria are generated during planning** (Phase 0), not during implementation. The planner agent writes `plan.json` with explicit `success_criteria[]` per step. These criteria define the **desired state** — what must be true after the step is implemented.
- **Verification is separated from test creation** — the plan's `verification[]` array explicitly declares how each criterion is observed. Each entry has a `type`:
  - `existing_test` — test already exists; run it pre- and post-patch for regression
  - `new_test` — test does not exist yet; agent must create it. The test function name MUST appear in `tests_to_create[]`, and the orchestrator verifies it was written.
  - `typecheck` / `build` / `execution` — non-test observation mechanisms
- The `tests_to_create[]` array documents what new test instrumentation the implement agent must write. The orchestrator parses the agent's patch to confirm each listed function was actually created.
- **Review is independent** — a separate agent (no code modification access, no test execution) evaluates the step's output against its success criteria and may propose plan updates for remaining steps.

### Historical note

These three tiers evolved from a 7-experiment progression that isolated each
mechanism individually. An earlier version of this document (and the archived
RTF) describes Exp 1-7 separately. The A/B/C framing collapses them into three
testable loop configurations:

| Early experiments | Feeds into |
|---|---|
| Exp 1 (vanilla), Exp 2 (feedback), Exp 2b (orchestrator) | Baseline — no loop |
| Exp 3 (state estimation) | Tier A — preparation |
| Exp 4 (step verification), Exp 5 (persistence), Exp 6 (independent review) | Tier B — task decomposition |
| Exp 7 (system convergence) | Tier C — system convergence |

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

| Tier | Config | Description | How the loop is enforced | Result |
|---|---|---|---|---|
| **A** | `opencode-deepseek-tierA-research-dummy.yaml` + `...tierA-implement-dummy.yaml` | Two-phase: research agent locked to `docs/` → implement agent prompted to read and follow docs | **Permissions** — research agent locked to `docs/` only; **Prompt** — Phase 2 instructed to read `docs/` and follow plan | ✅ Research agent forced to produce docs; Phase 2 follows plan |
| **B** | `opencode-deepseek-tierB-plan-dummy.yaml` + `...tierB-implement-dummy.yaml` + `...tierB-review-dummy.yaml` | Phase 0: planning agent produces `plan.json`. Per-step: implement → verify step-specific tests → review (agent critic) → repair loop → persist | **Permissions** — planner locked to `docs/`; reviewer read-only; **Script** — `run-tierB.py` orchestrates loop | ✅ Planner produces machine-parseable plan; step verifier runs only step's tests; reviewer evaluates output |
| **C** | (planned) | B + whole-system test + review convergence loop | Permissions + script | TBD |

### Earlier baselines (Exp 1–3b)

These earlier configurations informed the A/B/C design. See the historical note at the top of this document.

| Early experiment | What it tested | Key finding |
|---|---|---|
| **Exp1** Vanilla | Default agent, no custom prompt | Baseline (181K in, 9/9 F2P) |
| **Exp2a** Structured prompt | Implement → test → fix in system prompt | ❌ Agent ignored the loop — prompt alone is insufficient |
| **Exp2b** Orchestrator | Role-split agents with permission enforcement | ✅ Permissions structurally enforce delegation |
| **Exp3** Docs-first prompt | Prompt says "document before coding" | ❌ Agent skipped docs entirely |
| **Exp3b** Two-phase research→implement | Research agent locked to `docs/`, then implement | ✅ Permission enforcement works. 335 lines of docs produced. |

Each `-dummy` config uses a smaller dummy task for rapid iteration.

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

### Adding a Two-Phase Experiment (Tier A)

For a research → implement workflow, create two configs and a wrapper:

### Adding a Per-Step Loop (Tier B)

Tier B uses three configs and an orchestrator script:

```
run-tierB.py
├── Phase 0: Planning agent (perm-locked, docs/ only)
│   └── Reads codebase → writes docs/repository-analysis.md + docs/plan.json
├── For each step in plan.json:
│   ├── Implement loop (up to N repair attempts)
│   │   ├── Inject current-step.json into environment/docs/
│   │   ├── Inject step-specific test runner (only step's tests)
│   │   ├── Run implement agent + Pier verifier
│   │   └── If step-specific tests fail → write repair-feedback.json → retry
│   ├── Review
│   │   ├── Apply implement patch to a fresh copy of the code
│   │   ├── Run reviewer agent (read-only, docs/ only)
│   │   └── Parse review.json → if not approved, can trigger rework or plan update
│   └── Persist learnings
└── Summary output
```

The step-specific test runner is generated per-step — it runs only the tests listed in `plan.json` for that step, producing `reward.json` scoped to just that step.

Run: `python3 experiments/run-tierB.py`

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

| Config file | Tier / Exp | Mode | Notes |
|---|---|---|---|
| `opencode-deepseek.yaml` | Exp1 | Vanilla | Baseline, no custom prompt |
| `opencode-deepseek-dummy.yaml` | Exp1-dummy | Vanilla | Same but for dummy task |
| `opencode-deepseek-exp2-prompt.yaml` | Exp2a | Structured prompt | Prompt-only enforcement |
| `opencode-deepseek-exp2-prompt-dummy.yaml` | Exp2a-dummy | Structured prompt | Same for dummy task |
| `opencode-deepseek-exp2-orchestrator.yaml` | Exp2b | Orchestrator | Subagent role split |
| `opencode-deepseek-exp2-orchestrator-dummy.yaml` | Exp2b-dummy | Orchestrator | Same for dummy task |
| `opencode-deepseek-exp3-docs-dummy.yaml` | Exp3 | Docs-first prompt | Prompt-only; agent ignored |
| `opencode-deepseek-exp3b-research-dummy.yaml` | **Tier A** Phase 1 | Research (perm-locked) | Can only write `docs/` |
| `opencode-deepseek-tierB-plan-dummy.yaml` | **Tier B** Phase 0 | Planning (perm-locked) | Produces `plan.json` + `repository-analysis.md` |
| `opencode-deepseek-tierB-implement-dummy.yaml` | **Tier B** per-step | Step implementation | Prompt reads `current-step.json`; verifier enabled |
| `opencode-deepseek-tierB-review-dummy.yaml` | **Tier B** per-step | Step review (read-only) | Prompt evaluates step vs criteria; outputs `review.json` |
| `opencode-deepseek-tierA-research-dummy.yaml` | **Tier A** Phase 1 | Research + planning (perm-locked) | Produces `plan.json` with per-step success criteria |
| `opencode-deepseek-tierA-implement-dummy.yaml` | **Tier A** Phase 2 | Docs-guided implementation | Prompt tells agent to read docs/ and follow plan |
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

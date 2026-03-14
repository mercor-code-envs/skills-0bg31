# Skills -- Expert Task Guide

Each task is a software engineering problem that frontier AI models **fail without a custom skill** but can solve once given one. Your job is to write that skill.

---

## 1. Purpose

This repository contains Skills tasks designed to measure whether a model can select and use the correct reasoning skill to solve a software-engineering problem.

Each task should:
- Fail for baseline model runs without skills.
- Pass when the correct golden skills are available and used.
- Remain deterministic and reproducible in the Harbor execution environment.

## 2. Canonical Task Package

Each task lives in its own folder (kebab-case), for example:
- `consistent-hash-ring`
- `jwt-claims-validator`
- `aho-corasick-pattern-search`
- `lz77-sliding-window-codec`

In this example repository, each task is at the repository root (`<task-slug>/`).
In other delivery repos, tasks may be nested under `tasks/<task-slug>/`.

Expected layout:

```text
<task-root>/<task-slug>/
├── Dockerfile
├── setup.sh
├── input_files/
├── instruction.md
├── metadata.json
├── tests/
│   └── test.py
├── solution/
│   └── solve.sh
└── skills/
    ├── <golden-skill-1>/
    │   ├── SKILL.md
    │   └── scripts/ (required; unit tests are a QC expectation)
    ├── <golden-skill-2>/
    └── <distractor-skill-N>/
```

## 3. What You Receive vs. What You Deliver

When you claim a task and download it from S3, the task directory arrives with most files already written. Your job is to add skills and fill in evaluation results.

### Pre-provided input

These files are created by the task pipeline and delivered to you:

| File | Status | What it is |
|------|--------|-----------|
| `Dockerfile` | Do not modify | Container definition based on `task-base:latest`. Builds the execution environment. |
| `setup.sh` | Do not modify | Installs apt/pip dependencies during Docker build. |
| `input_files/` | Do not modify | Source code, configs, data, and scaffolds the model sees at `/app/`. |
| `instruction.md` | Do not modify unless fixing a confirmed leak | The problem statement shown to the AI agent at inference time. See below. |
| `tests/test.py` | Do not modify | Deterministic verifier that prints `pass` or `fail`. |
| `solution/solve.sh` | Do not modify | Oracle solution that makes `test.py` print `pass`. |
| `metadata.json` | **Fill in** | Partially filled: `task_name`, `category`, `input_files`, `test_file`, `solution_file` are set. `golden_skills`, `distractor_skills` are empty arrays. `failure_modes` contains TODO placeholders. |
| `skills/.gitkeep` | Replace | Empty placeholder; you create actual skill directories here. |

**About `instruction.md`:** Do not modify unless fixing a confirmed leak. Review it before writing skills -- if the instruction already reveals information that your golden skill is supposed to teach (specific parameter values, implementation patterns, algorithm names), the skill's signal is weakened and the model may solve the task from the instruction alone. If you find a confirmed leak, you may edit the instruction to describe *what* the model must achieve without hinting at *how*. Document all changes in your PR description.

### What you produce (deliverables)

You create the skills and fill in the evaluation metadata. Everything you deliver goes inside the existing task directory:

| Deliverable | Details |
|-------------|---------|
| **2 golden skills** | Each at `skills/<golden-name>/` containing: `SKILL.md` (with valid YAML frontmatter), `scripts/<name>.py` (working reference implementation demonstrating the skill's concept), and `scripts/test_<name>.py` (unit tests verifying the helper). The `scripts/`; `test_*.py` files are a **QC expectation** (not a strict validator rule). Target the specific failure modes you observe during baseline runs. |
| **3-5 distractor skills** | Each at `skills/<distractor-name>/` with the same structure as golden skills. Thematically similar to golden skills (cosine similarity >= 0.4 to pass automated checks, >= 0.6 recommended) but must not solve the task. |
| **Updated `metadata.json`** | Fill in `golden_skills` (array of golden skill directory names), `distractor_skills` (array of distractor directory names), and `failure_modes` (replace all TODO entries with actual evaluation outcomes). |
| **Passing validations** | `validate_task.py`, `validate_skill_format.py`, and `validate_skill_similarity.py` must all pass. |
| **Harbor evaluation results** | Both agents score < 1.0 without skills and = 1.0 with the full skill set. |
| **Pull request** | PR from your branch into main of your fork, titled `Task ID: [<airtable-task-id>]`. |

### Visual summary

```text
tasks/<task-slug>/
├── Dockerfile                          ← PRE-PROVIDED (do not modify)
├── setup.sh                            ← PRE-PROVIDED (do not modify)
├── input_files/                        ← PRE-PROVIDED (do not modify)
│   └── ...
├── instruction.md                      ← PRE-PROVIDED (review for leaks; see Section 3)
├── metadata.json                       ← PARTIALLY PROVIDED (fill in skills + failure_modes)
├── tests/
│   └── test.py                         ← PRE-PROVIDED (do not modify)
├── solution/
│   └── solve.sh                        ← PRE-PROVIDED (do not modify)
└── skills/                             ← YOU CREATE EVERYTHING BELOW
    ├── <golden-skill-1>/
    │   ├── SKILL.md
    │   └── scripts/                    ← required
    │       ├── helper.py
    │       └── test_helper.py          ← QC expectation; validator does not require this file
    ├── <golden-skill-2>/
    │   ├── SKILL.md
    │   └── scripts/
    │       ├── helper.py
    │       └── test_helper.py
    ├── <distractor-1>/
    │   ├── SKILL.md
    │   └── scripts/
    │       ├── helper.py
    │       └── test_helper.py
    ├── <distractor-2>/
    │   └── ...
    └── <distractor-3>/
        └── ...
```

---

## 4. Getting Started

All tasks are assigned through Airtable. When you claim a task, a GitHub repository is automatically forked for you within ~5 minutes.

### Step 0 -- Setup GitHub CLI (required)

Follow the GitHub CLI Quickstart:
https://docs.github.com/en/github-cli/github-cli/quickstart

You **must** authenticate `gh` with an SSH key (not HTTPS token flow):

```bash
gh auth login 
gh auth status
```


### Step 1 -- Claim your task in Airtable

Open your task list in the Airtable interface and claim a task. The automation will:
- Fork `mercor-code-envs/skills-template` into `mercor-code-envs/skills-<your-id>`
- Add you as a collaborator on the fork
- Update your task status to **In Progress**

### Step 2 -- Clone, create branch, and download task files

Copy and run the three **Code** commands shown in your Airtable task record in order:

```bash
# Clone Repo
gh repo clone mercor-code-envs/skills-<your-id>
cd skills-<your-id>

# Create Branch
git checkout -b skills-<task-id>

# Download S3 (downloads your task into tasks/<task-slug>/)
python3 tooling/download_s3.py \
  --s3-url "<url from airtable>" \
  --task-name "<task name from airtable>"
```

Your task is now at `tasks/<task-slug>/`.

### Step 3 -- Install Harbor (for local evaluation)

```bash
pip install harbor
# or
uv tool install harbor
```

---

## 5. Step-by-Step Workflow

### Step 4 -- Run the task without skills (baseline)

Confirm both agents fail before you write anything:

```bash
harbor run -p tasks/<task-slug> -e modal -a terminus-2 \
    -m "gemini/gemini-3.1-pro-preview"

harbor run -p tasks/<task-slug> -e modal -a claude-code \
    -m claude-opus-4-6
```

Both should score < 1.0. Note what each agent gets wrong -- this tells you what the skill needs to teach.

### Step 5 -- Write the golden skills

Create `tasks/<task-slug>/skills/<skill-name>/` with three things: the SKILL.md document, helper code, and tests.

The golden skills must:

- Target the **specific failure mode(s)** you observed
- Be **general and reusable** -- not a one-off hint for this exact task
- Not contain the solution or a step-by-step recipe

**What you write for each skill:**

1. **`SKILL.md`** -- the skill document (see format below).
2. **`scripts/<name>.py`** -- a working reference implementation that demonstrates the skill's core concept. This is real, runnable code -- not pseudocode. For example, a JWT validation skill includes a `JWTValidator` class with the correct validation order and clock skew logic.
3. **`scripts/test_<name>.py`** -- unit tests that verify the helper script works. These prove the skill's concepts are sound and executable. Tests should use pytest and be runnable with `python -m pytest scripts/`.

Pass format validation:

```bash
python3 tooling/validate_skill_format.py --skills-dir tasks/<task-slug>/skills
```

**SKILL.md format:**

```markdown
---
name: skill-name
description: One sentence describing what this skill teaches and when to use it.
tags: [tag1, tag2]
version: "1.0"
---

# Skill Name

## When to Use
...

## Key Concepts
...

## Common Pitfalls
...
```

Required frontmatter fields:
- `name`: 1-64 characters, lowercase alphanumeric + hyphens, must match the directory name.
- `description`: non-empty, max 1024 characters.

Size constraints (`validate_skill_format.py` checks these):
- Total: max 500 lines.
- Frontmatter: max 100 words.
- Body: max 5000 words.

Allowed items inside each skill directory: `SKILL.md` (required), `scripts/` (required), `references/` (optional), `assets/` (optional), `LICENSE*` (optional). No hidden files.

### Step 6 -- Verify the golden skills work

```bash
harbor run -p tasks/<task-slug> -e modal -a claude-code -m claude-opus-4-6
```

Expected: score = **1.0**. Revise and re-run if not. Confirm the agent actually read the skill file in the trajectory.

### Step 7 -- Write distractor skills

Add 3-5 distractor skills: thematically related but describe different (wrong or irrelevant) approaches. Validate similarity:

```bash
python3 tooling/validate_skill_similarity.py \
    --skills-dir tasks/<task-slug>/skills \
    --golden <golden-skill-1> <golden-skill-2>
```

Cosine similarity thresholds:

| Score | Verdict | Action |
|-------|---------|--------|
| >= 0.4 | PASS | Clearly in the same domain |
| 0.1 - 0.4 | WARN | Manual review recommended |
| < 0.1 | FAIL | Likely unrelated domain; must fix |

The QC rubric targets >= 0.6; the automated validator passes at >= 0.4.

### Step 8 -- End-to-end validation

```bash
harbor run -p tasks/<task-slug> -e modal -a terminus-2 \
    -m "gemini/gemini-3.1-pro-preview"

harbor run -p tasks/<task-slug> -e modal -a claude-code \
    -m claude-opus-4-6
```

Both should score **1.0**.

### Step 9 -- Fill in `metadata.json`

```json
{
  "golden_skills": ["<golden-1>", "<golden-2>"],
  "distractor_skills": ["<distractor-1>", "<distractor-2>", "<distractor-3>"],
  "failure_modes": {
    "gemini-3.1-pro": {
      "result": "fail",
      "reason": "<how the agent fails without skills>"
    },
    "claude-opus-4-6": {
      "result": "fail",
      "reason": "<how the agent fails without skills>"
    },
    "claude-opus-4-6-with-skills": {
      "result": "pass",
      "reason": "<which skill the agent read and how it helped>"
    }
  }
}
```

Run the full task validator:

```bash
python3 tooling/validate_task.py --task-path tasks/<task-slug>
```

### Step 10 -- Commit, push, and open a PR

```bash
git add tasks/<task-slug>/
git commit -m "Add skills: <task-slug>"
git push -u origin skills-<task-id>
```

Then use the **Code - Create PR** command from Airtable:

```bash
gh pr create --repo mercor-code-envs/skills-<your-id> \
  --title "Task ID: [<airtable-task-id>]" \
  --body "" \
  --base main \
  --assignee @me \
  --draft
```

CI runs `validate_task.py` automatically on your PR. Fix any failures before marking ready for review.

---

## 6. File Reference

### `Dockerfile`
- Required file based on `task-base:latest`.
- Typical structure (varies per task):

```dockerfile
FROM task-base:latest
WORKDIR /app
COPY skills /root/.claude/skills
COPY input_files/ /app/
COPY setup.sh /tmp/
RUN chmod +x /tmp/setup.sh && /tmp/setup.sh
```

- The skills `COPY` destination is **task/Dockerfile-dependent**. Each agent expects skills at a specific path: claude-code reads from `~/.claude/skills`, terminus-2 reads from `/app/skills/`. Check your task's Dockerfile to see which path(s) it copies skills to -- some Dockerfiles include both, others only one.
- `setup.sh` runs during image build, not at runtime.
- Treat as pre-provided; do not modify.

### `instruction.md`
- Model-facing prompt. Must be self-contained and explicit about which files are provided, where the finished implementation should be written, and required behavior/constraints.
- Must not name or hint the golden skills directly.

**Leak policy -- what counts as a leak vs. a valid requirement:**

| Acceptable in instruction | Considered a leak |
|---------------------------|-------------------|
| Functional requirements ("output must contain field X") | Algorithm names the golden skill teaches ("use Aho-Corasick") |
| Observable symptoms ("TLS connection fails") | Implementation details the skill provides (`struct.pack('BBB', ...)`) |
| Input/output formats and constraints | Specific parameter values that are the core insight of the skill |
| File paths and expected directory layout | Class/function names from the solution code |

If the instruction already gives away what your golden skill teaches, either **(a)** rewrite the overlapping portion of the instruction to be less prescriptive (document the change in your PR), or **(b)** choose a different angle for the golden skill so its value is not undermined.

### `setup.sh`
- Installs dependencies (apt and pip) needed by tests and solution execution.
- Runs during Docker image build (via the Dockerfile's `RUN` step), not at runtime. Contributors do not run this locally; Harbor handles it.
- Must be idempotent and non-interactive.

### `tests/test.py`
- Deterministic verifier. Ends with a `__main__` entrypoint that runs pytest and prints exactly `pass` or `fail`.
- Avoid randomness unless seeded, flaky timing assumptions, or network reliance.

### `solution/solve.sh`
- Oracle implementation installer (writes the correct file(s) under `/app`).
- Must pass `tests/test.py` after setup. Should not depend on hidden resources.

### `metadata.json`
- Machine-readable task config and evaluation record.
- Required keys: `task_name`, `category`, `input_files`, `test_file`, `solution_file`, `golden_skills`, `distractor_skills`, `failure_modes`.
- Use `golden_skills` (plural), not `golden_skill`.

### `skills/<skill-name>/scripts/`
- Each skill directory must contain a `scripts/` subdirectory.
- You write **two kinds of code** here:
  - **Helper script** (`<name>.py`) -- a working reference implementation that demonstrates the skill's core concept in runnable code. For example, a `jwt-claim-validation-order` skill includes a `JWTValidator` class showing the correct validation sequence and clock skew logic. This is not pseudocode -- it must execute cleanly.
  - **Test script** (`test_<name>.py`) -- unit tests (pytest) that verify the helper script works correctly. Tests should cover the key behaviors the skill teaches: correct outputs, edge cases, error conditions. Including tests is a **QC expectation** (`scripts/` is required, but `test_*.py` is not required by the validator).
- All scripts must run without errors: exit 0, no import errors, no missing dependencies (QC criterion #8).

---

## 7. Skill Design Standards

### Golden skills
- Target observed baseline failure modes.
- General and reusable across related tasks.
- Should teach method/patterns, not leak task-specific final code.
- Typically include: when to use, core algorithm/process, pitfalls and edge cases.

### Distractor skills
- Thematically similar to golden skills but not useful for the task's core failure mode.
- Internally coherent and technically valid.
- Must pass the same 9 QC criteria as golden skills (see Section 9).

### Typical counts
- 2 golden skills per task.
- 3-5 distractor skills per task.
- Current examples use 2 golden + 4 distractors.

---

## 8. Harbor Evaluation Environment

All task evaluation runs through **Harbor**, an execution harness that builds the task's Docker container and runs an AI agent inside it on Modal.

| Agent | Model flag | Skills path in container |
|-------|-----------|--------------------------|
| terminus-2 | `gemini/gemini-3.1-pro-preview` | `/app/skills/` |
| claude-code | `claude-opus-4-6` | `~/.claude/skills` |

Pass threshold: score = **1.0**. Environment: Modal (`-e modal`).

---

## 9. QC Rubric

All skills (golden and distractor) are evaluated against a 9-criterion rubric defined in `tooling/qc-prompt.md`. A skill **passes QC only if every applicable criterion is PASS**.

### Golden skill criteria (all 9 required)

1. **Atomic & Modular** -- the skill does one specific thing. Single verb-noun name (e.g. `jwt-claim-validation-order`). If you can describe it with "and also," it is doing too much.
2. **Requires Instruction Following** -- the skill's behavior cannot be guessed from training data. Must contain at least one of: idiosyncratic parameter values, non-obvious ordering constraints, edge cases a developer would miss, or implementation patterns that differ from the most natural approach.
3. **Deterministic Outputs** -- for a given input/state, the skill produces predictable results verifiable by assertions or exact comparison.
4. **State-Aware** -- applying the skill leaves a detectable side effect (file written, class importable, data structure with specific shape) that `tests/test.py` can check.
5. **Robust Error Logic** -- specifies exact error messages/conditions so an agent can predict what error will be raised and why.
6. **Specification Compliance** -- only allowed items at skill root (`SKILL.md`, `scripts/`, `references/`, `assets/`, `LICENSE*`), no hidden files.
7. **Concise** -- SKILL.md within size limits (100 words frontmatter, 5000 words / 500 lines body). Overflow material split into `references/`.
8. **Error-Proof Script Execution** -- every script in `scripts/` runs without errors (exit 0, no import errors, no missing dependencies).
9. **General-Purpose & Reusable** -- no project names, ticket IDs, or hardcoded paths. The skill must be plausibly reusable across 3+ different tasks.

### Distractor skill criteria (3 additional)

1. **Cannot Solve the Task** -- an agent using only this distractor skill cannot pass `tests/test.py`. The distractor must not contain the golden skill's critical constants, formulas, error strings, or implementation patterns.
2. **Maximally Relevant** -- name and description are in the same domain as the golden skill and use overlapping terminology. Target cosine similarity >= 0.6.
3. **Same Quality as Golden Skills** -- must pass all 9 golden skill criteria above.

---

## 10. `metadata.json` Schema Details

### Required fields

| Field | Type | Constraint |
|-------|------|------------|
| `task_name` | string | Must match the task directory name |
| `category` | string | Domain category (e.g. "String Algorithms", "Authentication & Security") |
| `input_files` | array of strings | Each file must exist in `input_files/` |
| `test_file` | string | Path to test file (e.g. `"tests/test.py"`) |
| `solution_file` | string | Path to solution (e.g. `"solution/solve.sh"`) |
| `golden_skills` | array of strings | Exactly 2 skill directory names |
| `distractor_skills` | array of strings | 3-5 skill directory names |
| `failure_modes` | object | Evaluation outcomes (see below) |

### `failure_modes` format

structured objects with 3 keys:

```json
{
  "failure_modes": {
    "gemini-3.1-pro": { "result": "fail", "reason": "how the agent fails without skills" },
    "claude-opus-4-6": { "result": "fail", "reason": "how the agent fails without skills" },
    "claude-opus-4-6-with-skills": { "result": "pass", "reason": "which skill the agent read and how it helped" }
  }
}
```

The validator accepts either format but warns on `TODO` values. Record detailed descriptions of observed behavior including which skill files were read and the reward score.

---

## 11. Common Mistakes To Avoid

- Omitting `Dockerfile` from the task package.
- Using `golden_skill` (singular) instead of `golden_skills` (plural) in metadata.
- Writing instructions that leak the required skill name or approach.
- Using nondeterministic tests or tests that do not print `pass`/`fail`.
- Forgetting to record both baseline and with-skills outcomes in `failure_modes`.
- Running `setup.sh` locally instead of using Harbor (setup.sh runs during Docker build).
- Exceeding SKILL.md size limits (500 lines / 5000 words body / 100 words frontmatter).
- Hidden files (`.DS_Store`, `__pycache__`) left in skill directories.
- Skill directory name not matching the `name` field in SKILL.md frontmatter.

---

## 12. Deliverable Checklist

- [x] Task folder includes all required files (`Dockerfile`, `setup.sh`, `instruction.md`, `metadata.json`, `tests/test.py`, `solution/solve.sh`, `input_files/`, `skills/`).
- [x] `instruction.md` is clear and skill-agnostic (no skill names or hints).
- [x] `setup.sh` installs all required dependencies.
- [x] `tests/test.py` is deterministic and prints `pass`/`fail`.
- [x] `solution/solve.sh` passes tests after setup.
- [x] 2 golden skills, each with `SKILL.md` (valid frontmatter) and `scripts/` directory (unit tests in `scripts/` are a QC expectation).
- [x] 3-5 distractor skills, each >= 0.4 cosine similarity with closest golden skill.
- [x] All skill directory names match their `metadata.json` entries.
- [x] `metadata.json` has all required keys and filled `failure_modes` (no TODO values).
- [x] Baseline runs (both agents) fail without skills.
- [x] Both agents score 1.0 with full skill set.
- [x] `python3 tooling/validate_task.py --task-path tasks/<task-slug>` passes.
- [x] PR opened with title `Task ID: [<airtable-task-id>]`.

---

## Quick Reference

| Item | Value |
|------|-------|
| Environment | Modal (`-e modal`) |
| claude-code model | `claude-opus-4-6` |
| terminus-2 model | `gemini/gemini-3.1-pro-preview` (with `gemini/` prefix) |
| Pass threshold | score = 1.0 |
| Skills path in container | `/app/skills/` (terminus-2), `~/.claude/skills` (claude-code) |
| Golden skills | 2 per task |
| Distractor skills | 3-5 per task |
| Similarity threshold | >= 0.4 automated pass, >= 0.6 QC target |

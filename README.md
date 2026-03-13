# GDM Skills — Expert Contributor Guide

Each task in this dataset is a software engineering problem that frontier models **fail without a custom skill** but can solve with it. You will receive a pre-seeded task and your job is to write the **golden skill** (and 3–5 distractors) that unlocks it.

---

## Prerequisites

| Tool | Install |
|------|---------|
| Python 3.11+ | system / pyenv |
| `harbor` | `pip install harbor` |
| `git` + `gh` CLI | `brew install gh` |
| Docker (optional, local fallback) | docker.com |

You will also need:
- API keys for **Gemini** (`GEMINI_API_KEY`) and **Anthropic** (`ANTHROPIC_API_KEY`) for running evals
- Access to the project **Airtable** (link in your onboarding email)

---

## Step 1 — Claim a Task

1. Open the project Airtable and find an unclaimed task in the **Available** column.
2. Assign it to yourself and move it to **In Progress**.
3. Copy the **task slug** (e.g. `grpc-interceptor-fix`).
4. Download the task zip from S3:

```bash
aws s3 cp s3://apex-swebench-extension/tasks/<task-slug>.zip /tmp/<task-slug>.zip
unzip /tmp/<task-slug>.zip -d /tmp/
```

---

## Step 2 — Fork & Branch

```bash
# One-time: fork the template repo
gh repo fork mercor-code-envs/skills-template --clone --remote

cd skills-template

# Per-task: create a branch
git checkout -b task/<task-slug>
```

Copy your task into the repo:

```bash
cp -r /tmp/<task-slug> tasks/
```

Your task directory arrives pre-populated with everything except skills:

```
tasks/<task-slug>/
├── Dockerfile              # ubuntu:24.04 — do not modify
├── setup.sh                # Dependency install + env setup
├── input_files/            # Files the agent sees in /app/
├── skills/                 # ← YOU POPULATE THIS
│   └── .gitkeep
├── instruction.md          # Problem statement shown to the agent
├── metadata.json           # Fill in golden_skills, distractor_skills, failure_modes
├── tests/
│   └── test.py             # Verifier (do not modify)
└── solution/
    └── solve.sh            # Oracle reference solution (do not modify)
```

---

## Step 3 — Understand the Task

Use Harbor to run the oracle (reference solution) and the nop (no-op) agent locally. This shows you the passing state and the failing state before you write any skills.

### Install Harbor

```bash
pip install harbor
```

Harbor requires API keys in your environment:

```bash
export ANTHROPIC_API_KEY="..."
export GEMINI_API_KEY="..."
```

### Run oracle — confirms the task is solvable

```bash
harbor run -p tasks/<task-slug> -e modal -a oracle
# Expected: Score 1.0 (all tests pass)
```

### Run nop — confirms tests don't pass vacuously

```bash
harbor run -p tasks/<task-slug> -e modal -a nop
# Expected: Score 0.0 (all tests fail without agent work)
```

### Run an agent without skills — study the failure

```bash
# claude-code
harbor run -p tasks/<task-slug> -e modal -a claude-code -m claude-opus-4-6

# terminus-2 (always use gemini/ prefix)
harbor run -p tasks/<task-slug> -e modal -a terminus-2 -m "gemini/gemini-3.1-pro-preview"
```

Both agents should score **< 1.0** (task is confirmed hard). Read the trajectory to understand exactly where and why the agent fails — this is the foundation for your golden skill.

> **Local Docker fallback:** If Modal is unavailable, replace `-e modal` with `-e docker`.
> Add `--force-build` whenever you change `Dockerfile`, `setup.sh`, or files under `input_files/`.

---

## Step 4 — Write the Golden Skill

A golden skill is a reusable reference document that provides the specific knowledge an agent needs to solve this class of problem. It is **not** a step-by-step solution to this task.

Create the skill directory:

```bash
mkdir -p tasks/<task-slug>/skills/<skill-name>/scripts
```

### `SKILL.md` structure

```markdown
---
name: <skill-name>
description: >
  One or two sentences. What knowledge does this skill provide?
  What problem does it solve? (max 1024 chars)
---

## When to Use

Describe the situation where an agent should reach for this skill.

## Approach

The core technique, algorithm, or configuration pattern — general enough
to apply across tasks, not just this one.

## Edge Cases

Gotchas, off-by-one errors, version-specific quirks, common wrong assumptions.

## Scripts

Reference `scripts/<script>.py` for a runnable implementation and
`scripts/test_<script>.py` for unit tests.
```

**SKILL.md constraints (enforced by CI):**

| Field | Limit |
|-------|-------|
| `name` | 1–64 chars, lowercase letters/digits/hyphens, matches directory name |
| `description` | 1–1024 chars |
| frontmatter total | < 100 words |
| body content | < 5000 words |
| total file length | ≤ 500 lines |

### `scripts/` directory (required)

Every skill must have a `scripts/` directory containing:
- A standalone implementation (`<skill-name>.py` or similar)
- A unit test file (`test_<skill-name>.py` or similar)

Scripts must exit 0. No hardcoded task-specific paths.

### Golden skill rules

- ✅ General-purpose — reads as a reusable reference, not a hint for this task
- ✅ No wording overlap with `instruction.md`
- ✅ No project names, ticket IDs, or hardcoded `/app/` paths
- ✅ Applicable to 3+ different tasks conceptually
- ❌ Does not contain the solution or step-by-step recipe
- ❌ Does not enumerate exact expected output values

---

## Step 5 — Validate Skill Format

Run the format validator before testing with Harbor:

```bash
python tooling/validate_skill_format.py --skills-dir tasks/<task-slug>/skills
# Expected: "All skills passed format validation."
```

Fix any errors it reports (missing frontmatter fields, file size violations, hidden files, missing `scripts/` directory).

---

## Step 6 — Verify the Golden Skill Unlocks the Task

```bash
harbor run -p tasks/<task-slug> -e modal -a claude-code -m claude-opus-4-6
# Expected: Score 1.0
```

Confirm in the trajectory that the agent **read the skill file autonomously** — it should navigate to `skills/` without being told which file to open. A pass driven by the model's background knowledge (without reading the skill) is marginal and should be noted.

If the agent still fails, read its trajectory, revise the skill, and re-run.

---

## Step 7 — Write 3–5 Distractor Skills

Distractors are skills that are **topically related** to the golden skill but describe a different (incorrect or irrelevant) approach. An agent that reads only a distractor should **not** be able to solve the task.

```bash
mkdir -p tasks/<task-slug>/skills/<distractor-name>/scripts
```

Repeat for 3–5 distractors. Each must:
- Pass the same `SKILL.md` format requirements as the golden skill
- Be in the same domain/topic area as the golden skill
- Clearly **not** contain the answer to the task

---

## Step 8 — Validate Distractor Similarity

Each distractor's `name + description` must have cosine similarity ≥ 0.6 with the golden skill (ensures they are topically related, not random noise):

```bash
python tooling/validate_skill_similarity.py \
  --skills-dir tasks/<task-slug>/skills \
  --golden <golden-skill-name>
```

Example output:
```
  oauth2-pkce-flow: cosine similarity = 0.7423
  session-token-rotation: cosine similarity = 0.6891
  api-key-hmac-signing: cosine similarity = 0.8102
  password-bcrypt-hashing: cosine similarity = 0.6340

All 4 distractors passed similarity validation (>= 0.6).
```

If a distractor scores below 0.6, revise its `name` or `description` to be more topically aligned, or replace it.

---

## Step 9 — E2E Verification (Full Skill Set)

With golden + all distractors in place, run both agents:

```bash
# Both should score 1.0 — agents must find the golden skill among the distractors
harbor run -p tasks/<task-slug> -e modal -a claude-code -m claude-opus-4-6
harbor run -p tasks/<task-slug> -e modal -a terminus-2 -m "gemini/gemini-3.1-pro-preview"
```

> **Critical for terminus-2:** always use the `gemini/` prefix. Without it, litellm routes to Vertex AI which will fail with an `APIConnectionError`.

Confirm in both trajectories that the correct golden skill was read.

---

## Step 10 — Update `metadata.json`

Fill in the three TODO fields:

```json
{
  "task_name": "<task-slug>",
  "category": "...",
  "input_files": ["..."],
  "test_file": "tests/test.py",
  "solution_file": "solution/solve.sh",
  "golden_skills": ["<your-golden-skill-name>"],
  "distractor_skills": ["<distractor-1>", "<distractor-2>", "<distractor-3>"],
  "failure_modes": {
    "gemini-3.1-pro": {
      "result": "fail",
      "reason": "<from Step 3 trajectory — what exactly did the agent get wrong?>"
    },
    "claude-opus-4-6": {
      "result": "fail",
      "reason": "<from Step 3 trajectory — what exactly did the agent get wrong?>"
    },
    "claude-opus-4-6-with-skills": {
      "result": "pass",
      "reason": "<which skill the agent read and how it unlocked the solution>"
    }
  }
}
```

---

## Step 11 — Final Structural Validation

```bash
python tooling/validate_task.py --task-path tasks/<task-slug>
# Expected: "All checks passed for task: <task-slug>"
```

This catches any structural issues (missing files, wrong metadata field values, skill directories not listed in metadata, etc.) before CI runs.

---

## Step 12 — Open a PR

```bash
git add tasks/<task-slug>/
git commit -m "feat: add skills for <task-slug>"
git push -u origin task/<task-slug>
gh pr create --title "Add skills: <task-slug>" --body "Closes task/<task-slug>"
```

**CI runs automatically on every push:**

| Check | Trigger | What it does |
|-------|---------|-------------|
| `validate-task` | Every push to PR | Structural validation — metadata, files, skill format |
| `qc-review` | When PR is marked **Ready for Review** | Full QC via Mercor API — skill quality, distractor quality |

Fix any errors reported in the PR comment before marking ready for review.

---

## Deliverable Checklist

Before marking the PR ready for review:

- [ ] `skills/<golden-skill>/SKILL.md` — passes format validation, general-purpose
- [ ] `skills/<golden-skill>/scripts/` — implementation + unit tests present
- [ ] 3–5 distractor skills, each with `SKILL.md` + `scripts/`
- [ ] All distractors pass similarity check ≥ 0.6 vs golden
- [ ] `claude-code` scores 1.0 with full skill set (Step 9)
- [ ] `terminus-2` scores 1.0 with full skill set (Step 9)
- [ ] Both agents score < 1.0 without skills (Step 3 — already calibrated, re-verify if you edited `instruction.md`)
- [ ] `metadata.json` fully filled — no TODO strings remain
- [ ] `python tooling/validate_task.py` exits 0

---

## Task Structure Reference

```
tasks/<task-name>/
├── Dockerfile                  # ubuntu:24.04 base — do not modify
├── setup.sh                    # Installs dependencies, sets up environment
├── input_files/                # Files copied to /app/ in the container
│   └── .gitkeep
├── skills/                     # You create these
│   ├── <golden-skill>/
│   │   ├── SKILL.md
│   │   └── scripts/
│   ├── <distractor-1>/
│   │   ├── SKILL.md
│   │   └── scripts/
│   └── ...                     # 3–5 distractors total
├── instruction.md              # Shown to agent — do not modify
├── metadata.json               # Fill in golden_skills, distractor_skills, failure_modes
├── tests/
│   └── test.py                 # Verifier — do not modify
└── solution/
    └── solve.sh                # Oracle — do not modify
```

---

## Harbor Quick Reference

```bash
# Confirm task is solvable (score should be 1.0)
harbor run -p tasks/<task> -e modal -a oracle

# Confirm task isn't trivially solvable (score should be 0.0)
harbor run -p tasks/<task> -e modal -a nop

# Run claude-code agent
harbor run -p tasks/<task> -e modal -a claude-code -m claude-opus-4-6

# Run terminus-2 agent (gemini/ prefix required)
harbor run -p tasks/<task> -e modal -a terminus-2 -m "gemini/gemini-3.1-pro-preview"

# View trajectory in browser
harbor view jobs/<job-dir>

# Local Docker (if Modal unavailable)
harbor run -p tasks/<task> -e docker -a claude-code -m claude-opus-4-6 --force-build
```

---

## Tooling Quick Reference

```bash
# Validate full task structure (metadata, files, skills)
python tooling/validate_task.py --task-path tasks/<task>

# Validate SKILL.md format for all skills in a task
python tooling/validate_skill_format.py --skills-dir tasks/<task>/skills

# Validate distractor similarity vs golden skill
python tooling/validate_skill_similarity.py \
  --skills-dir tasks/<task>/skills \
  --golden <golden-skill-name>
```

---

## Example Task

See `example_tasks/jwt-claims-validator/` for a complete worked example with:
- Two golden skills (`jwt-claim-validation-order`, `jwt-timing-safe-verify`)
- Four distractor skills
- Fully filled `metadata.json` with `failure_modes`
- Complete `scripts/` with implementation and unit tests

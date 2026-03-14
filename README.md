# GDM Skills — Expert Task Guide

Each task is a software engineering problem that frontier AI models **fail without a custom skill** but can solve once given one. Your job is to write that skill.

---

## Getting Started

All tasks are assigned through Airtable. When you claim a task, a GitHub repository is automatically forked for you within ~5 minutes.

### Step 1 — Claim your task in Airtable

Open your task list in the Airtable interface and claim a task. The automation will:
- Fork `mercor-code-envs/skills-template` into `mercor-code-envs/skills-<your-id>`
- Add you as a collaborator on the fork
- Update your task status to **In Progress**

### Step 2 — Clone, create branch, and download task files

Copy and run the three **Code** commands shown in your Airtable task record in order:

**Code - Clone Repo:**
```bash
gh repo clone mercor-code-envs/skills-<your-id>
cd skills-<your-id>
```

**Code - Create Branch** (creates your task branch locally):
```bash
git checkout -b skills-<task-id>
```

**Code - Download S3** (downloads your task into `tasks/<task-slug>/`):
```bash
python3 tooling/download_s3.py \
  --s3-url "<url from airtable>" \
  --task-name "<task name from airtable>"
```

Your task is now at `tasks/<task-slug>/`.

### Step 3 — Install Harbor (for local evaluation)

```bash
pip install harbor
```

Or with uv: `uv tool install harbor`

---

## Your Task: What to Build

Inside `tasks/<task-slug>/` you will find:

```
tasks/<task-slug>/
├── Dockerfile              # ubuntu:24.04 — do not modify
├── setup.sh                # Install/setup logic
├── input_files/            # Task data files
├── skills/                 # ← YOU FILL THIS IN
│   └── .gitkeep
├── instruction.md          # Problem statement (what the AI sees)
├── metadata.json           # Fill in golden_skills, distractor_skills, failure_modes
├── tests/
│   └── test.py             # Verifier — do not modify
└── solution/
    └── solve.sh            # Reference solution — do not modify
```

You need to add **2+ golden skills + 3–5 distractor skills** under `tasks/<task-slug>/skills/`.

---

## Step 4 — Run the Task Without Skills (Baseline)

Confirm both agents fail before you write anything:

```bash
harbor run -p tasks/<task-slug> -e modal -a terminus-2 \
    -m "gemini/gemini-3.1-pro-preview"

harbor run -p tasks/<task-slug> -e modal -a claude-code \
    -m claude-opus-4-6
```

Both should score < 1.0. Note what each agent gets wrong — this tells you what the skill needs to teach.

---

## Step 5 — Write the Golden Skills

Create `tasks/<task-slug>/skills/<skill-name>/SKILL.md`. The golden skills must:

- Target the **specific failure mode(s)** you observed
- Be **general and reusable** — not a one-off hint for this exact task
- Not contain the solution or a step-by-step recipe
- Pass format validation:

```bash
python3 tooling/validate_skill_format.py tasks/<task-slug>/skills/<skill-name>/SKILL.md
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

---

## Step 6 — Verify the Golden Skills Work

```bash
harbor run -p tasks/<task-slug> -e modal -a claude-code -m claude-opus-4-6
```

Expected: score = **1.0**. Revise and re-run if not. Confirm the agent actually read the skill file in the trajectory.
The agent must use **all skills** in order to be considered passing.

---

## Step 7 — Write Distractor Skills

Add 3–5 distractor skills: thematically related but describe different (wrong or irrelevant) approaches. Each must score ≥ 0.6 cosine similarity with the golden skill:

```bash
python3 tooling/validate_skill_similarity.py \
    tasks/<task-slug>/skills/<golden-skill>/SKILL.md \
    tasks/<task-slug>/skills/<distractor-name>/SKILL.md
```

---

## Step 8 — End-to-End Validation

```bash
harbor run -p tasks/<task-slug> -e modal -a terminus-2 \
    -m "gemini/gemini-3.1-pro-preview"

harbor run -p tasks/<task-slug> -e modal -a claude-code \
    -m claude-opus-4-6
```

Both should score **1.0**.

---

## Step 9 — Fill in `metadata.json`

```json
{
  "golden_skills": ["<skill-name>"],
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
python3 tooling/validate_task.py tasks/<task-slug>
```

---

## Step 10 — Commit, Push, and Open a PR

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

## Deliverable Checklist

- [ ] `tasks/<task-slug>/skills/<golden-skill>/SKILL.md` — passes format check
- [ ] `tasks/<task-slug>/skills/<distractor-N>/SKILL.md` — 3–5 distractors, each ≥ 0.6 similarity
- [ ] `metadata.json` — all three `failure_modes` entries filled in
- [ ] `claude-code` scores 1.0 with skills
- [ ] Both agents score 1.0 with full skill set
- [ ] `python3 tooling/validate_task.py tasks/<task-slug>` passes
- [ ] PR opened with title `Task ID: [<airtable-task-id>]`

---

## Quick Reference

| Item | Value |
|------|-------|
| Environment | Modal (`-e modal`) |
| claude-code model | `claude-opus-4-6` |
| terminus-2 model | `gemini/gemini-3.1-pro-preview` (with `gemini/` prefix) |
| Pass threshold | score = 1.0 |
| Skills path in container | `/app/skills/` (terminus-2), `~/.claude/skills` (claude-code) |

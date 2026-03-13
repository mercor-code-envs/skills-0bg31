# GDM Skills — Expert Task Guide

Each task is a software engineering problem that frontier AI models **fail without a custom skill** but can solve once given one. Your job is to write that skill.

---

## Getting Started

All tasks are assigned through Airtable. When you claim a task, a GitHub repository and branch are automatically set up for you.

### Step 1 — Claim your task in Airtable

Open your task list in the Airtable interface and claim a task. Within ~5 minutes:
- A GitHub repo (`mercor-code-envs/skills-<your-id>`) is created for you
- A branch (`skills-<task-id>`) is created with the task files already committed
- You are added as a collaborator on the repo
- Your task status updates to **In Progress**

### Step 2 — Clone your repo and checkout your task branch

Use the **Code - Clone Repo** command shown in Airtable:

```bash
gh repo clone mercor-code-envs/skills-<your-id>
cd skills-<your-id>
```

Then checkout your task branch (it already exists — the task files are there):

```bash
git fetch origin
git checkout skills-<task-id>
```

> ⚠️ Do NOT use `git checkout -b` — your branch already exists with task files committed.
> Use `git checkout skills-<task-id>` (no `-b` flag) to check out the existing branch.

Your task is in `tasks/<task-slug>/`.

### Step 3 — Download Harbor (for local evaluation)

```bash
pip install harbor-bench
```

Or follow the [Harbor install guide](https://github.com/Mercor-Intelligence/harbor).

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
│   └── test.py             # Verifier (do not modify)
└── solution/
    └── solve.sh            # Reference solution (do not modify)
```

You need to add **1 golden skill + 3–5 distractor skills** under `tasks/<task-slug>/skills/`.

---

## Step 4 — Run the Task Without Skills (Baseline)

Confirm both agents fail before you write anything:

```bash
# Run without skills to see how agents fail
harbor run -p tasks/<task-slug> -e modal -a terminus-2 \
    -m "gemini/gemini-3.1-pro-preview"

harbor run -p tasks/<task-slug> -e modal -a claude-code \
    -m claude-opus-4-6
```

Both should score < 1.0. Note what each agent gets wrong — this tells you what the skill needs to teach.

---

## Step 5 — Write the Golden Skill

Create `tasks/<task-slug>/skills/<skill-name>/SKILL.md`. The golden skill must:

- Target the **specific failure mode** you observed (what did the agent miss?)
- Be **general and reusable** — not a one-off hint for this exact task
- Not contain the solution or a step-by-step recipe
- Pass the format check:

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

## Step 6 — Verify the Golden Skill Works

Run `claude-code` with the skill in place:

```bash
harbor run -p tasks/<task-slug> -e modal -a claude-code -m claude-opus-4-6
```

Expected: score = **1.0**. If not, revise the skill and re-run. Check the trajectory to confirm the agent actually **read** the skill file.

---

## Step 7 — Write Distractor Skills

Add 3–5 distractor skills: thematically related but describe different (wrong or irrelevant) approaches.

```bash
# Check cosine similarity — each distractor must score ≥ 0.6 vs the golden skill
python3 tooling/validate_skill_similarity.py \
    tasks/<task-slug>/skills/<golden-skill>/SKILL.md \
    tasks/<task-slug>/skills/<distractor-name>/SKILL.md
```

---

## Step 8 — End-to-End Validation

With the full skill set in place, run both agents:

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

Then run the full task validator:

```bash
python3 tooling/validate_task.py tasks/<task-slug>
```

---

## Step 10 — Submit Your PR

Use the **Code - Create PR** command shown in Airtable:

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

Before submitting:

- [ ] `tasks/<task-slug>/skills/<golden-skill>/SKILL.md` — passes format check
- [ ] `tasks/<task-slug>/skills/<distractor-N>/SKILL.md` — 3–5 distractors, each ≥ 0.6 cosine similarity
- [ ] `metadata.json` — all three `failure_modes` entries filled in
- [ ] `claude-code` scores 1.0 with skills (Step 6)
- [ ] Both agents score 1.0 with full skill set (Step 8)
- [ ] `python3 tooling/validate_task.py tasks/<task-slug>` passes
- [ ] PR created with title `Task ID: [<airtable-task-id>]`

---

## Quick Reference

| Item | Value |
|------|-------|
| Environment | Modal (`-e modal`) |
| claude-code model | `claude-opus-4-6` |
| terminus-2 model | `gemini/gemini-3.1-pro-preview` (with `gemini/` prefix) |
| Pass threshold | score = 1.0 |
| Skills path in container | `/app/skills/` (terminus-2), `~/.claude/skills` (claude-code) |

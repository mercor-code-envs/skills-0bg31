---
name: qc-skill
description: Quality-check agent skill directories against the Agent Skills specification. Validates directory structure, SKILL.md frontmatter, conciseness limits, script presence, distractor count and similarity, and whether skills are sufficiently general rather than overly task-specific. Use when reviewing submitted golden or distractor skills before approval.
---

# Skill QC

## Quick Start

```bash
# Validate structure and frontmatter for all skills in a directory
python scripts/validate_skill_format.py --skills-dir tasks/<slug>/skills/

# Validate distractor count (3-5) and cosine similarity to golden(s)
python scripts/validate_skill_similarity.py \
    --skills-dir tasks/<slug>/skills/ \
    --golden <golden-skill-name> [<golden-skill-name-2>]
```

Both scripts exit 0 on success, 1 on failure, 2 on config error.

---

## Full QC Checklist

Work through Steps 1–3 in order. A skill is approved only when every check passes.

### Step 1: Automated Checks

Run both scripts and resolve all errors before proceeding to manual review.

**`validate_skill_format.py`** checks each skill directory for:

| Check | Rule |
|---|---|
| No unexpected files | Only `SKILL.md`, `scripts/`, `references/`, `assets/`, `LICENSE*` allowed |
| No hidden files/dirs | `.DS_Store`, `.pytest_cache`, `__pycache__`, etc. must not be present |
| `SKILL.md` present | Required |
| Valid YAML frontmatter | Must open and close with `---` |
| `name` present and valid | Non-empty, 1–64 chars, `[a-z0-9-]` only, no leading/trailing/consecutive hyphens, matches directory name |
| `description` present | Non-empty, 1–1024 chars |
| `compatibility` length | ≤ 500 chars if provided |
| Body content present | Non-empty markdown after closing `---` |
| SKILL.md line count | ≤ 500 lines |
| Frontmatter word count | < 100 words |
| Body word count | < 5000 words |
| `scripts/` exists | Required directory |

**`validate_skill_similarity.py`** checks:

| Check | Rule |
|---|---|
| Distractor count | Exactly 3–5 distractor skills |
| Cosine similarity | PASS ≥ 0.4 · WARN 0.1–0.4 (manual review) · FAIL < 0.1 |
| Multiple goldens | Pass `--golden name1 name2` when a task has two golden skills |

> **Note on WARN scores:** TF cosine on short descriptions can undercount similarity when
> two skills use different vocabulary for the same domain (common in human-written skills).
> A WARN is not a blocker — manually verify the distractor is thematically related and
> document your rationale in the PR description.

---

### Step 2: Manual Checks

#### 2.1 Directory Structure

Confirm the layout matches the spec exactly:

```
skill-name/
├── SKILL.md          ← required
└── scripts/          ← required
    └── *.py / *.sh
references/           ← optional
assets/               ← optional
LICENSE*              ← optional
```

No other files or directories permitted at the skill root.

Check that `SKILL.md` body contains all of:
- Step-by-step instructions for the agent
- Input/output examples
- Common edge cases and error handling guidance

#### 2.2 Golden Skill Rubric

Each skill (golden or distractor) must satisfy every criterion:

| Criterion | Pass condition |
|---|---|
| Atomic & Modular | Does exactly one thing; clear verb-noun name (e.g., `extract-metadata`) |
| Instruction-Reliant | Agent fails without reading SKILL.md; behavior is not guessable |
| Deterministic Outputs | Same input + state → same result every time |
| State-Aware | Leaves a verifiable side effect (file, DB row, config change) |
| Robust Error Logic | Actionable error messages for invalid inputs and unexpected states |
| Error-proof Scripts | All scripts in `scripts/` run without errors (test with a unit test) |

#### 2.3 Over-Specificity Check

**A skill is too specific if it only works for one task, project, or fixed environment.**

Flag the skill if ANY of the following are true:

- [ ] The name contains a project name, ticket ID, or person's name
- [ ] The description references a single task or PR
- [ ] Scripts contain hardcoded paths, credentials, or hostnames not exposed as parameters
- [ ] The skill cannot plausibly be reused across ≥ 3 different tasks or contexts
- [ ] The behavior depends on a specific undocumented file name or schema

#### 2.4 Distractor Skill Additional Checks

- [ ] Distractor count is 3–5
- [ ] Each distractor's name and description is in the same domain as the golden skill
- [ ] Each distractor satisfies the full Golden Skill Rubric
- [ ] No distractor can solve the triggering task

---

### Step 3: Report Findings

```
Skill: <skill-name>
Check: <criterion name>
Issue: <specific problem>
Fix:   <what the author should do>
```

A skill submission is **approved** only when all automated and manual checks pass.

---

## Spec Reference

### Frontmatter Fields

| Field | Required | Constraint |
|---|---|---|
| `name` | Yes | 1–64 chars, `[a-z0-9-]`, no leading/trailing/consecutive hyphens, matches dir name |
| `description` | Yes | 1–1024 chars, non-empty |
| `license` | No | Short license name or bundled file reference |
| `compatibility` | No | 1–500 chars, environment requirements only |

### Length Limits

| Element | Limit |
|---|---|
| YAML frontmatter | < 100 words |
| Body content | < 5000 words |
| Total SKILL.md | ≤ 500 lines |

### Similarity Thresholds (calibrated on 16 official example-task distractors)

| Score | Status | Action |
|---|---|---|
| ≥ 0.4 | PASS | No action needed |
| 0.1 – 0.4 | WARN | Manual review — confirm same domain |
| < 0.1 | FAIL | Too dissimilar — must revise distractor |

---

## Edge Cases

- **Empty `scripts/` directory**: The directory must exist. Empty is acceptable only if all executable logic is documented as external commands in SKILL.md.
- **Similarity false negatives**: Human-written descriptions that use different vocabulary for the same domain commonly score 0.1–0.4. A WARN here is expected and not a blocker — apply manual judgment.
- **Skills with digits in name**: Valid (e.g., `pdf-v2`), but digits alone must not be the only differentiator.
- **Multiple golden skills**: Pass both names to `--golden`; the tool scores each distractor against the closest golden.

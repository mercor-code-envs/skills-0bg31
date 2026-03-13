#!/usr/bin/env python3
"""
Validate that each distractor skill's (name + description) has cosine similarity >= 0.4
with the closest golden skill's (name + description). Uses only YAML frontmatter fields.

Also enforces that there are 3-5 distractor skills (per spec).

Uses TF cosine similarity (no external model). Run from the task directory or pass --skills-dir.

Thresholds (calibrated on 16 official example-task distractors):
  >= 0.4   PASS  — clearly in the same domain
  0.1–0.4  WARN  — same general domain, different vocabulary; manual review recommended
  < 0.1    FAIL  — likely unrelated domain; must fix

Exit codes:
  0 — all distractors pass or warn (manual review may be needed)
  1 — one or more distractors hard-fail (< 0.1) or wrong count
  2 — usage / config error

Usage:
  python validate_skill_similarity.py --skills-dir tasks/<slug>/skills --golden NAME [NAME2 ...]
"""

import argparse
import math
import re
import sys
from pathlib import Path


def find_skills_dir(given):
    if given:
        return Path(given).resolve()
    return Path.cwd() / "skills"


def parse_frontmatter(content):
    """Parse YAML frontmatter including multiline block scalars (> and |)."""
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    data = {}
    lines = parts[1].splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            i += 1
            continue
        k, v = stripped.split(":", 1)
        key = k.strip()
        val = v.strip().strip("'\"").strip()

        if val in (">", "|", ">-", "|-", ">+", "|+") and key:
            # Block scalar — collect following indented lines
            fold = val.startswith(">")
            block_lines = []
            i += 1
            while i < len(lines):
                bl = lines[i]
                if bl and not bl[0].isspace():
                    break  # next top-level key
                block_lines.append(bl.strip())
                i += 1
            val = (" " if fold else "\n").join(ln for ln in block_lines if ln)
        else:
            i += 1

        if key and key not in data:
            data[key] = val
    return data


def tokenize(text):
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def tf_vec(text):
    vec = {}
    for t in tokenize(text):
        vec[t] = vec.get(t, 0) + 1
    return vec


def cosine(a, b):
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    na = math.sqrt(sum(v ** 2 for v in a.values())) or 1e-10
    nb = math.sqrt(sum(v ** 2 for v in b.values())) or 1e-10
    return dot / (na * nb)


def get_name_desc(skill_dir):
    skill_md = Path(skill_dir) / "SKILL.md"
    if not skill_md.exists():
        return None, None
    data = parse_frontmatter(skill_md.read_text())
    return data.get("name"), data.get("description")


def main():
    parser = argparse.ArgumentParser(
        description="Validate distractor skill cosine similarity vs golden skill(s)."
    )
    parser.add_argument("--skills-dir", default=None, help="Path to skills/ directory")
    parser.add_argument(
        "--golden", required=True, nargs="+",
        help="Golden skill directory name(s) — one or more"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.4,
        help="Warn threshold (default 0.4). Distractors below this need manual review."
    )
    parser.add_argument(
        "--fail-threshold", type=float, default=0.1,
        help="Hard-fail threshold (default 0.1). Distractors below this must be fixed."
    )
    args = parser.parse_args()

    skills_dir = find_skills_dir(args.skills_dir)
    if not skills_dir.exists():
        print(f"Error: skills directory not found: {skills_dir}", file=sys.stderr)
        sys.exit(2)

    # Load all golden skill vectors
    golden_vecs = []
    for golden_name in args.golden:
        golden_dir = skills_dir / golden_name
        if not golden_dir.is_dir():
            print(f"Error: golden skill not found: {golden_dir}", file=sys.stderr)
            sys.exit(2)
        name, desc = get_name_desc(golden_dir)
        if not name or not desc:
            print(f"Error: golden skill '{golden_name}' missing name or description", file=sys.stderr)
            sys.exit(2)
        print(f"Golden: {name}")
        print(f"  desc: {desc[:100]}{'...' if len(desc) > 100 else ''}")
        golden_vecs.append(tf_vec(f"{name} {desc}"))
    print()

    # Find distractors (all skill dirs that are not golden)
    all_skill_dirs = [d for d in skills_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    distractors = [d for d in all_skill_dirs if d.name not in args.golden]

    if len(distractors) < 3:
        print(f"Error: found {len(distractors)} distractor(s); spec requires 3-5.", file=sys.stderr)
        sys.exit(1)
    if len(distractors) > 5:
        print(f"Error: found {len(distractors)} distractor(s); spec requires 3-5.", file=sys.stderr)
        sys.exit(1)

    hard_failures = []
    soft_warnings = []

    for skill_dir in sorted(distractors):
        name, desc = get_name_desc(skill_dir)
        if not name or not desc:
            hard_failures.append((skill_dir.name, None, "missing name or description in frontmatter"))
            print(f"  {skill_dir.name}: ERROR — missing name or description")
            continue

        vec = tf_vec(f"{name} {desc}")
        sim = max(cosine(gv, vec) for gv in golden_vecs)
        desc_preview = desc[:80] + ("..." if len(desc) > 80 else "")

        if sim < args.fail_threshold:
            status = f"FAIL (< {args.fail_threshold}) — likely unrelated domain, must fix"
            hard_failures.append((skill_dir.name, sim, status))
        elif sim < args.threshold:
            status = f"WARN (< {args.threshold}) — manual review recommended"
            soft_warnings.append((skill_dir.name, sim, status))
        else:
            status = "PASS"

        print(f"  {skill_dir.name}: {sim:.4f}  [{status}]")
        print(f"    desc: {desc_preview}\n")

    print()
    if hard_failures:
        print("Hard failures — distractors must be more thematically related to the golden:", file=sys.stderr)
        for name, sim, msg in hard_failures:
            sim_str = f"{sim:.4f}" if sim is not None else "N/A"
            print(f"  {name}: {sim_str} — {msg}", file=sys.stderr)
        sys.exit(1)

    if soft_warnings:
        print(
            f"Warnings: {len(soft_warnings)} distractor(s) scored below {args.threshold}.\n"
            "TF cosine can undercount similarity when descriptions use different vocabulary\n"
            "for the same domain. Manually verify these distractors are thematically related\n"
            "to the golden skill before submitting."
        )
    else:
        print(f"All {len(distractors)} distractors passed similarity check (>= {args.threshold}).")

    sys.exit(0)


if __name__ == "__main__":
    main()

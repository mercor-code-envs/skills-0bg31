#!/usr/bin/env python3
"""
Validate that each distractor skill's (name + description) has cosine similarity >= 0.6
with the golden skill's (name + description). Uses only YAML frontmatter name and description.

Also enforces that there are 3-5 distractor skills (per spec).

Uses TF-IDF + cosine similarity (no external model). Run from environment/ or pass --skills-dir.
Usage: python validate_skill_similarity.py [--skills-dir PATH] [--golden NAME]
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
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    data = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip()
        val = v.strip().strip("'\"").strip()
        if key and key not in data:
            data[key] = val
    return data


def tokenize(text):
    text = (text or "").lower()
    return re.findall(r"[a-z0-9]+", text)


def text_to_vec(text, all_docs=None):
    """Bag-of-words with optional TF-IDF. For short texts, TF only often gives more stable cosine."""
    tokens = tokenize(text)
    vec = {}
    for t in tokens:
        vec[t] = vec.get(t, 0) + 1
    if all_docs:
        doc_tokens = [tokenize(d) for d in all_docs]
        n_docs = len(all_docs)
        for t in list(vec):
            df = sum(1 for doc in doc_tokens if t in doc)
            idf = math.log((n_docs + 1) / (df + 1)) + 1
            vec[t] *= idf
    return vec


def cosine(a, b):
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    na = math.sqrt(sum(a.get(k, 0) ** 2 for k in a)) or 1e-10
    nb = math.sqrt(sum(b.get(k, 0) ** 2 for k in b)) or 1e-10
    return dot / (na * nb)


def get_name_desc(skill_dir):
    skill_md = Path(skill_dir) / "SKILL.md"
    if not skill_md.exists():
        return None, None
    data = parse_frontmatter(skill_md.read_text())
    return data.get("name"), data.get("description")


def main():
    parser = argparse.ArgumentParser(description="Validate distractor skills have >= 0.6 similarity to golden.")
    parser.add_argument("--skills-dir", default=None, help="Path to skills directory")
    parser.add_argument("--golden", required=True, help="Golden skill directory name")
    parser.add_argument("--threshold", type=float, default=0.6, help="Minimum cosine similarity (default 0.6)")
    args = parser.parse_args()

    skills_dir = find_skills_dir(args.skills_dir)
    if not skills_dir.exists():
        print(f"Error: skills directory not found: {skills_dir}", file=sys.stderr)
        sys.exit(2)

    golden_dir = skills_dir / args.golden
    if not golden_dir.is_dir():
        print(f"Error: golden skill not found: {golden_dir}", file=sys.stderr)
        sys.exit(2)

    golden_name, golden_desc = get_name_desc(golden_dir)
    if not golden_name or not golden_desc:
        print("Error: golden skill missing name or description in frontmatter", file=sys.stderr)
        sys.exit(2)

    skill_dirs = [d for d in skills_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    distractors = [d for d in skill_dirs if d.name != args.golden]

    # Enforce 3-5 distractor count
    if len(distractors) < 3:
        print(
            f"Error: found {len(distractors)} distractor skill(s); spec requires 3-5.",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(distractors) > 5:
        print(
            f"Error: found {len(distractors)} distractor skill(s); spec requires 3-5.",
            file=sys.stderr,
        )
        sys.exit(1)

    golden_text = f"{golden_name} {golden_desc}".strip()
    golden_emb = text_to_vec(golden_text, None)

    failed = []
    for skill_dir in distractors:
        name, desc = get_name_desc(skill_dir)
        if not name or not desc:
            failed.append((skill_dir.name, None, "missing name or description"))
            continue
        text = f"{name} {desc}".strip()
        emb = text_to_vec(text, None)
        sim = cosine(golden_emb, emb)
        print(f"  {skill_dir.name}: cosine similarity = {sim:.4f}")
        if sim < args.threshold:
            failed.append((skill_dir.name, sim, f"below threshold {args.threshold}"))

    if failed:
        print("\nFailures:", file=sys.stderr)
        for name, sim, msg in failed:
            if sim is not None:
                print(f"  {name}: {sim:.4f} - {msg}", file=sys.stderr)
            else:
                print(f"  {name}: {msg}", file=sys.stderr)
        sys.exit(1)
    print(f"\nAll {len(distractors)} distractors passed similarity validation (>= {args.threshold}).")
    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Validate skill directories under environment/skills/ for spec compliance:
- Only allowed files/dirs present (SKILL.md, scripts/, references/, assets/, LICENSE*)
- No hidden files or directories (.DS_Store, .pytest_cache, __pycache__, etc.)
- SKILL.md exists and has YAML frontmatter (---)
- Required frontmatter: name, description
- name: non-empty, lowercase, hyphenated, 1-64 chars, matches directory name
- description: non-empty, <= 1024 chars
- compatibility: <= 500 chars if present
- Body content present after frontmatter
- SKILL.md <= 500 lines total
- YAML frontmatter < 100 words
- Body content < 5000 words
- scripts/ directory exists

Run from repository root or from environment/ with skills in ./skills/.
Usage: python validate_skill_format.py [--skills-dir PATH]
"""

import argparse
import re
import sys
from pathlib import Path

ALLOWED_ITEMS = {
    "SKILL.md", "scripts", "references", "assets",
    "LICENSE", "LICENSE.txt", "LICENSE.md",
}


def find_skills_dir(given):
    if given:
        return Path(given).resolve()
    return Path.cwd() / "skills"


def parse_frontmatter(content):
    if not content.startswith("---"):
        return None, "SKILL.md must start with YAML frontmatter (---)"
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None, "Frontmatter not closed with ---"
    data = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            key = k.strip()
            val = v.strip().strip("'\"").strip()
            if key and key not in data:
                data[key] = val
    return data, None


def validate_skill(skill_dir):
    errors = []
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        return [f"Not a directory: {skill_dir}"]

    # Check for unexpected or hidden files/dirs inside the skill directory
    for item in skill_dir.iterdir():
        if item.name.startswith("."):
            errors.append(
                f"{skill_dir.name}: hidden file/directory not allowed: '{item.name}' "
                f"(remove .DS_Store, .pytest_cache, __pycache__, etc.)"
            )
        elif item.name not in ALLOWED_ITEMS:
            errors.append(
                f"{skill_dir.name}: unexpected item '{item.name}' "
                f"(allowed: {', '.join(sorted(ALLOWED_ITEMS))})"
            )

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        errors.append(f"Missing SKILL.md in {skill_dir.name}")
        return errors

    content = skill_md.read_text()

    # Line count
    lines = content.splitlines()
    if len(lines) > 500:
        errors.append(f"{skill_dir.name}: SKILL.md exceeds 500 lines ({len(lines)})")

    if not content.startswith("---"):
        errors.append(f"{skill_dir.name}: SKILL.md must start with ---")
        return errors

    parts = content.split("---", 2)
    if len(parts) < 3:
        errors.append(f"{skill_dir.name}: frontmatter not closed with ---")
        return errors

    fm_text = parts[1]
    body_text = parts[2]

    # Frontmatter word count
    fm_words = len(fm_text.split())
    if fm_words >= 100:
        errors.append(f"{skill_dir.name}: YAML frontmatter exceeds 100 words ({fm_words})")

    # Body content required
    if not body_text.strip():
        errors.append(f"{skill_dir.name}: SKILL.md has no body content after frontmatter")

    # Body word count
    body_words = len(body_text.split())
    if body_words > 5000:
        errors.append(f"{skill_dir.name}: body content exceeds 5000 words ({body_words})")

    data, err = parse_frontmatter(content)
    if err:
        errors.append(f"{skill_dir.name}: {err}")
        return errors

    # name validation
    if "name" not in data:
        errors.append(f"{skill_dir.name}: Missing required field 'name'")
    else:
        name = str(data["name"]).strip()
        if len(name) == 0:
            errors.append(f"{skill_dir.name}: name must not be empty")
        else:
            if len(name) > 64:
                errors.append(f"{skill_dir.name}: name exceeds 64 characters")
            if name != name.lower():
                errors.append(f"{skill_dir.name}: name must be lowercase")
            if name.startswith("-") or name.endswith("-"):
                errors.append(f"{skill_dir.name}: name cannot start or end with hyphen")
            if "--" in name:
                errors.append(f"{skill_dir.name}: name cannot contain consecutive hyphens")
            if not re.match(r"^[a-z0-9-]+$", name):
                errors.append(f"{skill_dir.name}: name must be lowercase letters, digits, hyphens only")
            if skill_dir.name != name:
                errors.append(f"{skill_dir.name}: directory name must match skill name '{name}'")

    # description validation
    if "description" not in data:
        errors.append(f"{skill_dir.name}: Missing required field 'description'")
    else:
        desc = str(data["description"]).strip()
        if len(desc) == 0:
            errors.append(f"{skill_dir.name}: description must not be empty")
        elif len(desc) > 1024:
            errors.append(f"{skill_dir.name}: description exceeds 1024 characters")

    # compatibility validation (optional field)
    if "compatibility" in data:
        compat = str(data["compatibility"]).strip()
        if len(compat) > 500:
            errors.append(f"{skill_dir.name}: compatibility exceeds 500 characters ({len(compat)})")

    # scripts/ directory
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.is_dir():
        errors.append(f"{skill_dir.name}: Missing scripts/ directory")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate skill format (SKILL.md + scripts/).")
    parser.add_argument("--skills-dir", default=None, help="Path to skills directory (default: cwd/skills)")
    args = parser.parse_args()

    skills_dir = find_skills_dir(args.skills_dir)
    if not skills_dir.exists():
        print(f"Error: skills directory not found: {skills_dir}", file=sys.stderr)
        sys.exit(2)

    all_errors = []
    for sub in sorted(skills_dir.iterdir()):
        if sub.is_dir() and not sub.name.startswith("."):
            all_errors.extend(validate_skill(sub))

    if all_errors:
        for e in all_errors:
            print(e, file=sys.stderr)
        sys.exit(1)
    print("All skills passed format validation.")
    sys.exit(0)


if __name__ == "__main__":
    main()

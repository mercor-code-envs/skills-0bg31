#!/usr/bin/env python3
"""
Skills — Task Structure Validator

Checks every structural requirement for a task directory.
Exits 0 if all required checks pass, 1 if any errors are found.

Usage:
    Expert format (default — environment/ with Dockerfile + skills/ + setup.sh, task.toml):
    Expert format (default — environment/ with Dockerfile + skills/ + setup.sh, task.toml):
        python validate_task.py --task-path tasks/my-task
        python validate_task.py --task-name my-task

    Delivery format (skills/ at root, input_files/, no environment/ or task.toml):
        python validate_task.py --task-path tasks/my-task --delivery
        python validate_task.py --task-name my-task --delivery
"""
import argparse
import ast
import json
import math
import re
import stat
import subprocess
import sys
from collections import Counter
from pathlib import Path

try:
    from allowed_packages import PIP_PACKAGES as _PIP_ALLOWED, APT_PACKAGES as _APT_ALLOWED
except ImportError:
    _PIP_ALLOWED: set[str] = set()
    _APT_ALLOWED: set[str] = set()

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

try:
    from skills_ref import validate as _skills_ref_validate
    _SKILLS_REF_AVAILABLE = True
except ImportError:
    _SKILLS_REF_AVAILABLE = False


# ---------------------------------------------------------------------------
# Output helpers — Errors: format the CI parser expects
# ---------------------------------------------------------------------------

errors: list[str] = []
warnings: list[str] = []


def error(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def print_results(task_name: str) -> None:
    if not errors and not warnings:
        print(f"All checks passed for task: {task_name}")
        return
    if errors:
        print("Errors:")
        for e in errors:
            print(f"  - {e}")
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  - {w}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        error(f"{path.name} is not valid JSON: {e}")
        return None
    except FileNotFoundError:
        return None


def parse_frontmatter(skill_md: Path) -> dict:
    """Extract YAML frontmatter key-value pairs from a SKILL.md file.
    Handles both single-line (key: value) and block scalar (key: >) formats.
    """
    text = skill_md.read_text()
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fields: dict = {}
    lines = parts[1].splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            i += 1
            continue
        k, _, v = stripped.partition(":")
        key = k.strip()
        val = v.strip().strip('"').strip("'").strip()
        if val in (">", "|", ">-", "|-", ">+", "|+") and key:
            # Block scalar — collect following indented lines
            fold = val.startswith(">")
            block_lines = []
            i += 1
            while i < len(lines):
                bl = lines[i]
                if bl and not bl[0].isspace():
                    break
                block_lines.append(bl.strip())
                i += 1
            val = (" " if fold else "\n").join(ln for ln in block_lines if ln)
        else:
            i += 1
        if key and key not in fields:
            fields[key] = val
    return fields


# Valid skill name: 1-64 chars, lowercase letters/numbers/hyphens,
# no leading/trailing/consecutive hyphens
SKILL_NAME_RE = re.compile(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$')


def is_valid_skill_name(name: str) -> bool:
    if not name or len(name) > 64:
        return False
    if "--" in name:
        return False
    return bool(SKILL_NAME_RE.match(name))


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def check_metadata(task_dir: Path, task_name: str, delivery: bool = False) -> dict | None:
    """Validate metadata.json schema and cross-references."""
    meta_path = task_dir / "metadata.json"
    if not meta_path.exists():
        error("metadata.json is missing")
        return None

    meta = load_json(meta_path)
    if meta is None:
        return None

    required = ["task_name", "category", "golden_skills", "distractor_skills"]
    if delivery:
        required += ["input_files"]
    _ALLOWED_META_FIELDS = set(required) | {"input_files", "test_file", "solution_file"}
    for field in required:
        if field not in meta:
            error(f"metadata.json missing required field: '{field}'")
    for field in meta:
        if field not in _ALLOWED_META_FIELDS:
            error(f"metadata.json contains unexpected field: '{field}'")

    if meta.get("task_name") and meta["task_name"] != task_name:
        error(
            f"metadata.json task_name '{meta['task_name']}' "
            f"does not match directory name '{task_name}'"
        )

    golden = meta.get("golden_skills", [])
    if not isinstance(golden, list) or len(golden) < 2:
        error(f"metadata.json golden_skills must have at least 2 items (got {len(golden) if isinstance(golden, list) else 0})")

    distractors = meta.get("distractor_skills", [])
    if not isinstance(distractors, list):
        error("metadata.json distractor_skills must be an array")
    elif len(distractors) < 3:
        error(f"metadata.json distractor_skills must have at least 3 items (got {len(distractors)})")
    elif len(distractors) > 5:
        error(f"metadata.json distractor_skills has {len(distractors)} items; maximum is 5")
    elif isinstance(golden, list) and len(distractors) < len(golden):
        error(
            f"metadata.json distractor_skills ({len(distractors)}) must be >= "
            f"golden_skills ({len(golden)})"
        )

    # A skill must appear in exactly one list — not both
    if isinstance(golden, list) and isinstance(distractors, list):
        overlap = set(golden) & set(distractors)
        for name in sorted(overlap):
            error(f"metadata.json '{name}' appears in both golden_skills and distractor_skills")

    if not isinstance(meta.get("input_files", []), list):
        error("metadata.json input_files must be an array")

    return meta


_EXPERT_ALLOWED = {"metadata.json", "instruction.md", "tests", "solution", "task.toml", "environment"}
_DELIVERY_ALLOWED = {"metadata.json", "instruction.md", "setup.sh", "tests", "solution", "input_files", "skills"}

_PACKAGE_INSTALL_RE = re.compile(
    r'^\s*(?!#)'
    r'(apt(?:-get)?\s+install|pip3?\s+install|conda\s+install|brew\s+install|npm\s+install|yarn\s+add)'
    r'([^\n]*)',
    re.MULTILINE,
)

_PKG_NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')


def _normalize_pkg(name: str) -> str:
    """Normalize a package name for allowlist comparison (lowercase, _ → -)."""
    return name.lower().replace('_', '-')


def _extract_packages(args_str: str) -> list[str]:
    """Extract bare package names from the argument string of an install command.

    Strips flags (starting with -), version specifiers ([...], ==, >=, etc.),
    stops at shell operators (&&, ||, ;, |), and returns only package names.
    """
    pkgs = []
    for token in args_str.split():
        if token in ('&&', '||', ';', '|', '\\'):
            break  # stop at shell operators
        if token.startswith('-'):
            continue  # skip flags like -y, --no-cache-dir
        if token.startswith('/'):
            continue  # skip absolute paths (e.g. /var/lib/apt/lists/*)
        # Strip extras and version specifiers: pkg[extra]==1.0 → pkg
        name = re.split(r'[\[=!<>~@]', token)[0]
        if _PKG_NAME_RE.match(name):
            pkgs.append(name)
    return pkgs


def _check_no_package_installs(setup_sh: Path, label: str) -> None:
    """Fail if setup.sh installs packages not in the pre-installed allowlist."""
    raw = setup_sh.read_text(errors="replace")
    # Join line continuations so multi-line install commands are a single line
    text = re.sub(r'\\\n', ' ', raw)
    # Remove comment lines to avoid false positives
    text = '\n'.join(
        line for line in text.splitlines()
        if not line.lstrip().startswith('#')
    )
    for match in _PACKAGE_INSTALL_RE.finditer(text):
        cmd = match.group(1).strip()
        args = match.group(2)
        pkgs = _extract_packages(args)

        # Determine which allowlist to use
        if cmd.startswith('apt'):
            allowlist = _APT_ALLOWED
        elif 'pip' in cmd:
            allowlist = _PIP_ALLOWED
        else:
            # conda, brew, npm, yarn — no allowlist; always flag
            allowlist = set()

        non_allowed = [p for p in pkgs if _normalize_pkg(p) not in allowlist]
        if non_allowed or not pkgs:
            # Flag if any package is not allowlisted, or if we couldn't parse packages
            # (e.g. -r requirements.txt) assume non-allowed
            if not pkgs:
                error(
                    f"{label} contains a package install command '{cmd}' — "
                    "all dependencies must be pre-installed in the base image"
                )
            else:
                error(
                    f"{label} contains a package install command '{cmd}' with "
                    f"non-allowlisted package(s): {', '.join(non_allowed)} — "
                    "all dependencies must be pre-installed in the base image"
                )


def check_required_files(task_dir: Path, meta: dict | None, delivery: bool = False) -> None:
    """Check required top-level files and directories exist."""
    # Unexpected top-level items
    allowed = _DELIVERY_ALLOWED if delivery else _EXPERT_ALLOWED
    for item in sorted(task_dir.iterdir()):
        if item.name.startswith("."):
            continue
        if item.name not in allowed:
            error(f"unexpected top-level item: {item.name}")

    if not (task_dir / "instruction.md").exists():
        error("instruction.md is missing")

    if not delivery:
        # Expert format: setup.sh lives inside environment/ (Docker build context)
        env_dir = task_dir / "environment"
        if not env_dir.exists():
            error("environment/ directory is missing")
        else:
            if not (env_dir / "Dockerfile").exists():
                error("environment/Dockerfile is missing")
            setup_sh = env_dir / "setup.sh"
            if not setup_sh.exists():
                error("environment/setup.sh is missing")
            else:
                first_line = setup_sh.read_bytes().split(b"\n", 1)[0].rstrip(b"\r")
                if first_line != b"#!/bin/bash":
                    error(f"environment/setup.sh shebang must be '#!/bin/bash', got '{first_line.decode(errors='replace')}'")
                setup_text = setup_sh.read_text(errors="replace")
                has_oracle_ref = "oracle/" in setup_text or "oracle\\" in setup_text
                has_oracle_cleanup = (
                    "rm -rf ../oracle" in setup_text
                    or "rm -rf oracle" in setup_text
                    or "rm -rf ./oracle" in setup_text
                )
                if has_oracle_ref and not has_oracle_cleanup:
                    error("environment/setup.sh references oracle/ but does not remove it — add 'rm -rf ../oracle' after running oracle scripts")
                _check_no_package_installs(setup_sh, "environment/setup.sh")
    else:
        # Delivery format: setup.sh at task root
        setup_sh = task_dir / "setup.sh"
        if not setup_sh.exists():
            error("setup.sh is missing")
        else:
            first_line = setup_sh.read_bytes().split(b"\n", 1)[0].rstrip(b"\r")
            if first_line != b"#!/bin/bash":
                error(f"setup.sh shebang must be '#!/bin/bash', got '{first_line.decode(errors='replace')}'")
            setup_text = setup_sh.read_text(errors="replace")
            has_oracle_ref = "oracle/" in setup_text or "oracle\\" in setup_text
            has_oracle_cleanup = (
                "rm -rf ../oracle" in setup_text
                or "rm -rf oracle" in setup_text
                or "rm -rf ./oracle" in setup_text
            )
            if has_oracle_ref and not has_oracle_cleanup:
                error("setup.sh references oracle/ but does not remove it — add 'rm -rf ../oracle' after running oracle scripts")
            _check_no_package_installs(setup_sh, "setup.sh")
    if not (task_dir / "instruction.md").exists():
        error("instruction.md is missing")

    if not delivery:
        # Expert format: setup.sh lives inside environment/ (Docker build context)
        env_dir = task_dir / "environment"
        if not env_dir.exists():
            error("environment/ directory is missing")
        else:
            if not (env_dir / "Dockerfile").exists():
                error("environment/Dockerfile is missing")
            setup_sh = env_dir / "setup.sh"
            if not setup_sh.exists():
                error("environment/setup.sh is missing")
            else:
                first_line = setup_sh.read_bytes().split(b"\n", 1)[0].rstrip(b"\r")
                if first_line != b"#!/bin/bash":
                    error(f"environment/setup.sh shebang must be '#!/bin/bash', got '{first_line.decode(errors='replace')}'")
                setup_text = setup_sh.read_text(errors="replace")
                has_oracle_ref = "oracle/" in setup_text or "oracle\\" in setup_text
                has_oracle_cleanup = (
                    "rm -rf ../oracle" in setup_text
                    or "rm -rf oracle" in setup_text
                    or "rm -rf ./oracle" in setup_text
                )
                if has_oracle_ref and not has_oracle_cleanup:
                    error("environment/setup.sh references oracle/ but does not remove it — add 'rm -rf ../oracle' after running oracle scripts")
                _check_no_package_installs(setup_sh, "environment/setup.sh")
    else:
        # Delivery format: setup.sh at task root
        setup_sh = task_dir / "setup.sh"
        if not setup_sh.exists():
            error("setup.sh is missing")
        else:
            first_line = setup_sh.read_bytes().split(b"\n", 1)[0].rstrip(b"\r")
            if first_line != b"#!/bin/bash":
                error(f"setup.sh shebang must be '#!/bin/bash', got '{first_line.decode(errors='replace')}'")
            setup_text = setup_sh.read_text(errors="replace")
            has_oracle_ref = "oracle/" in setup_text or "oracle\\" in setup_text
            has_oracle_cleanup = (
                "rm -rf ../oracle" in setup_text
                or "rm -rf oracle" in setup_text
                or "rm -rf ./oracle" in setup_text
            )
            if has_oracle_ref and not has_oracle_cleanup:
                error("setup.sh references oracle/ but does not remove it — add 'rm -rf ../oracle' after running oracle scripts")
            _check_no_package_installs(setup_sh, "setup.sh")

    test_py = task_dir / "tests" / "test.py"
    if not test_py.exists():
        error("tests/test.py is missing")
    else:
        _check_test_py(task_dir, test_py, delivery=delivery)

    if not (task_dir / "solution" / "solve.sh").exists():
        error("solution/solve.sh is missing")

    if delivery:
        input_folder = meta.get("input_files_folder", "input_files") if meta else "input_files"
        input_dir = task_dir / input_folder
        if not input_dir.exists():
            error(f"input files folder '{input_folder}/' is missing")


_ENTRY_POINT_BLOCK = (
    'if __name__ == "__main__":\n'
    '    exit_code = pytest.main([__file__, "-rA"])\n'
    '    print("pass" if exit_code == 0 else "fail")\n'
    '    sys.exit(exit_code)'
)


def _check_test_py(task_dir: Path, test_py: Path, delivery: bool = False) -> None:
    """Check tests/test.py is self-contained and runnable via pytest."""
    # Self-contained: no delegation to test_outputs.py or other helpers
    if (task_dir / "tests" / "test_outputs.py").exists():
        error("tests/test_outputs.py must not exist — tests/test.py must be self-contained")

    content = test_py.read_text(errors="replace")
    if "test_outputs" in content:
        error("tests/test.py references test_outputs — must be self-contained with no delegated files")

    # Expert format requires tests/test.sh wrapper; delivery format must not have it
    test_sh = task_dir / "tests" / "test.sh"
    if not delivery:
        if not test_sh.exists():
            error("tests/test.sh is missing — expert format requires a test.sh wrapper that runs test.py and writes reward.txt")
    else:
        if test_sh.exists():
            error("tests/test.sh must not exist in delivery format — tests/test.py must be self-contained")

    # Required imports for entry point
    try:
        tree = ast.parse(content)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        if "pytest" not in imported:
            error("tests/test.py does not import pytest — required for pytest format")
        if "sys" not in imported:
            error("tests/test.py does not import sys — required for the entry point block")

        # At least one test function or test class
        has_test = any(
            (isinstance(node, ast.FunctionDef) and node.name.startswith("test_"))
            or (isinstance(node, ast.ClassDef) and node.name.startswith("Test"))
            for node in ast.walk(tree)
        )
        if not has_test:
            error("tests/test.py has no test functions (def test_*) or test classes (class Test*)")

    except SyntaxError:
        pass  # syntax errors caught by the pytest run below

    # Required entry point block
    if _ENTRY_POINT_BLOCK not in content:
        error(
            'tests/test.py is missing the required entry point block:\n'
            '    if __name__ == "__main__":\n'
            '        exit_code = pytest.main([__file__, "-rA"])\n'
            '        print("pass" if exit_code == 0 else "fail")\n'
            '        sys.exit(exit_code)'
        )

    # Runnable: pytest must not exit with error code 2+ (import/syntax errors)
    # Exit code 0 = all pass, 1 = some failures (expected), 2+ = collection/import error
    # Output must contain a pytest summary line like "3 passed, 1 failed in 0.42s"
    _PYTEST_SUMMARY_RE = re.compile(r'\d+\s+(passed|failed|error)')
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", str(test_py), "--tb=short", "-q", "--no-header"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=task_dir,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode >= 2:
            first_error = next((l for l in output.splitlines() if "ERROR" in l or "error" in l.lower()), output[:200])
            error(f"tests/test.py fails to collect/run via pytest (exit {result.returncode}): {first_error}")
        elif not _PYTEST_SUMMARY_RE.search(output):
            error(
                "tests/test.py did not produce a pytest summary line — "
                "expected output like '3 passed, 1 failed in 0.42s'"
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Skip if pytest not available or timeout


_TEST_PATH_RE = re.compile(r'(^|/)tests?/')


def _is_test_path(p: str) -> bool:
    """Return True if the path starts with or contains a tests/ or test/ component."""
    return bool(_TEST_PATH_RE.search(p.replace("\\", "/") + "/"))


def check_input_files_exist(task_dir: Path, meta: dict) -> None:
    """Bidirectional check: declared input_files ↔ actual files in input_files/.

    1. Every file declared in metadata.json must exist on disk.
    2. Every top-level entry in input_files/ must be declared in metadata.json.
    3. Neither declared entries nor on-disk top-level names may be tests/ or test/.
    """
    input_folder = meta.get("input_files_folder", "input_files")
    input_dir = task_dir / input_folder
    declared = set(meta.get("input_files", []))

    # Check declared entries for test paths
    for fname in declared:
        if _is_test_path(fname):
            error(f"input_files entry '{fname}' references a tests/ or test/ path — test files must not be listed as input_files")

    # Direction 1: declared → disk
    for fname in declared:
        if not (input_dir / fname).exists():
            error(f"input_files entry '{fname}' not found in {input_folder}/")

    # Direction 2: disk → declared
    if input_dir.exists():
        on_disk = {
            p.name for p in input_dir.iterdir()
            if not p.name.startswith(".")
        }
        undeclared = {
            name for name in on_disk
            if name not in declared
            and not any(d == name or d.startswith(name + "/") for d in declared)
        }
        for fname in sorted(undeclared):
            error(f"'{input_folder}/{fname}' exists on disk but is not listed in metadata.json input_files")

        # Check on-disk top-level entries for test directories
        for fname in sorted(on_disk):
            if _is_test_path(fname):
                error(f"'{input_folder}/{fname}' is a tests/ or test/ directory — test files must not be in {input_folder}/")

    # Oracle check: if input_files/oracle/ exists, setup.sh must remove it
    oracle_dir = input_dir / "oracle"
    if oracle_dir.exists():
        setup_sh = task_dir / "setup.sh"
        if setup_sh.exists():
            setup_text = setup_sh.read_text(errors="replace")
            if "rm -rf ../oracle" not in setup_text and "rm -rf oracle" not in setup_text and "rm -rf ./oracle" not in setup_text:
                error("input_files/oracle/ exists but setup.sh does not remove it — setup.sh must contain 'rm -rf ../oracle'")
        else:
            error("input_files/oracle/ exists but setup.sh is missing — setup.sh must remove oracle/ after use")



# Expected Dockerfile content for expert format tasks
_EXPECTED_DOCKERFILE = """FROM public.ecr.aws/k4t1e3r5/skill-base:latest

WORKDIR /
COPY skills /root/.claude/skills
COPY skills /root/skills
COPY . /
COPY setup.sh /tmp/
RUN chmod +x /tmp/setup.sh && /tmp/setup.sh
"""


def check_dockerfile(task_dir: Path) -> None:
    """Enforce that environment/Dockerfile matches the standard template exactly."""
    dockerfile = task_dir / "environment" / "Dockerfile"
    if not dockerfile.exists():
        return  # already caught by check_required_files
    actual = dockerfile.read_text()
    if actual != _EXPECTED_DOCKERFILE:
        error(
            "environment/Dockerfile does not match the required standard template — "
            "it must be identical to the skill-base template "
            "(FROM public.ecr.aws/k4t1e3r5/skill-base:latest, COPY skills /root/.claude/skills, "
            "COPY skills /root/skills, COPY . /, COPY setup.sh, RUN setup.sh)"
        )


_SIM_FLOOR = 0.60
_SIM_CEILING = 0.90


def _desc_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z0-9]{1,}", text.lower())


def _cosine_sim(a: list[str], b: list[str]) -> float:
    ca, cb = Counter(a), Counter(b)
    common = set(ca) & set(cb)
    dot = sum(ca[t] * cb[t] for t in common)
    mag = math.sqrt(sum(v * v for v in ca.values())) * math.sqrt(sum(v * v for v in cb.values()))
    return dot / mag if mag else 0.0


def _get_description(skill_dir: Path) -> str:
    """Extract the description field value from a skill's SKILL.md frontmatter."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return ""
    fields = parse_frontmatter(skill_md)
    return str(fields.get("description", ""))


def check_skill_similarity(task_dir: Path, meta: dict, delivery: bool = False) -> None:
    """For each distractor, its description must score >= 0.60 with at least one golden
    description, and must not score >= 0.90 with any golden description."""
    skills_dir = task_dir / "skills" if delivery else task_dir / "environment" / "skills"
    label = "skills" if delivery else "environment/skills"
    golden = meta.get("golden_skills", [])
    distractors = meta.get("distractor_skills", [])

    golden_tokens = {g: _desc_tokens(_get_description(skills_dir / g)) for g in golden}

    for d_name in distractors:
        d_tokens = _desc_tokens(_get_description(skills_dir / d_name))
        if not d_tokens:
            continue
        sims = {
            g_name: _cosine_sim(d_tokens, g_tok)
            for g_name, g_tok in golden_tokens.items()
            if g_tok
        }
        if not sims:
            continue
        max_sim_name = max(sims, key=lambda k: sims[k])
        max_sim = sims[max_sim_name]
        if max_sim >= _SIM_CEILING:
            error(
                f"{label}/{d_name} description is too similar to {label}/{max_sim_name} "
                f"({max_sim:.2f} >= {_SIM_CEILING}) — distractor may leak golden skill content"
            )
        elif max_sim < _SIM_FLOOR:
            error(
                f"{label}/{d_name} description has no sufficiently similar golden skill "
                f"(max score {max_sim:.2f} < {_SIM_FLOOR}) — distractor must score >= {_SIM_FLOOR} "
                "against at least one golden skill description"
            )


def check_skills(task_dir: Path, meta: dict | None, delivery: bool = False) -> None:
    """Validate skills/ directory structure and SKILL.md frontmatter."""
    if delivery:
        skills_dir = task_dir / "skills"
        skills_label = "skills"
    else:
        skills_dir = task_dir / "environment" / "skills"
        skills_label = "environment/skills"
    if not skills_dir.exists():
        error(f"{skills_label}/ directory is missing")
        return

    all_declared = []
    if meta:
        all_declared = (
            list(meta.get("golden_skills", [])) +
            list(meta.get("distractor_skills", []))
        )

    # All declared skills must exist
    for skill_name in all_declared:
        if not (skills_dir / skill_name).exists():
            error(f"{skills_label}/{skill_name}/ is missing (declared in metadata.json)")

    # All existing skill dirs must be declared in exactly one list
    actual_skill_dirs = [d.name for d in skills_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    for skill_name in actual_skill_dirs:
        if skill_name not in all_declared:
            error(
                f"{skills_label}/{skill_name}/ exists but is not listed in "
                "golden_skills or distractor_skills in metadata.json"
            )

    for skill_name in actual_skill_dirs:
        _check_single_skill(skills_dir / skill_name, skill_name, skills_label=skills_label)



# ---------------------------------------------------------------------------
# Script syntax checks
# ---------------------------------------------------------------------------

def _check_scripts_syntax(scripts_dir: Path, prefix: str) -> None:
    """Syntax-check every script in skills/<name>/scripts/ without executing it.

    For .sh files: checks bash syntax (bash -n) and executable bit.
    For .py files: checks Python syntax (py_compile) and import ordering.
    """
    for script in sorted(scripts_dir.iterdir()):
        if script.suffix == ".sh":
            # Executable bit required for shell scripts
            mode = script.stat().st_mode
            if not (mode & stat.S_IXUSR):
                error(f"{prefix}/scripts/{script.name} is not executable — run: chmod +x {script.name}")
            result = subprocess.run(
                ["bash", "-n", str(script)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                first_line = result.stderr.strip().splitlines()[0] if result.stderr.strip() else "syntax error"
                error(f"{prefix}/scripts/{script.name} has bash syntax errors: {first_line}")
        elif script.suffix == ".py":
            result = subprocess.run(
                ["python3", "-m", "py_compile", str(script)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                first_line = result.stderr.strip().splitlines()[0] if result.stderr.strip() else "syntax error"
                error(f"{prefix}/scripts/{script.name} has Python syntax errors: {first_line}")
            else:
                _check_python_import_order(script, prefix)


def _check_python_import_order(script: Path, prefix: str) -> None:
    """Check that all imports appear before non-import, non-docstring code."""
    try:
        tree = ast.parse(script.read_text())
    except SyntaxError:
        return  # already caught by py_compile
    hit_non_import = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if hit_non_import:
                error(
                    f"{prefix}/scripts/{script.name} has import on line {node.lineno} "
                    "after non-import code — move all imports to the top of the file"
                )
                return
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            pass  # module-level docstring
        else:
            hit_non_import = True


# ---------------------------------------------------------------------------

_SKILL_ROOT_ALLOWED = {"SKILL.md", "scripts", "references", "assets"}


def _check_single_skill(skill_dir: Path, skill_name: str, skills_label: str = "skills") -> None:
    prefix = f"{skills_label}/{skill_name}"

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        error(f"{prefix}/SKILL.md is missing")
    else:
        _check_skill_md(skill_md, skill_name, prefix)

    # Run skills-ref library validation
    if _SKILLS_REF_AVAILABLE:
        for problem in _skills_ref_validate(skill_dir):
            error(f"{prefix}: skills-ref: {problem}")
    else:
        error(f"{prefix}: skills-ref library is not installed — run: pip install skills-ref")

    if (skill_dir / "scripts").exists():
        _check_scripts_syntax(skill_dir / "scripts", prefix)

    # Skill root must contain only SKILL.md, scripts/, and optionally references/, assets/
    for item in sorted(skill_dir.iterdir()):
        if item.name.startswith("."):
            continue
        if item.name in {"__pycache__"} or item.name.endswith(".pyc"):
            continue
        if item.name not in _SKILL_ROOT_ALLOWED:
            error(
                f"{prefix}/{item.name} is not allowed at the skill root — "
                "skill directory may only contain: SKILL.md, scripts/, references/, assets/"
            )


_INSTRUCTION_FORBIDDEN = [
    "You have access to skill files",
    "/workspace/skills/",
]


_PATH_RE = re.compile(r'\b([a-zA-Z0-9_.+-]+(?:/[a-zA-Z0-9_.+-]+)+)\b')


# Nudge text that must appear verbatim at the very start of instruction.md.
# {skill_path} is replaced at runtime with an actual path — match any non-whitespace token.
_NUDGE_RE = re.compile(
    r'^The documentation and scripts in \S+ are useful for high-level repeated workflows '
    r'such as common tool usage or calling external APIs, etc that would otherwise be error-prone\. '
    r'Prioritize using existing scripts when possible and only write custom solutions when truly necessary\.\n'
    r'\nNever use a script without reading its documentation first\. '
    r'All subdirectories have a SKILL\.md file with documentation which you must read before '
    r'using the scripts in such subdirectories\.'
)

# Anchored version used to detect the nudge elsewhere in the file (not at position 0)
_NUDGE_BODY_RE = re.compile(
    r'The documentation and scripts in \S+ are useful for high-level repeated workflows'
)


def check_instruction_md(task_dir: Path, meta: dict | None = None, delivery: bool = False) -> None:
    """Check instruction.md does not reference skills, forbidden paths, or mismatched input paths."""
    instruction = task_dir / "instruction.md"
    if not instruction.exists():
        return  # already caught by check_required_files
    text = instruction.read_text(errors="replace")

    # Nudge text must appear at the very beginning of instruction.md
    if not _NUDGE_RE.match(text):
        error(
            "instruction.md is missing the required nudge text at the start, or it is not at position 0 — "
            "instruction.md must begin with: "
            "'The documentation and scripts in <skill_path> are useful for high-level repeated workflows...'"
        )
    else:
        # Must not appear a second time anywhere else in the file
        matches = list(_NUDGE_BODY_RE.finditer(text))
        if len(matches) > 1:
            error(
                "instruction.md contains the nudge text more than once — "
                "it must appear exactly at the beginning and nowhere else"
            )

    # Forbidden phrases
    for phrase in _INSTRUCTION_FORBIDDEN:
        if phrase in text:
            error(
                f"instruction.md contains forbidden phrase '{phrase}' — "
                "task prompt must not reference skill files or the skills workspace"
            )

    # No golden or distractor skill names mentioned
    if meta:
        all_skills = list(meta.get("golden_skills", [])) + list(meta.get("distractor_skills", []))
        for skill_name in all_skills:
            if skill_name in text:
                error(
                    f"instruction.md mentions skill name '{skill_name}' — "
                    "task prompt must not reference skill names"
                )

    # No SKILL.md body excerpts (8-word n-gram match)
    if meta:
        skills_dir = task_dir / "skills" if delivery else task_dir / "environment" / "skills"
        all_skill_names = list(meta.get("golden_skills", [])) + list(meta.get("distractor_skills", []))
        skill_ngrams: set[tuple[str, ...]] = set()
        for skill_name in all_skill_names:
            skill_md = skills_dir / skill_name / "SKILL.md"
            if skill_md.exists():
                raw = skill_md.read_text(errors="replace")
                # Extract body only (after closing ---)
                parts = raw.split("---", 2)
                body = parts[2] if len(parts) >= 3 else raw
                words = body.lower().split()
                skill_ngrams |= {tuple(words[i:i+8]) for i in range(len(words) - 7)}
        if skill_ngrams:
            inst_words = text.lower().split()
            inst_ngrams = {tuple(inst_words[i:i+8]) for i in range(len(inst_words) - 7)}
            if skill_ngrams & inst_ngrams:
                error("instruction.md contains an excerpt from a SKILL.md body — task prompt must not include skill content")

    # Check: for each declared input_file, if its name appears in instruction.md,
    # ensure the path used in the instruction matches the declared name exactly.
    if delivery and meta:
        declared_inputs = meta.get("input_files", [])
        declared_set = set(declared_inputs)
        for fname in declared_inputs:
            basename = fname.rsplit("/", 1)[-1]
            if basename in text or fname in text:
                for match in _PATH_RE.finditer(text):
                    candidate = match.group(1)
                    # Flag only if candidate shares the basename but is not declared at all
                    if candidate.rsplit("/", 1)[-1] == basename and candidate != fname and candidate not in declared_set:
                        error(
                            f"instruction.md references '{candidate}' but metadata.json declares "
                            f"this file as '{fname}' — paths must match"
                        )



def _check_skill_md(skill_md: Path, skill_name: str, prefix: str) -> None:
    """
    Validate SKILL.md frontmatter per skill spec:
    - name: required, 1-64 chars, lowercase alphanumeric + hyphens, matches directory name
    - description: required, 1-1024 chars
    """
    raw = skill_md.read_bytes()

    # Error 3: CRLF line endings break YAML frontmatter parsers
    if b"\r\n" in raw:
        error(f"{prefix}/SKILL.md has Windows CRLF line endings — convert to LF")

    text = raw.decode("utf-8", errors="replace")

    # Validate frontmatter with PyYAML — same parser Gemini's eval harness uses.
    # Catches unquoted colons, duplicate keys, and other strict YAML violations
    # that our lenient regex parser silently ignores.
    if _YAML_AVAILABLE and text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                _yaml.safe_load(parts[1])
            except _yaml.YAMLError as e:
                # Extract just the problem line for a compact error message
                problem = str(e).split("\n")[0]
                error(f"{prefix}/SKILL.md frontmatter is not valid YAML: {problem}")
                return  # downstream checks will be noise if YAML is broken

    # Error 2: unquoted description value containing a bare colon causes YAML parse failure
    # Match: description: <value-not-quoted-and-not-block-scalar> that contains ':'
    _unquoted_colon_desc = re.search(
        r'^description\s*:\s*(?!["\'>|])([^\n]+:[^\n]+)$',
        text.split("---", 2)[1] if text.startswith("---") and text.count("---") >= 2 else "",
        re.MULTILINE,
    )
    if _unquoted_colon_desc:
        error(
            f"{prefix}/SKILL.md description contains an unquoted colon — "
            "wrap the value in double quotes or use a block scalar (>)"
        )

    fields = parse_frontmatter(skill_md)

    if not fields:
        error(f"{prefix}/SKILL.md has no valid YAML frontmatter (must start with ---)")
        return

    # name field
    name = fields.get("name", "")
    if not name:
        error(f"{prefix}/SKILL.md missing required frontmatter field: 'name'")
    else:
        if len(name) > 64:
            error(f"{prefix}/SKILL.md name is {len(name)} chars; maximum is 64")
        if not is_valid_skill_name(name):
            error(
                f"{prefix}/SKILL.md name '{name}' is invalid — "
                "must be lowercase letters, numbers, hyphens only; "
                "no leading/trailing/consecutive hyphens"
            )
        elif name != skill_name:
            error(f"{prefix}/SKILL.md name '{name}' does not match directory name '{skill_name}'")

    # description field
    desc = fields.get("description", "")
    if not desc:
        error(f"{prefix}/SKILL.md missing required frontmatter field: 'description'")
    elif len(desc) > 1024:
        error(f"{prefix}/SKILL.md description is {len(desc)} chars; maximum is 1024")
    if desc and ":" in desc:
        error(f"{prefix}/SKILL.md description contains a colon — rewrite to avoid ':' in descriptions")
    if desc and ("<" in desc or ">" in desc):
        error(f"{prefix}/SKILL.md description contains angle brackets — avoid <> in frontmatter descriptions")

    # size limits
    lines = text.splitlines()
    if len(lines) > 500:
        error(f"{prefix}/SKILL.md is {len(lines)} lines; maximum is 500")

    fm_end = text.find("\n---", 3)
    if fm_end != -1:
        fm_words = len(text[3:fm_end].split())
        if fm_words > 100:
            error(f"{prefix}/SKILL.md frontmatter is {fm_words} words; maximum is 100")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def validate(task_path: Path, delivery: bool = False) -> bool:
    task_name = task_path.name

    if not task_path.exists():
        error(f"Task directory does not exist: {task_path}")
        print_results(task_name)
        return False

    meta = check_metadata(task_path, task_name, delivery=delivery)
    check_required_files(task_path, meta, delivery=delivery)

    if not delivery:
        check_dockerfile(task_path)

    if not delivery:
        check_dockerfile(task_path)

    if meta and delivery:
        check_input_files_exist(task_path, meta)

    check_skills(task_path, meta, delivery=delivery)

    if meta:
        check_skill_similarity(task_path, meta, delivery=delivery)

    check_instruction_md(task_path, meta=meta, delivery=delivery)

    # Check all task files for app/ or workspace/ path references
    skip_dirs = {".git", "__pycache__", "node_modules"}
    for fpath in task_path.rglob("*"):
        if fpath.is_dir() or any(d in fpath.parts for d in skip_dirs):
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = fpath.relative_to(task_path)
        for match in _PATH_RE.finditer(text):
            candidate = match.group(1)
            if candidate.startswith("app/") or candidate.startswith("workspace/"):
                error(
                    f"{rel}: references path '{candidate}' — app/ and workspace/ paths are not allowed in any task file"
                )

    print_results(task_name)
    return len(errors) == 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a Skills task directory",
        epilog=(
            "Examples:\n"
            "  Expert format (default):\n"
            "    python validate_task.py --task-path tasks/my-task\n"
            "    python validate_task.py --task-name my-task\n"
            "\n"
            "  Delivery format (input_files/, skills/ at root):\n"
            "    python validate_task.py --task-path tasks/my-task --delivery\n"
            "    python validate_task.py --task-name my-task --delivery\n"
            "\n"
            "Format differences:\n"
            "  Expert:   Dockerfile + setup.sh + skills/ inside environment/, has task.toml, no input_files/\n"
            "  Expert:   Dockerfile + setup.sh + skills/ inside environment/, has task.toml, no input_files/\n"
            "  Delivery: skills at skills/, has input_files/, no environment/ or task.toml"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task-path", type=Path, help="Direct path to task directory")
    group.add_argument("--task-name", type=str, help="Task name (looked up under tasks/)")
    parser.add_argument(
        "--delivery", action="store_true", default=False,
        help="Validate in delivery format (input_files/, skills/ at root). "
             "Default (no flag) is expert format (environment/skills/, no input_files/).",
    )
    args = parser.parse_args()

    task_path = (
        args.task_path if args.task_path
        else Path(__file__).parent.parent / "tasks" / args.task_name
    )

    sys.exit(0 if validate(task_path, delivery=args.delivery) else 1)


if __name__ == "__main__":
    main()

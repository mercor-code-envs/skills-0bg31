#!/usr/bin/env python3
"""
Skills — Task Structure Validator

Checks every structural requirement for a task directory.
Exits 0 if all required checks pass, 1 if any errors are found.

Usage:
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
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


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

    required = ["task_name", "category", "golden_skills", "distractor_skills", "failure_modes"]
    if delivery:
        required += ["input_files"]
    for field in required:
        if field not in meta:
            error(f"metadata.json missing required field: '{field}'")

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

    # Check failure_modes is present, has required keys, and is properly filled
    failure_modes = meta.get("failure_modes", {})
    if not failure_modes:
        error("metadata.json failure_modes is missing or empty")
    else:
        required_fm_keys = ["gemini-3.1-pro-base", "claude-opus-4-6-base", "claude-opus-4-6-with-skills"]
        for key in required_fm_keys:
            if key not in failure_modes:
                error(f"metadata.json failure_modes missing required key: '{key}'")
        for key, val in failure_modes.items():
            val_str = json.dumps(val) if isinstance(val, dict) else str(val)
            if "TODO" in val_str:
                error(f"metadata.json failure_modes['{key}'] still has TODO — fill in after running evals")
            elif len(val_str.strip('"')) < 150:
                error(f"metadata.json failure_modes['{key}'] is too short (minimum 150 characters) — describe what the agent tried and where it got stuck")

    return meta


_EXPERT_ALLOWED = {"metadata.json", "instruction.md", "tests", "solution", "task.toml", "environment"}
_DELIVERY_ALLOWED = {"metadata.json", "instruction.md", "setup.sh", "tests", "solution", "input_files", "skills"}


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
                    "rm -rf /workspace/oracle" in setup_text
                    or "rm -rf oracle" in setup_text
                    or "rm -rf ./oracle" in setup_text
                )
                if has_oracle_ref and not has_oracle_cleanup:
                    error("environment/setup.sh references oracle/ but does not remove it — add 'rm -rf /workspace/oracle' after running oracle scripts")
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
                "rm -rf /workspace/oracle" in setup_text
                or "rm -rf oracle" in setup_text
                or "rm -rf ./oracle" in setup_text
            )
            if has_oracle_ref and not has_oracle_cleanup:
                error("setup.sh references oracle/ but does not remove it — add 'rm -rf /workspace/oracle' after running oracle scripts")

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
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", str(test_py), "--tb=short", "-q", "--no-header"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=task_dir,
        )
        if result.returncode >= 2:
            # Extract first error line for a compact message
            output = (result.stdout + result.stderr).strip()
            first_error = next((l for l in output.splitlines() if "ERROR" in l or "error" in l.lower()), output[:200])
            error(f"tests/test.py fails to collect/run via pytest (exit {result.returncode}): {first_error}")
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
            if "rm -rf /workspace/oracle" not in setup_text and "rm -r /workspace/oracle" not in setup_text:
                error("input_files/oracle/ exists but setup.sh does not remove it — setup.sh must contain 'rm -rf /workspace/oracle'")
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
# Cosine similarity helpers
# ---------------------------------------------------------------------------

def _skill_tokens(skill_dir: Path) -> list[str]:
    """Return lowercase word tokens from a SKILL.md (description + body, no code blocks)."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return []
    text = skill_md.read_text(errors="replace")
    # Strip fenced code blocks to avoid matching on code syntax
    text = re.sub(r"```[\s\S]*?```", " ", text)
    return re.findall(r"[a-z][a-z0-9]{1,}", text.lower())


def _cosine_sim(a: list[str], b: list[str]) -> float:
    ca, cb = Counter(a), Counter(b)
    common = set(ca) & set(cb)
    dot = sum(ca[t] * cb[t] for t in common)
    mag = math.sqrt(sum(v * v for v in ca.values())) * math.sqrt(sum(v * v for v in cb.values()))
    return dot / mag if mag else 0.0


_SIMILARITY_MAX_THRESHOLD = 0.85
_SIMILARITY_MIN_THRESHOLD = 0.40


def check_skill_similarity(task_dir: Path, meta: dict, delivery: bool = False) -> None:
    """Check cosine similarity between each golden↔distractor SKILL.md pair.

    - Too high (>= 0.85): distractor may leak golden skill content.
    - Too low (< 0.40): distractor is not relevant enough to the golden skill.
    """
    skills_dir = task_dir / "skills" if delivery else task_dir / "environment" / "skills"
    golden = meta.get("golden_skills", [])
    distractors = meta.get("distractor_skills", [])

    skills_label = "skills" if delivery else "environment/skills"
    for g_name in golden:
        g_tokens = _skill_tokens(skills_dir / g_name)
        if not g_tokens:
            continue
        for d_name in distractors:
            d_tokens = _skill_tokens(skills_dir / d_name)
            if not d_tokens:
                continue
            sim = _cosine_sim(g_tokens, d_tokens)
            if sim >= _SIMILARITY_MAX_THRESHOLD:
                warn(
                    f"{skills_label}/{g_name} and {skills_label}/{d_name} have high cosine similarity "
                    f"({sim:.2f} >= {_SIMILARITY_MAX_THRESHOLD}) — distractor may leak "
                    "golden skill content"
                )
            elif sim < _SIMILARITY_MIN_THRESHOLD:
                warn(
                    f"{skills_label}/{g_name} and {skills_label}/{d_name} have low cosine similarity "
                    f"({sim:.2f} < {_SIMILARITY_MIN_THRESHOLD}) — distractor may not be "
                    "relevant enough to the golden skill"
                )

def _check_single_skill(skill_dir: Path, skill_name: str, skills_label: str = "skills") -> None:
    prefix = f"{skills_label}/{skill_name}"

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        error(f"{prefix}/SKILL.md is missing")
    else:
        _check_skill_md(skill_md, skill_name, prefix)

    if (skill_dir / "scripts").exists():
        _check_scripts_syntax(skill_dir / "scripts", prefix)


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

    if meta and delivery:
        check_input_files_exist(task_path, meta)

    check_skills(task_path, meta, delivery=delivery)

    if meta:
        check_skill_similarity(task_path, meta, delivery=delivery)

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

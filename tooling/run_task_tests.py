#!/usr/bin/env python3
"""
Skills — Task Test Runner

Reads the test_file path from metadata.json and runs it with pytest,
reporting pass / fail / skip counts. Does NOT validate task structure —
use validate_task.py for that.

Usage:
    python run_task_tests.py --task-path tasks/my-task
    python run_task_tests.py --task-name my-task         # looks under tasks/
    python run_task_tests.py --task-path tasks/my-task --verbose
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def load_metadata(task_dir: Path) -> dict | None:
    meta_path = task_dir / "metadata.json"
    if not meta_path.exists():
        print(f"ERROR: metadata.json not found in {task_dir}", file=sys.stderr)
        return None
    try:
        return json.loads(meta_path.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: metadata.json is invalid JSON: {e}", file=sys.stderr)
        return None


def parse_pytest_counts(output: str) -> dict[str, int]:
    """Extract passed/failed/error/skipped counts from pytest summary line."""
    counts: dict[str, int] = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    # e.g. "3 passed, 1 failed, 2 skipped in 0.42s"
    for key in counts:
        m = re.search(rf"(\d+)\s+{key}", output)
        if m:
            counts[key] = int(m.group(1))
    return counts


def run_tests(task_path: Path, verbose: bool = False) -> bool:
    meta = load_metadata(task_path)
    if meta is None:
        return False

    task_name = meta.get("task_name", task_path.name)
    test_file_rel = meta.get("test_file", "tests/test.py")
    test_file = task_path / test_file_rel

    if not test_file.exists():
        print(f"ERROR: test file '{test_file_rel}' not found (from metadata.json)")
        return False

    print(f"Task:      {task_name}")
    print(f"Test file: {test_file_rel}")
    print()

    cmd = [
        "python3", "-m", "pytest", str(test_file),
        "--tb=short", "--no-header", "-q",
    ]
    if verbose:
        cmd.append("-v")

    try:
        result = subprocess.run(
            cmd,
            capture_output=not verbose,
            text=True,
            timeout=120,
            cwd=task_path,
        )
    except subprocess.TimeoutExpired:
        print("ERROR: pytest timed out after 120s")
        return False
    except FileNotFoundError:
        print("ERROR: python3 or pytest not found — install pytest first")
        return False

    output = (result.stdout or "") + (result.stderr or "")

    if result.returncode >= 2:
        print("RESULT: COLLECTION ERROR (exit code {})".format(result.returncode))
        print()
        print(output.strip())
        return False

    counts = parse_pytest_counts(output)
    total = counts["passed"] + counts["failed"] + counts["error"] + counts["skipped"]

    status = "PASS" if counts["failed"] == 0 and counts["error"] == 0 else "FAIL"

    print(f"RESULT: {status}")
    print(f"  {counts['passed']} passed  {counts['failed']} failed  "
          f"{counts['error']} errors  {counts['skipped']} skipped  ({total} total)")

    if not verbose and (counts["failed"] > 0 or counts["error"] > 0):
        print()
        # Print only the failure section from pytest output
        lines = output.splitlines()
        in_failures = False
        for line in lines:
            if re.match(r"=+ (FAILURES|ERRORS) =+", line):
                in_failures = True
            if in_failures:
                print(line)
            if in_failures and re.match(r"=+ short test summary =+", line):
                break

    return status == "PASS"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the test file for a Skills task (path from metadata.json)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task-path", type=Path, help="Direct path to task directory")
    group.add_argument("--task-name", type=str, help="Task name (looked up under tasks/)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Pass -v to pytest")
    args = parser.parse_args()

    task_path = (
        args.task_path if args.task_path
        else Path(__file__).parent.parent / "tasks" / args.task_name
    )

    if not task_path.exists():
        print(f"ERROR: task directory does not exist: {task_path}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0 if run_tests(task_path, verbose=args.verbose) else 1)


if __name__ == "__main__":
    main()

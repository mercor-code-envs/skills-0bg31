#!/usr/bin/env python3
"""QC validation for Skills tasks — V3.

1. Packages the task directory as a zip archive
2. Uploads to S3 temporary storage
3. Triggers the LLMaaJ QC validation API (Sections 2-4)
4. Polls for results
5. Writes a single GitHub comment markdown file (qc-comment.md) with the
   full V3 checklist filled out — pass/fail per item
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

QC_API_URL = os.environ.get("VALIDATION_API_URL")
S3_BUCKET = os.environ.get("S3_BUCKET_TEMP")

_PROMPT_PATH = Path(__file__).parent.parent / "qc-prompt.md"

MAX_POLL_TIME = 800
POLL_INTERVAL = 10

PASS = "PASS"
FAIL = "FAIL"

# ---------------------------------------------------------------------------
# Packaging + S3
# ---------------------------------------------------------------------------

def package_task(task_dir: Path, task_name: str) -> Path:
    archive_path = task_dir.parent / f"{task_name}.zip"
    if archive_path.exists():
        archive_path.unlink()

    result = subprocess.run(
        ["zip", "-r", str(archive_path), f"{task_name}/"],
        cwd=task_dir.parent,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Failed to create archive: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    print(f"Created archive: {archive_path}")
    return archive_path


def upload_to_s3(archive_path: Path, task_name: str) -> str:
    timestamp = int(time.time())
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    s3_key = f"skills-tmp/{timestamp}-{run_id}/{task_name}.zip"
    s3_url = f"s3://{S3_BUCKET}/{s3_key}"

    print(f"Uploading to S3: {s3_url}")
    result = subprocess.run(
        ["aws", "s3", "cp", str(archive_path), s3_url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"S3 upload failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    print("Upload complete")
    return s3_url


def cleanup_s3(s3_url: str) -> None:
    subprocess.run(["aws", "s3", "rm", s3_url], capture_output=True)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def make_request(method: str, url: str, api_key: str, body: dict | None = None) -> dict:
    headers = {"x-api-key": api_key}
    if body:
        headers["Content-Type"] = "application/json"

    try:
        response = requests.request(method=method, url=url, headers=headers, json=body, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        print(f"API error: {e}", file=sys.stderr)
        if e.response is not None:
            print(f"Status: {e.response.status_code}", file=sys.stderr)
            print(f"Body: {e.response.text}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Network error: {e}", file=sys.stderr)
        sys.exit(1)


def trigger_validation(s3_url: str, api_key: str) -> str:
    prompt = _PROMPT_PATH.read_text()
    payload = {
        "s3_url": s3_url,
        "prompt": prompt,
        "github_writer": os.environ.get("GITHUB_WRITER", ""),
    }
    url = f"{QC_API_URL}/custom"
    print("Triggering LLMaaJ QC via /custom endpoint")
    response = make_request("POST", url, api_key, payload)
    run_id = response["id"]
    print(f"Validation run ID: {run_id}")
    return run_id


def poll_for_results(run_id: str, api_key: str) -> dict:
    url = f"{QC_API_URL}/{run_id}"
    elapsed = 0

    print("Polling for results...")
    while elapsed < MAX_POLL_TIME:
        result = make_request("GET", url, api_key)
        status = result.get("run_status")
        print(f"  [{elapsed}s] status={status}")

        if status in ("success", "fail"):
            return result

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    print("Timed out waiting for validation results", file=sys.stderr)
    sys.exit(1)


def parse_llmaaj_result(result: dict) -> dict | None:
    """Extract the structured QC JSON from the API response."""
    metadata = result.get("result_metadata", {})

    # Try agent_output first (raw LLM text → JSON)
    agent_output = metadata.get("agent_output", "")
    if agent_output:
        # Strip markdown code fences if present
        text = agent_output.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Fallback: try evaluation dict
    evaluation = metadata.get("evaluation", {})
    if isinstance(evaluation, dict) and "sections" in evaluation:
        return evaluation

    return None


# ---------------------------------------------------------------------------
# Comment rendering
# ---------------------------------------------------------------------------

def _item_line(label: str, passed: bool | None, note: str = "") -> str:
    check = "x" if passed else " "
    line = f"- [{check}] {label}"
    if note and not passed:
        line += f" — *{note}*"
    return line


def _section_header(title: str, passed: bool | None) -> str:
    result = PASS if passed else (FAIL if passed is False else "N/A")
    return f"### {title} — {result}"



def render_llmaaj_sections(qc: dict) -> list[str]:
    lines: list[str] = []
    sections = qc.get("sections", {})

    # ---- Section 2 ----
    s2 = sections.get("section2_skill_quality", {})
    lines.append(_section_header("Section 2 — Skill Quality", s2.get("pass")))
    lines.append("")
    criteria = s2.get("criteria", {})

    c2 = criteria.get("criterion2_distractors_cannot_solve", {})
    lines.append(f"**Criterion 2 — Distractors Cannot Solve the Task — {'PASS' if c2.get('pass') else 'FAIL'}**")
    for key, label in [
        ("no_critical_logic_in_distractor", "No critical golden logic reproduced in any distractor"),
    ]:
        item = c2.get("items", {}).get(key, {})
        lines.append(_item_line(label, item.get("pass"), item.get("note", "")))
    lines.append("")

    c4 = criteria.get("criterion4_spectrum", {})
    lines.append(f"**Criterion 4 — Spectrum-Based Checks — {'PASS' if c4.get('pass') else 'FAIL'}**")
    for key, label in [
        ("single_core_capability", "Skill prioritizes one core capability"),
        ("interacts_with_environment", "Skill interacts with the environment (files, databases, configs)"),
    ]:
        item = c4.get("items", {}).get(key, {})
        lines.append(_item_line(label, item.get("pass"), item.get("note", "")))
    lines.append("")

    # ---- Section 3 ----
    s3 = sections.get("section3_task_quality", {})
    lines.append(_section_header("Section 3 — Task Quality", s3.get("pass")))
    lines.append("")
    s3_criteria = s3.get("criteria", {})

    tp = s3_criteria.get("task_prompt", {})
    lines.append(f"**Task Prompt — {'PASS' if tp.get('pass') else 'FAIL'}**")
    for key, label in [
        ("reads_naturally_as_real_request", "Task reads naturally as a real user request"),
        ("input_paths_match_actual_files", "Input file paths in prompt match actual input_files/"),
        ("no_golden_skill_names_in_prompt", "Task prompt does not mention any golden skill by name"),
        ("no_skill_md_excerpts_in_prompt", "Task prompt contains no excerpts from any SKILL.md"),
    ]:
        item = tp.get("items", {}).get(key, {})
        lines.append(_item_line(label, item.get("pass"), item.get("note", "")))
    lines.append("")

    th = s3_criteria.get("technical_hygiene", {})
    lines.append(f"**Technical Hygiene — {'PASS' if th.get('pass') else 'FAIL'}**")
    for key, label in [
        ("dates_specified_for_time_sensitive_data", "Dates specified for any time-sensitive data"),
        ("latex_for_math_variables", "Formal math/science variables use LaTeX notation"),
    ]:
        item = th.get("items", {}).get(key, {})
        lines.append(_item_line(label, item.get("pass"), item.get("note", "")))
    lines.append("")

    return lines


def format_comment(
    task_name: str,
    qc: dict | None,
    run_id: str,
) -> str:
    overall = qc is not None and qc.get("overall_pass", False)
    status_word = "PASSED" if overall else "FAILED"

    lines = [
        f"<!-- skills-qc-{task_name} -->",
        f"<!-- validation-run-id: {run_id} -->",
        f"## Skills QC — {status_word}",
        "",
        f"**Task:** `{task_name}`",
        "",
    ]

    if qc is not None:
        lines += render_llmaaj_sections(qc)

        if qc.get("summary"):
            lines += ["---", "### Summary", "", qc["summary"], ""]

        if qc.get("flags"):
            lines += ["### Flags", ""]
            for flag in qc["flags"]:
                lines.append(f"- {flag}")
            lines.append("")
    else:
        lines += [
            "### Sections 2-3 — LLMaaJ Checks",
            "",
            "*LLMaaJ evaluation unavailable — API did not return structured results.*",
            "",
        ]

    lines += ["---", f"<sub>Automated by [validate-task](.github/workflows/validate-task.yml) · run `{run_id}`</sub>"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run QC validation for a skills task")
    parser.add_argument("--task-name", required=True, help="Task directory name under tasks/")
    parser.add_argument("--task-dir", help="Override task directory path")
    args = parser.parse_args()

    api_key = os.environ.get("QC_API_KEY")
    if not api_key:
        print("QC_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    if not QC_API_URL:
        print("VALIDATION_API_URL environment variable not set", file=sys.stderr)
        sys.exit(1)

    if not S3_BUCKET:
        print("S3_BUCKET_TEMP environment variable not set", file=sys.stderr)
        sys.exit(1)

    if args.task_dir:
        task_dir = Path(args.task_dir)
    else:
        workspace = Path(os.environ.get("GITHUB_WORKSPACE", "."))
        task_dir = workspace / "tasks" / args.task_name

    if not task_dir.exists():
        print(f"Task directory not found: {task_dir}", file=sys.stderr)
        sys.exit(1)

    archive_path = None
    s3_url = None
    qc = None
    run_id = "n/a"

    try:
        archive_path = package_task(task_dir, args.task_name)
        s3_url = upload_to_s3(archive_path, args.task_name)
        run_id = trigger_validation(s3_url, api_key)
        result = poll_for_results(run_id, api_key)

        with open("qc-report.json", "w") as f:
            json.dump(result, f, indent=2)
        print("Saved: qc-report.json")

        qc = parse_llmaaj_result(result)
        if qc is None:
            print("Warning: could not parse structured QC JSON from LLMaaJ response", file=sys.stderr)

    finally:
        if s3_url:
            cleanup_s3(s3_url)
        if archive_path and archive_path.exists():
            archive_path.unlink()

    # --- Write unified comment ---
    comment = format_comment(args.task_name, qc, run_id)
    with open("qc-comment.md", "w") as f:
        f.write(comment)
    print("Saved: qc-comment.md")

    if qc is None or not qc.get("overall_pass", False):
        print("QC validation failed — see qc-comment.md for details", file=sys.stderr)
        sys.exit(1)

    print("QC validation complete!")


if __name__ == "__main__":
    main()

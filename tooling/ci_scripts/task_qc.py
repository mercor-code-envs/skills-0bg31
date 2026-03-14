#!/usr/bin/env python3
"""QC validation for Skills tasks.

1. Packages the task directory as a zip archive
2. Uploads to S3 temporary storage
3. Triggers the QC validation API
4. Polls for results
5. Writes a GitHub comment markdown file (qc-comment.md)
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

STATUS_EMOJI = {"success": "✅", "fail": "❌"}
SEVERITY_EMOJI = {"blocker": "🔴", "warn": "⚠️", "info": "ℹ️"}


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


def trigger_validation(s3_url: str, api_key: str, check_name: str) -> str:
    prompt = _PROMPT_PATH.read_text()
    payload = {
        "s3_url": s3_url,
        "prompt": prompt,
        "github_writer": os.environ.get("GITHUB_WRITER", ""),
    }
    url = f"{QC_API_URL}/custom"
    print("Triggering QC via /custom endpoint")
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


# ---------------------------------------------------------------------------
# Comment formatting
# ---------------------------------------------------------------------------

def format_comment(result: dict, task_name: str, run_id: str) -> str:
    run_status = result.get("run_status", "unknown")
    metadata = result.get("result_metadata", {})
    evaluation = metadata.get("evaluation", {})
    details = evaluation.get("details", {})
    if isinstance(details, str):
        flagged = []
    else:
        flagged = details.get("flagged_criteria", [])

    status_emoji = STATUS_EMOJI.get(run_status, "⏳")
    severity = result.get("severity")
    severity_str = f"{SEVERITY_EMOJI.get(severity, '')} {severity}".strip() if severity else "N/A"

    lines = [
        f"<!-- skills-qc-{task_name} -->",
        f"<!-- validation-run-id: {run_id} -->",
        f"## {status_emoji} Skills QC",
        "",
        f"**Task: `{task_name}`**",
        "",
        "| Field | Value |",
        "|-------|-------|",
    ]

    passed = evaluation.get("passed")
    if passed is not None:
        p_emoji = "✅" if passed else "❌"
        lines.append(f"| Result | {p_emoji} {'Passed' if passed else 'Failed'} |")

    score = evaluation.get("score")
    if score is not None:
        lines.append(f"| Score | {score:.2f} |")

    if severity:
        lines.append(f"| Severity | {severity_str} |")

    for ts_field in ("created_at", "updated_at"):
        if result.get(ts_field):
            lines.append(f"| {ts_field.replace('_', ' ').title()} | {result[ts_field]} |")

    lines.append("")

    # Summary
    if evaluation.get("summary"):
        lines += ["### Summary", "", evaluation["summary"], ""]

    # Flagged criteria
    if flagged:
        lines += ["### Issues Found", "", f"**{len(flagged)} issue(s) detected**", ""]
        for idx, item in enumerate(flagged, 1):
            criterion = item.get("criterion", "Unknown")
            issue = item.get("issue", "")
            fix = item.get("suggested_fix", "")
            lines += [f"#### {idx}. {criterion}", "", f"**Issue:** {issue}", ""]
            if fix:
                lines += [f"**Suggested Fix:** {fix}", ""]

    # Fallback: raw issues_found list
    if metadata.get("issues_found") and not flagged:
        lines += ["### Issues Found", ""]
        lines += [f"- {i}" for i in metadata["issues_found"]]
        lines.append("")

    # Agent output (collapsible)
    if metadata.get("agent_output"):
        output = metadata["agent_output"]
        if len(output) > 1000:
            output = f"{output[:1000]}...\n\n*[Output truncated]*"
        lines += [
            "<details>",
            "<summary>Agent Output</summary>",
            "",
            "```",
            output,
            "```",
            "</details>",
            "",
        ]

    lines += ["---", "*Automated by Skills QC*"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run QC validation for a skills task")
    parser.add_argument("--task-name", required=True, help="Task directory name under tasks/")
    parser.add_argument("--task-dir", help="Override task directory path")
    parser.add_argument("--check-name", default="skill_qc", help="Check name to run (default: skill_qc)")
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

    try:
        archive_path = package_task(task_dir, args.task_name)
        s3_url = upload_to_s3(archive_path, args.task_name)
        run_id = trigger_validation(s3_url, api_key, args.check_name)
        result = poll_for_results(run_id, api_key)

        # Write JSON report
        with open("qc-report.json", "w") as f:
            json.dump(result, f, indent=2)
        print("Saved: qc-report.json")

        # Write comment markdown
        comment = format_comment(result, args.task_name, run_id)
        with open("qc-comment.md", "w") as f:
            f.write(comment)
        print("Saved: qc-comment.md")

        if result.get("run_status") == "fail":
            print("QC validation failed", file=sys.stderr)
            sys.exit(1)

        print("QC validation complete!")

    finally:
        if s3_url:
            cleanup_s3(s3_url)
        if archive_path and archive_path.exists():
            archive_path.unlink()


if __name__ == "__main__":
    main()

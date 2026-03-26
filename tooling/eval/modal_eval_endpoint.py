"""Modal eval endpoint for skills-template CI.

Three endpoints:
  POST /submit  — accepts task config, returns job_id immediately
  GET  /status  — poll with job_id, reads results from S3
  POST /run_eval — synchronous (blocks until done)

Job state stored in S3: s3://{bucket}/jobs/{job_id}/status.json

Deploy: modal deploy tooling/eval/modal_eval_endpoint.py
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import modal

log = logging.getLogger("eval-ci")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Modal app + image
# ---------------------------------------------------------------------------

app = modal.App("skills-eval-ci")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "curl", "ca-certificates", "zip", "unzip", "jq")
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -",
        "apt-get install -y nodejs",
        "npm install -g @anthropic-ai/claude-code --ignore-scripts",
    )
    .pip_install(
        "harbor>=0.1.41",
        "boto3>=1.35.0",
        "requests>=2.32.0",
    )
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RESULTS_BUCKET = "skills-eval-ci"
PER_RUN_TIMEOUT = 5400

GATE_SPECS: list[dict[str, Any]] = [
    {"name": "oracle", "agent": "oracle", "model": "oracle",
     "runs": 1, "variant": "all_skills_nudge",
     "pass_fn": lambda p, t: p == t},
    {"name": "gemini_no_skills", "agent": "terminus-2", "model": "gemini/gemini-3.1-pro-preview",
     "runs": 3, "variant": "no_skills",
     "pass_fn": lambda p, t: p == 0},
    {"name": "gemini_distractor_only", "agent": "terminus-2", "model": "gemini/gemini-3.1-pro-preview",
     "runs": 3, "variant": "distractor_only",
     "pass_fn": lambda p, t: p == 0},
    {"name": "gemini_all_skills_nudge", "agent": "terminus-2", "model": "gemini/gemini-3.1-pro-preview",
     "runs": 5, "variant": "all_skills_nudge", "check_all_golden": True,
     "pass_fn": lambda p, t: 1 <= p <= t - 1},
]


# ---------------------------------------------------------------------------
# S3 job state store
# ---------------------------------------------------------------------------


def _s3_client():
    import boto3
    return boto3.client("s3")


def _job_s3_key(job_id: str) -> str:
    return f"jobs/{job_id}/status.json"


def save_job_state(job_id: str, state: dict) -> None:
    try:
        _s3_client().put_object(
            Bucket=RESULTS_BUCKET,
            Key=_job_s3_key(job_id),
            Body=json.dumps(state, default=str),
            ContentType="application/json",
        )
    except Exception as e:
        log.warning(f"Failed to save job state for {job_id}: {e}")


def load_job_state(job_id: str) -> dict | None:
    try:
        s3 = _s3_client()
        resp = s3.get_object(Bucket=RESULTS_BUCKET, Key=_job_s3_key(job_id))
        return json.loads(resp["Body"].read().decode())
    except Exception as e:
        if "NoSuchKey" in str(type(e).__name__) or "NoSuchKey" in str(e):
            return None
        log.warning(f"Failed to load job state for {job_id}: {e}")
        return None


# ---------------------------------------------------------------------------
# Nudge validation
# ---------------------------------------------------------------------------

# Must match the canonical nudge required by validate_task.py.
_NUDGE_RE = re.compile(
    r'^The documentation and scripts in \S+ are useful for high-level repeated workflows '
    r'such as common tool usage or calling external APIs, etc that would otherwise be error-prone\. '
    r'Prioritize using existing scripts when possible and only write custom solutions when truly necessary\.\n'
    r'\nNever use a script without reading its documentation first\. '
    r'All subdirectories have a SKILL\.md file with documentation which you must read before '
    r'using the scripts in such subdirectories\.'
)

# Strips the canonical nudge block from the start of instruction.md (used for no_skills variant).
_NUDGE_STRIP_RE = re.compile(
    r'^The documentation and scripts in \S+ are useful for.*?'
    r'using the scripts in such subdirectories\.\n*',
    re.DOTALL,
)


def check_task_nudge(task_dir: Path) -> None:
    """Hard-fail if instruction.md does not begin with the required nudge text."""
    instruction = task_dir / "instruction.md"
    if not instruction.exists():
        raise ValueError(f"instruction.md missing in {task_dir.name}")
    text = instruction.read_text(errors="replace")
    if not _NUDGE_RE.match(text):
        raise ValueError(
            f"Task '{task_dir.name}' instruction.md does not begin with the required nudge. "
            "Run validate_task.py to diagnose and fix before submitting to eval."
        )


# ---------------------------------------------------------------------------
# Skill variant preparation
# ---------------------------------------------------------------------------


def find_skills_dir(task_dir: Path) -> Path | None:
    for c in [task_dir / "environment" / "skills", task_dir / "skills"]:
        if c.exists() and c.is_dir():
            return c
    return None


def strip_nudge(instruction_path: Path) -> None:
    """Remove the canonical nudge block from instruction.md (for no_skills variant)."""
    if not instruction_path.exists():
        return
    content = instruction_path.read_text()
    content = _NUDGE_STRIP_RE.sub("", content, count=1)
    instruction_path.write_text(content.strip() + "\n")


def prepare_no_skills(src: Path, dst: Path, **_: Any) -> None:
    shutil.copytree(src, dst)
    dockerfile = dst / "environment" / "Dockerfile"
    if dockerfile.exists():
        lines = dockerfile.read_text().split("\n")
        lines = [l for l in lines if not l.strip().startswith("COPY skills")]
        dockerfile.write_text("\n".join(lines) + "\n")
    skills_dir = find_skills_dir(dst)
    if skills_dir:
        shutil.rmtree(skills_dir)
    strip_nudge(dst / "instruction.md")


def prepare_golden_only(src: Path, dst: Path, *, golden_dirs: list[str], **_: Any) -> None:
    shutil.copytree(src, dst)
    skills_dir = find_skills_dir(dst)
    if skills_dir:
        for d in list(skills_dir.iterdir()):
            if d.is_dir() and d.name not in golden_dirs:
                shutil.rmtree(d)


def prepare_distractor_only(src: Path, dst: Path, *, distractor_dirs: list[str], **_: Any) -> None:
    shutil.copytree(src, dst)
    skills_dir = find_skills_dir(dst)
    if skills_dir:
        for d in list(skills_dir.iterdir()):
            if d.is_dir() and d.name not in distractor_dirs:
                shutil.rmtree(d)


def prepare_all_skills_nudge(src: Path, dst: Path, **_: Any) -> None:
    shutil.copytree(src, dst)


VARIANT_BUILDERS = {
    "no_skills": prepare_no_skills,
    "golden_only": prepare_golden_only,
    "distractor_only": prepare_distractor_only,
    "all_skills_nudge": prepare_all_skills_nudge,
}


def prepare_all_variants(
    original_task_dir: Path, workspace: Path, task_name: str,
    golden_dirs: list[str], distractor_dirs: list[str],
) -> dict[str, Path]:
    variants = {}
    for variant_name in {g["variant"] for g in GATE_SPECS}:
        staging = workspace / f"staging_{variant_name}"
        staging.mkdir(parents=True, exist_ok=True)
        VARIANT_BUILDERS[variant_name](
            original_task_dir, staging / task_name,
            golden_dirs=golden_dirs, distractor_dirs=distractor_dirs,
        )
        variants[variant_name] = staging
        log.info(f"Prepared variant: {variant_name}")
    return variants


# ---------------------------------------------------------------------------
# Harbor eval
# ---------------------------------------------------------------------------


def harbor_eval(
    staging_dir: Path, task_name: str, agent: str, model: str,
    label: str, jobs_base: Path, force_build: bool = False,
) -> dict:
    job_dir = jobs_base / label
    job_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "harbor", "run",
        "-p", str(staging_dir), "-t", task_name,
        "-a", agent, "-m", model,
        "-k", "1", "-n", "1",
        "-e", "modal", "--jobs-dir", str(job_dir), "--no-delete",
    ]
    if force_build:
        cmd.append("--force-build")

    log.info(f"[{label}] {' '.join(cmd)}")
    t0 = time.time()

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=PER_RUN_TIMEOUT)
        log.info(f"[{label}] exit={result.returncode} ({time.time()-t0:.0f}s)")
        if result.stdout:
            log.info(f"[{label}] stdout: {result.stdout[:800]}")
        if result.stderr:
            log.info(f"[{label}] stderr: {result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        return {"passed": False, "reward": None, "error": "timeout", "duration": PER_RUN_TIMEOUT, "label": label}
    except Exception as e:
        return {"passed": False, "reward": None, "error": str(e), "duration": time.time() - t0, "label": label}

    duration = time.time() - t0
    reward = None
    passed = False

    result_files = list(job_dir.rglob("result.json"))
    if result_files:
        try:
            data = json.loads(result_files[0].read_text())
            evals = data.get("stats", {}).get("evals", {})
            if isinstance(evals, dict) and evals:
                first_eval = next(iter(evals.values()))
                reward_stats = first_eval.get("reward_stats", {}).get("reward", {})
                if isinstance(reward_stats, dict):
                    reward = 1 if "1.0" in reward_stats or 1.0 in reward_stats else 0
                    passed = reward == 1
                elif isinstance(reward_stats, (int, float)):
                    reward = int(reward_stats)
                    passed = reward == 1
                log.info(f"[{label}] parsed: reward={reward}")

            if reward is None:
                for eval_data in (evals.values() if isinstance(evals, dict) else []):
                    metrics = eval_data.get("metrics", [])
                    if metrics and isinstance(metrics[0], dict):
                        mean = metrics[0].get("mean")
                        if mean is not None:
                            reward = 1 if float(mean) >= 0.5 else 0
                            passed = reward == 1
                            break

            if reward is None:
                for trial_rj in sorted(job_dir.rglob("*/result.json")):
                    if trial_rj == result_files[0]:
                        continue
                    try:
                        td = json.loads(trial_rj.read_text())
                        vr = td.get("verifier_result", {}).get("rewards", {}).get("reward")
                        if vr is not None:
                            reward = int(vr)
                            passed = reward == 1
                            break
                    except Exception:
                        pass
        except Exception as e:
            log.warning(f"[{label}] parse error: {e}")
    else:
        log.warning(f"[{label}] NO result.json found")

    log.info(f"[{label}] FINAL: reward={reward} passed={passed}")
    return {"passed": passed, "reward": reward, "error": None, "duration": round(duration, 1), "label": label}


# ---------------------------------------------------------------------------
# Gate execution
# ---------------------------------------------------------------------------


def check_golden_skills_in_trajectory(run_job_dir: Path, golden_dirs: list[str]) -> bool:
    """Return True if all golden skill names appear in at least one trajectory.json in this run."""
    for traj_file in run_job_dir.rglob("trajectory.json"):
        try:
            text = traj_file.read_text(errors="replace")
            if all(skill in text for skill in golden_dirs):
                return True
        except Exception:
            pass
    return False


def run_gate(gate: dict, staging_dir: Path, task_name: str, jobs_base: Path, golden_dirs: list[str] | None = None) -> dict:
    gate_name = gate["name"]
    n_runs = gate["runs"]
    runs = []

    with ThreadPoolExecutor(max_workers=n_runs) as ex:
        futs = {
            ex.submit(
                harbor_eval, staging_dir, task_name,
                gate["agent"], gate["model"],
                f"{gate_name}-run-{i+1}", jobs_base,
                force_build=(i == 0),
            ): i for i in range(n_runs)
        }
        for fut in as_completed(futs):
            try:
                runs.append(fut.result())
            except Exception as e:
                runs.append({"passed": False, "reward": None, "error": str(e), "duration": 0})

    pass_count = sum(1 for r in runs if r.get("passed"))
    total = len(runs)
    gate_passed = gate["pass_fn"](pass_count, total)

    golden_check_passed = None
    if gate.get("check_all_golden") and gate_passed and golden_dirs:
        golden_check_passed = any(
            check_golden_skills_in_trajectory(jobs_base / r["label"], golden_dirs)
            for r in runs if r.get("passed")
        )
        if not golden_check_passed:
            log.info(f"[{gate_name}] golden skill usage check FAILED — no successful run used all golden skills")
            gate_passed = False

    log.info(f"[{gate_name}] {pass_count}/{total} — {'PASS' if gate_passed else 'FAIL'}")
    return {
        "gate_name": gate_name, "gate_passed": gate_passed,
        "pass_count": pass_count, "total_runs": total, "runs": runs,
        "golden_check_passed": golden_check_passed,
    }


# ---------------------------------------------------------------------------
# S3 download
# ---------------------------------------------------------------------------


def download_and_unpack(s3_bucket: str, s3_key: str, workspace: Path) -> Path:
    import boto3
    local_zip = workspace / "task.zip"
    log.info(f"Downloading s3://{s3_bucket}/{s3_key}")
    boto3.client("s3").download_file(s3_bucket, s3_key, str(local_zip))
    original = workspace / "original"
    original.mkdir(exist_ok=True)
    subprocess.run(["unzip", "-q", str(local_zip), "-d", str(original)], check=True)
    task_dirs = [d for d in original.iterdir() if d.is_dir()]
    if not task_dirs:
        raise ValueError(f"No task directory found in {s3_key}")
    return task_dirs[0]


# ---------------------------------------------------------------------------
# Core eval logic
# ---------------------------------------------------------------------------


def _run_eval_core(job_id: str, payload: dict) -> dict:
    """Run all 6 gates, save progress to S3 after each gate completes."""
    t0 = time.time()
    task_name = payload.get("task_name", "unknown")
    log.info(f"=== [{job_id}] Eval CI: {task_name} ===")

    state = {"status": "running", "task_name": task_name, "job_id": job_id, "gates": {}}
    save_job_state(job_id, state)

    workspace = Path(tempfile.mkdtemp(prefix=f"eval_{task_name}_"))
    jobs_base = workspace / "jobs"
    jobs_base.mkdir()

    try:
        task_dir = download_and_unpack(payload["s3_bucket"], payload["s3_key"], workspace)
        check_task_nudge(task_dir)
        variants = prepare_all_variants(
            task_dir, workspace, task_name,
            payload.get("golden_skill_dirs", []),
            payload.get("distractor_skill_dirs", []),
        )

        golden_dirs = payload.get("golden_skill_dirs", [])
        gate_results = {}
        with ThreadPoolExecutor(max_workers=len(GATE_SPECS)) as ex:
            futs = {
                ex.submit(run_gate, g, variants[g["variant"]], task_name, jobs_base, golden_dirs): g["name"]
                for g in GATE_SPECS
            }
            for fut in as_completed(futs):
                name = futs[fut]
                try:
                    gate_results[name] = fut.result()
                except Exception as e:
                    gate_results[name] = {
                        "gate_name": name, "gate_passed": False,
                        "pass_count": 0, "total_runs": 0, "runs": [], "error": str(e),
                    }
                # Save progress after each gate completes
                state["gates"] = gate_results
                state["completed_gates"] = len(gate_results)
                state["total_gates"] = len(GATE_SPECS)
                save_job_state(job_id, state)

        eval_passed = all(g.get("gate_passed", False) for g in gate_results.values())
        duration = round(time.time() - t0, 1)

        log.info(f"=== [{job_id}] {'PASSED' if eval_passed else 'FAILED'} in {duration}s ===")
        for gn, gr in gate_results.items():
            log.info(f"  {gn}: {'PASS' if gr.get('gate_passed') else 'FAIL'} "
                     f"({gr.get('pass_count','?')}/{gr.get('total_runs','?')})")

        final_state = {
            "status": "completed", "job_id": job_id, "task_name": task_name,
            "eval_passed": eval_passed, "gates": gate_results, "duration_sec": duration,
        }
        save_job_state(job_id, final_state)
        return final_state

    except Exception as e:
        import traceback
        traceback.print_exc()
        error_state = {
            "status": "failed", "job_id": job_id, "task_name": task_name,
            "eval_passed": False, "error": str(e), "gates": state.get("gates", {}),
            "duration_sec": round(time.time() - t0, 1),
        }
        save_job_state(job_id, error_state)
        return error_state
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


# ---------------------------------------------------------------------------
# Auth — all endpoints require X-Api-Key header
# ---------------------------------------------------------------------------


def _check_api_key(api_key: str | None) -> None:
    """Validate API key. Raises 401 if invalid."""
    from fastapi import HTTPException
    expected = os.environ.get("EVAL_API_KEY", "")
    if not expected:
        return
    if not api_key or api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Api-Key")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_SECRETS = [modal.Secret.from_name("eval-api-keys"), modal.Secret.from_name("eval-aws-credentials")]


@app.function(image=image, secrets=_SECRETS, cpu=4, memory=8192, timeout=7200)
def _run_eval_background(job_id: str, payload: dict) -> dict:
    """Modal function that runs eval in its own container."""
    return _run_eval_core(job_id, payload)


@app.function(image=image, secrets=_SECRETS, cpu=1, memory=512, timeout=30)
@modal.fastapi_endpoint(method="POST")
def submit(payload: dict, api_key: str = "") -> dict:
    """Submit eval job. Requires api_key param. Poll /status for results."""
    _check_api_key(api_key)

    job_id = uuid.uuid4().hex[:12]
    save_job_state(job_id, {"status": "submitted", "task_name": payload.get("task_name"), "job_id": job_id})
    _run_eval_background.spawn(job_id, payload)

    log.info(f"Job {job_id} submitted for {payload.get('task_name')}")
    return {"job_id": job_id, "status": "submitted"}


@app.function(image=image, secrets=_SECRETS, cpu=1, memory=512, timeout=30)
@modal.fastapi_endpoint(method="GET")
def status(job_id: str, api_key: str = "") -> dict:
    """Poll for results. Requires api_key param."""
    _check_api_key(api_key)

    state = load_job_state(job_id)
    if state is None:
        return {"status": "not_found", "job_id": job_id}
    return state


@app.function(image=image, secrets=_SECRETS, cpu=4, memory=8192, timeout=7200)
@modal.fastapi_endpoint(method="POST")
def run_eval(payload: dict, api_key: str = "") -> dict:
    """Synchronous endpoint. Requires api_key param."""
    _check_api_key(api_key)

    job_id = uuid.uuid4().hex[:12]
    return _run_eval_core(job_id, payload)

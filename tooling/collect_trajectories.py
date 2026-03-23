#!/usr/bin/env python3
"""
Collect trajectory.json files from jobs/ and organize them into trajectories/.

Naming convention:
  trajectories/<task-name>-<agent>-<skill-status>.json

Where:
  agent:        gemini | claude
  skill-status: skill | no-skill

Skill trajectories must use ALL golden skills from metadata to be collected. For no-skill trajectory, there must NO skills involved.

Usage:
  python3 collect_trajectories.py [--dry-run]
"""

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
JOBS_DIR = ROOT / "jobs"
TASKS_DIR = ROOT / "tasks"
OUT_DIR = ROOT / "trajectories"


def detect_agent(job_dir: Path) -> str | None:
    config = job_dir / "config.json"
    if config.exists():
        try:
            data = json.loads(config.read_text())
            for a in data.get("agents", []):
                name = a.get("name", "").lower()
                model = a.get("model_name", "").lower()
                if "gemini" in model or "terminus" in name:
                    return "gemini"
                if "claude" in model or "claude-code" in name:
                    return "claude"
        except Exception:
            pass

    for trial_dir in job_dir.iterdir():
        if not trial_dir.is_dir() or trial_dir.name == "__pycache__":
            continue
        if (trial_dir / "agent" / "claude-code.txt").exists():
            return "claude"
        if (trial_dir / "agent" / "trajectory.json").exists():
            try:
                traj = json.loads((trial_dir / "agent" / "trajectory.json").read_text())
                agent_name = traj.get("agent", {}).get("name", "").lower()
                if "terminus" in agent_name or "gemini" in agent_name:
                    return "gemini"
                if "claude" in agent_name:
                    return "claude"
            except Exception:
                pass
    return None


def detect_skill_status(job_name: str, task_name: str) -> str:
    if "noskill" in job_name or "no-skill" in job_name or "no_skill" in job_name:
        return "no-skill"

    meta_path = TASKS_DIR / task_name / "metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            if meta.get("golden_skills", []):
                return "skill"
            return "no-skill"
        except Exception:
            pass

    return "skill"


def extract_task_name(job_dir: Path) -> str | None:
    for trial_dir in job_dir.iterdir():
        if not trial_dir.is_dir():
            continue
        parts = trial_dir.name.rsplit("__", 1)
        if len(parts) == 2:
            return parts[0]
    return None


def find_trajectory(job_dir: Path) -> Path | None:
    for trial_dir in job_dir.iterdir():
        if not trial_dir.is_dir():
            continue
        traj = trial_dir / "agent" / "trajectory.json"
        if traj.exists():
            return traj
    return None


def validate_golden_skill_usage(traj_path: Path, task_name: str) -> tuple[bool, list[str], list[str]]:
    """Returns (is_valid, golden_used, golden_missing). Valid = all golden skills used."""
    meta_path = TASKS_DIR / task_name / "metadata.json"
    if not meta_path.exists():
        return True, [], []

    try:
        meta = json.loads(meta_path.read_text())
    except Exception:
        return True, [], []

    golden = set(meta.get("golden_skills", []))
    if not golden:
        return True, [], []

    try:
        traj = json.loads(traj_path.read_text())
    except Exception:
        return False, [], sorted(golden)

    skills_used = set()
    for step in traj.get("steps", []):
        for tc in step.get("tool_calls", []):
            if tc.get("function_name") == "Skill":
                skill_name = tc.get("arguments", {}).get("skill", "")
                if skill_name:
                    skills_used.add(skill_name)

    golden_used = sorted(skills_used & golden)
    golden_missing = sorted(golden - skills_used)
    return len(golden_missing) == 0, golden_used, golden_missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print actions without copying")
    args = parser.parse_args()

    if not JOBS_DIR.exists():
        print(f"ERROR: {JOBS_DIR} does not exist")
        return

    if not args.dry_run:
        OUT_DIR.mkdir(exist_ok=True)

    collected = 0
    duplicates = 0
    errors = []

    candidates: dict[str, tuple[Path, float]] = {}

    for job_dir in sorted(JOBS_DIR.iterdir()):
        if not job_dir.is_dir():
            continue

        traj_path = find_trajectory(job_dir)
        if not traj_path:
            continue

        agent = detect_agent(job_dir)
        if not agent:
            errors.append(f"{job_dir.name}: could not detect agent")
            continue

        task_name = extract_task_name(job_dir)
        if not task_name:
            errors.append(f"{job_dir.name}: could not extract task name")
            continue

        skill_status = detect_skill_status(job_dir.name, task_name)
        out_name = f"{task_name}-{agent}-{skill_status}.json"
        mtime = traj_path.stat().st_mtime

        if out_name in candidates:
            duplicates += 1
            if mtime > candidates[out_name][1]:
                candidates[out_name] = (traj_path, mtime)
        else:
            candidates[out_name] = (traj_path, mtime)

    rejected = []
    for out_name, (traj_path, _) in sorted(candidates.items()):
        if "-skill.json" in out_name and "-no-skill.json" not in out_name:
            task_name = out_name.rsplit("-", 2)[0]
            valid, used, missing = validate_golden_skill_usage(traj_path, task_name)
            if not valid:
                rejected.append((out_name, used, missing))
                continue

        out_path = OUT_DIR / out_name
        if args.dry_run:
            print(f"  {traj_path} -> {out_path}")
        else:
            shutil.copy2(traj_path, out_path)
        collected += 1

    print(f"\nCollected:  {collected}")
    print(f"Duplicates: {duplicates} (kept latest)")
    if rejected:
        print(f"Rejected:   {len(rejected)} (missing golden skills)")
        for name, used, missing in rejected:
            print(f"  {name}: used {used}, missing {missing}")
    if errors:
        print(f"Errors:     {len(errors)}")
        for e in errors[:10]:
            print(f"  {e}")


if __name__ == "__main__":
    main()

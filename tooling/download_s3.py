#!/usr/bin/env python3
"""Download and extract a task zip from a presigned S3 URL into tasks/."""
import argparse, io, sys, zipfile
from pathlib import Path

try:
    import urllib.request as urlreq
except ImportError:
    pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--s3-url", required=True)
    parser.add_argument("--task-name", default=None, help="Optional override for output dir name")
    args = parser.parse_args()

    print(f"Downloading task zip...")
    with urlreq.urlopen(args.s3_url) as resp:
        data = resp.read()
    print(f"Downloaded {len(data)//1024} KB")

    tasks_dir = Path(__file__).parent.parent / "tasks"
    tasks_dir.mkdir(exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        # Detect top-level dir name from zip
        top_dirs = {p.split("/")[0] for p in zf.namelist() if "/" in p}
        task_slug = list(top_dirs)[0] if top_dirs else args.task_name or "task"
        dest = tasks_dir / task_slug
        if dest.exists():
            import shutil; shutil.rmtree(dest)
        for member in zf.namelist():
            if member.startswith(f"{task_slug}/"):
                rel = member[len(task_slug)+1:]
                if not rel:
                    continue
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if not member.endswith("/"):
                    target.write_bytes(zf.read(member))
    print(f"Extracted to: tasks/{task_slug}/")
    print(f"Next: cd tasks/{task_slug} and start working!")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Fork Sync Utility — Skills template tooling.

Finds all expert fork repos (named `skills-*`, excluding `skills-template` itself)
within the org and syncs them with the latest state of the template's main branch.

Expert fork repos may be created from the template (not true GitHub forks), so
`repo.get_forks()` won't find them. Instead we search by name prefix within the org.

Sync strategy per repo:
  - All non-tasks/ files: taken from template (infra, workflows, tooling, etc.)
  - tasks/ files: preserved from fork (expert completed tasks on main are kept)
  - trajectories/ files: preserved from fork (model eval results must never be deleted)
  - tasks/.gitkeep: taken from template
  This means template changes never overwrite expert task work or eval results on fork main.

Usage:
    export GITHUB_TOKEN=ghp_...
    python sync_forks.py --template-repo mercor-code-envs/skills-template
    python sync_forks.py --template-repo mercor-code-envs/skills-template --dry-run
"""

import argparse
import base64
import os
import sys
import time

try:
    from github import Auth, Github, GithubException, InputGitTreeElement
except ImportError:
    print("ERROR: PyGithub not installed. Run: pip install PyGithub", file=sys.stderr)
    sys.exit(1)


def get_github_client() -> Github:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set.")
    return Github(auth=Auth.Token(token))


def find_fork_repos(gh: Github, org_name: str, template_repo, fork_prefix: str = "skills-") -> list:
    """
    Return all repos in the org that start with fork_prefix, excluding the template itself.
    Covers both true GitHub forks and template-created repos (fork=False).
    """
    org = gh.get_organization(org_name)
    repos = []
    for repo in org.get_repos():
        if repo.name.startswith(fork_prefix) and repo.full_name != template_repo.full_name:
            repos.append(repo)
    return repos


def sync_fork(template_repo, fork_repo, branch: str = "main", dry_run: bool = False) -> dict:
    result = {"fork": fork_repo.full_name, "method": None, "success": False, "message": ""}

    if dry_run:
        result.update(
            method="dry-run",
            success=True,
            message="[dry-run] Would sync {}:{}".format(fork_repo.full_name, branch),
        )
        return result

    # Always use direct Git tree push — never merge_upstream.
    # Sync strategy:
    #   - All non-tasks/ files: taken from template (infra, workflows, tooling, etc.)
    #   - tasks/ files: preserved from fork (expert completed tasks on main are kept)
    #   - trajectories/ files: preserved from fork (model eval results must never be deleted)
    #   - tasks/.gitkeep: taken from template
    # This means template changes never overwrite expert task work or eval results on fork main.
    try:
        template_sha = template_repo.get_branch(branch).commit.sha
        fork_sha = fork_repo.get_branch(branch).commit.sha

        if template_sha == fork_sha:
            result.update(method="direct_push", success=True, message="Already up to date.")
            return result

        template_tree = template_repo.get_git_tree(template_sha, recursive=True)
        fork_tree = fork_repo.get_git_tree(fork_sha, recursive=True)
        fork_blobs = {item.path: item for item in fork_tree.tree if item.type == "blob"}
        template_blobs = {item.path: item for item in template_tree.tree if item.type == "blob"}

        # Build target blob set:
        #   - Start with all template blobs (infra + tasks/.gitkeep)
        #   - Layer fork's tasks/ content on top (expert work preserved)
        #   - Layer fork's trajectories/ content on top (model eval results preserved)
        target_blobs = dict(template_blobs)
        for path, item in fork_blobs.items():
            if (path.startswith("tasks/") and path != "tasks/.gitkeep") or path.startswith("trajectories/"):
                target_blobs[path] = item

        # Compute what needs to change vs current fork state
        new_items = []
        for path, item in target_blobs.items():
            if getattr(fork_blobs.get(path), "sha", None) == item.sha:
                continue  # already up to date
            if path in template_blobs:
                # Fetch content from template repo
                blob = template_repo.get_git_blob(item.sha)
                raw = (
                    base64.b64decode(blob.content)
                    if blob.encoding == "base64"
                    else blob.content.encode()
                )
                content = raw.decode("utf-8", errors="replace")
                new_items.append(
                    InputGitTreeElement(path=path, mode=item.mode, type="blob", content=content)
                )
            # Expert task blobs already exist in fork — no content fetch needed

        # Delete files in fork that are not in target_blobs
        # (non-tasks/ files removed from template get cleaned up; tasks/ is never deleted)
        deleted = []
        for path in fork_blobs:
            if path not in target_blobs:
                new_items.append(
                    InputGitTreeElement(path=path, mode="100644", type="blob", sha=None)
                )
                deleted.append(path)

        if not new_items:
            result.update(method="direct_push", success=True, message="No infra changes to sync.")
            return result

        base_tree = fork_repo.get_git_tree(fork_sha)
        new_tree = fork_repo.create_git_tree(new_items, base_tree=base_tree)
        new_commit = fork_repo.create_git_commit(
            message="chore: sync infra from {}".format(template_repo.full_name),
            tree=new_tree,
            parents=[fork_repo.get_git_commit(fork_sha)],
        )
        # force=True required for repos that diverged (e.g. via sync_with_template merge commits)
        fork_repo.get_git_ref("heads/{}".format(branch)).edit(new_commit.sha, force=True)

        msg = "Synced {} file(s)".format(len(new_items))
        if deleted:
            msg += ", deleted {} stale infra file(s)".format(len(deleted))
        result.update(method="direct_push", success=True, message=msg)
        return result

    except GithubException as exc:
        result.update(
            method="direct_push",
            success=False,
            message="Direct push failed ({}): {}".format(exc.status, exc.data),
        )
        return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync all Skills expert repos (skills-*) with template.",
    )
    parser.add_argument("--template-repo", required=True, metavar="OWNER/REPO")
    parser.add_argument("--fork-prefix", default="skills-", metavar="PREFIX",
                        help="Repo name prefix to match (default: skills-)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--pause", type=float, default=0.5)
    args = parser.parse_args()

    try:
        gh = get_github_client()
    except RuntimeError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        sys.exit(1)

    try:
        template_repo = gh.get_repo(args.template_repo)
    except GithubException as exc:
        print("ERROR: Could not fetch {}: {}".format(args.template_repo, exc), file=sys.stderr)
        sys.exit(1)

    org_name = args.template_repo.split("/")[0]
    print("Template repo : {}".format(template_repo.full_name))
    print("Org           : {}".format(org_name))
    print("Fork prefix   : {}".format(args.fork_prefix))
    print("Branch        : {}".format(args.branch))
    print("Dry run       : {}".format(args.dry_run))
    print()

    forks = find_fork_repos(gh, org_name, template_repo, fork_prefix=args.fork_prefix)

    if not forks:
        print("No repos with prefix '{}' found in {}.".format(args.fork_prefix, org_name))
        sys.exit(0)

    print("Found {} repo(s) to sync...".format(len(forks)))
    print()

    results = []
    ok_count = fail_count = 0

    for fork in forks:
        result = sync_fork(template_repo, fork, branch=args.branch, dry_run=args.dry_run)
        results.append(result)
        icon = "OK " if result["success"] else "ERR"
        print("  [{}] {:55s}  ({})  {}".format(icon, result["fork"], result["method"], result["message"]))
        if result["success"]:
            ok_count += 1
        else:
            fail_count += 1
        time.sleep(args.pause)

    print()
    print("=" * 60)
    print("Sync complete: {} OK, {} failed out of {} repos.".format(ok_count, fail_count, len(forks)))

    if fail_count > 0:
        for r in results:
            if not r["success"]:
                print("  - {}: {}".format(r["fork"], r["message"]))
        sys.exit(1)


if __name__ == "__main__":
    main()

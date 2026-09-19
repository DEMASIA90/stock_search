from __future__ import annotations

import json
import subprocess
import sys

from src.github_cli import ensure_gh

WORKFLOW = "update-and-deploy.yml"


def run(args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, check=True, capture_output=capture)


def main() -> int:
    try:
        gh = str(ensure_gh())
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    try:
        run([gh, "auth", "status", "--hostname", "github.com"])
    except subprocess.CalledProcessError:
        print(
            "[ERROR] GitHub CLI login is not configured. Run configure_hourly_update.bat first.",
            file=sys.stderr,
        )
        return 1

    try:
        print("\nLatest hourly update runs:\n")
        run([gh, "run", "list", "--workflow", WORKFLOW, "--limit", "6"])
        latest = run(
            [
                gh,
                "run",
                "list",
                "--workflow",
                WORKFLOW,
                "--limit",
                "1",
                "--json",
                "databaseId,status,conclusion,url,createdAt,headBranch",
            ],
            capture=True,
        )
        rows = json.loads(latest.stdout or "[]")
        if not rows:
            print("\n[WARN] No workflow run was found. Push the workflow and run configure_hourly_update.bat.")
            return 2
        item = rows[0]
        print(
            f"\nLatest: {item.get('status')} / {item.get('conclusion')} "
            f"({item.get('createdAt') or ''}, {item.get('headBranch') or ''})\n{item.get('url') or ''}"
        )
        if item.get("conclusion") == "failure":
            print("\nFailed-step log:\n")
            subprocess.run([gh, "run", "view", str(item["databaseId"]), "--log-failed"], check=False)
            return 1
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] GitHub Actions status check failed ({exc.returncode}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

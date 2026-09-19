from __future__ import annotations

import json
import shutil
import subprocess
import sys

WORKFLOW = "update-and-deploy.yml"


def run(args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, check=True, capture_output=capture)


def main() -> int:
    if not shutil.which("gh"):
        print("[ERROR] GitHub CLI is not installed: https://cli.github.com/", file=sys.stderr)
        return 1
    try:
        run(["gh", "auth", "status"])
        print("\nLatest hourly update runs:\n")
        run(["gh", "run", "list", "--workflow", WORKFLOW, "--limit", "6"])
        latest = run(
            ["gh", "run", "list", "--workflow", WORKFLOW, "--limit", "1", "--json", "databaseId,status,conclusion,url"],
            capture=True,
        )
        rows = json.loads(latest.stdout or "[]")
        if not rows:
            print("\n[WARN] No workflow run was found. Push the workflow and run configure_hourly_update.bat.")
            return 2
        item = rows[0]
        print(f"\nLatest: {item.get('status')} / {item.get('conclusion')}\n{item.get('url') or ''}")
        if item.get("conclusion") == "failure":
            print("\nFailed-step log:\n")
            subprocess.run(["gh", "run", "view", str(item["databaseId"]), "--log-failed"], check=False)
            return 1
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] GitHub Actions status check failed ({exc.returncode}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

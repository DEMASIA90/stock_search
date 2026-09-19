from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / ".tools" / "github-cli"
USER_AGENT = "stock_search-hourly-updater/1.0"


def _existing_gh() -> Path | None:
    system = shutil.which("gh")
    if system:
        return Path(system)

    executable = "gh.exe" if os.name == "nt" else "gh"
    if TOOLS_DIR.exists():
        candidates = sorted(TOOLS_DIR.rglob(executable))
        if candidates:
            return candidates[0]

    if os.name == "nt":
        extra_candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "gh.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GitHub CLI" / "gh.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "GitHub CLI" / "gh.exe",
        ]
        for candidate in extra_candidates:
            if candidate.is_file():
                return candidate
    return None


def _windows_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64"}:
        return "amd64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    raise RuntimeError(f"지원하지 않는 Windows CPU 아키텍처입니다: {platform.machine()}")


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _download_portable_windows() -> Path:
    arch = _windows_arch()
    release_url = "https://api.github.com/repos/cli/cli/releases/latest"
    with urllib.request.urlopen(_request(release_url), timeout=30) as response:
        release = json.load(response)

    assets = release.get("assets") if isinstance(release, dict) else None
    if not isinstance(assets, list):
        raise RuntimeError("GitHub CLI 최신 릴리스 정보를 읽지 못했습니다.")

    suffix = f"_windows_{arch}.zip"
    asset = next(
        (
            item
            for item in assets
            if isinstance(item, dict)
            and str(item.get("name") or "").lower().endswith(suffix)
            and item.get("browser_download_url")
        ),
        None,
    )
    if not asset:
        raise RuntimeError(f"GitHub CLI Windows {arch} portable ZIP을 찾지 못했습니다.")

    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gh-install-", dir=str(TOOLS_DIR)) as temp_dir:
        temp = Path(temp_dir)
        archive = temp / str(asset["name"])
        request = urllib.request.Request(
            str(asset["browser_download_url"]),
            headers={"User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=90) as response, archive.open("wb") as target:
            shutil.copyfileobj(response, target)
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(temp / "extract")

        gh = next((path for path in (temp / "extract").rglob("gh.exe") if path.is_file()), None)
        if gh is None:
            raise RuntimeError("다운로드한 GitHub CLI ZIP 안에서 gh.exe를 찾지 못했습니다.")

        version = str(release.get("tag_name") or "latest").lstrip("v") or "latest"
        install_dir = TOOLS_DIR / version
        if install_dir.exists():
            shutil.rmtree(install_dir)
        source_dir = gh.parent.parent if gh.parent.name.lower() == "bin" else gh.parent
        shutil.copytree(source_dir, install_dir)

    installed = next((path for path in install_dir.rglob("gh.exe") if path.is_file()), None)
    if installed is None:
        raise RuntimeError("GitHub CLI portable 설치 후 gh.exe를 찾지 못했습니다.")
    return installed


def _try_winget_install() -> Path | None:
    winget = shutil.which("winget")
    if not winget:
        return None
    base = [
        winget,
        "install",
        "--id",
        "GitHub.cli",
        "-e",
        "--source",
        "winget",
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--silent",
    ]
    for command in (base + ["--disable-interactivity"], base):
        subprocess.run(command, check=False)
        installed = _existing_gh()
        if installed:
            return installed
    return None


def ensure_gh(*, verbose: bool = True) -> Path:
    """Return a usable GitHub CLI, installing a project-local Windows copy if needed.

    The preferred fallback is a portable ZIP under .tools/github-cli, so the setup
    does not require administrator privileges and does not depend on PATH refreshes.
    """
    found = _existing_gh()
    if found:
        return found

    if os.name != "nt":
        raise RuntimeError(
            "GitHub CLI(gh)를 찾지 못했습니다. https://cli.github.com/ 에서 설치한 뒤 다시 실행하세요."
        )

    if verbose:
        print("[INFO] GitHub CLI가 없어 프로젝트 폴더에 portable gh.exe를 자동 설치합니다...")
    try:
        installed = _download_portable_windows()
        if verbose:
            print(f"[OK] GitHub CLI portable 설치 완료: {installed}")
        return installed
    except Exception as exc:
        if verbose:
            print(f"[WARN] GitHub CLI portable 자동 설치 실패: {exc}")
            print("[INFO] winget이 있으면 GitHub CLI 설치를 한 번 더 시도합니다...")

    installed = _try_winget_install()
    if installed:
        if verbose:
            print(f"[OK] GitHub CLI 설치 완료: {installed}")
        return installed

    raise RuntimeError(
        "GitHub CLI 자동 설치에 실패했습니다. 네트워크/보안 프로그램이 GitHub Releases를 "
        "차단하는지 확인하거나 https://cli.github.com/ 에서 GitHub CLI를 설치한 뒤 다시 실행하세요."
    )

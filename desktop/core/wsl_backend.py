from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess


_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def is_windows_host() -> bool:
    return os.name == "nt"


def wsl_executable() -> str:
    return shutil.which("wsl.exe") or shutil.which("wsl") or "wsl.exe"


def wsl_available() -> bool:
    return bool(shutil.which("wsl.exe") or shutil.which("wsl"))


def windows_to_wsl_path(path: str) -> str:
    if not path:
        return path
    norm = os.path.normpath(path)
    if not _WIN_DRIVE_RE.match(norm):
        return norm.replace("\\", "/")
    drive = norm[0].lower()
    tail = norm[2:].replace("\\", "/")
    return f"/mnt/{drive}{tail}"


def maybe_to_wsl_path(arg: str) -> str:
    if not isinstance(arg, str):
        return str(arg)
    if _WIN_DRIVE_RE.match(arg):
        return windows_to_wsl_path(arg)
    return arg


def prepare_subprocess(
    cmd: list[str],
    cwd: str | None = None,
    use_wsl_backend: bool = False,
    wsl_distro: str = "",
) -> tuple[list[str], str | None]:
    normalized = [str(part) for part in cmd]
    if not (use_wsl_backend and is_windows_host()):
        return normalized, cwd

    linux_cmd = [maybe_to_wsl_path(part) for part in normalized]
    script = " ".join(shlex.quote(part) for part in linux_cmd)

    if cwd:
        linux_cwd = windows_to_wsl_path(cwd)
        script = f"cd {shlex.quote(linux_cwd)} && {script}"

    wrapped = [wsl_executable()]
    distro = (wsl_distro or "").strip()
    if distro:
        wrapped += ["-d", distro]
    wrapped += ["bash", "-lc", script]
    return wrapped, None


def command_exists(cmd: str, use_wsl_backend: bool = False, wsl_distro: str = "") -> bool:
    if not cmd:
        return False
    if use_wsl_backend and is_windows_host():
        exec_cmd = [wsl_executable()]
        distro = (wsl_distro or "").strip()
        if distro:
            exec_cmd += ["-d", distro]
        exec_cmd += ["bash", "-lc", f"command -v {shlex.quote(cmd)}"]
        result = subprocess.run(
            exec_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
        return result.returncode == 0
    return bool(shutil.which(cmd))


def resolve_wsl_python(wsl_distro: str = "") -> str:
    if not is_windows_host():
        return ""
    exec_cmd = [wsl_executable()]
    distro = (wsl_distro or "").strip()
    if distro:
        exec_cmd += ["-d", distro]
    script = (
        'if [ -x "$HOME/miniconda3/bin/python" ]; then '
        'printf "%s" "$HOME/miniconda3/bin/python"; '
        'elif [ -x "$HOME/anaconda3/bin/python" ]; then '
        'printf "%s" "$HOME/anaconda3/bin/python"; '
        'elif command -v python3 >/dev/null 2>&1; then '
        'command -v python3; '
        'elif command -v python >/dev/null 2>&1; then '
        'command -v python; '
        'fi'
    )
    try:
        result = subprocess.run(
            exec_cmd + ["bash", "-lc", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()

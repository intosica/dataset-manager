"""Optional packages: install them into a private virtual environment or the user's system site-packages.

The app itself keeps working without them; features that need a package stay disabled until it is importable.
Installing into a venv keeps the (read-only) flatpak runtime untouched, so the default target is a venv next to
the app's data. Whichever target is chosen, `activate()` makes the packages importable in this process, so the
capability checks and the collection run see them without a restart.
"""
from __future__ import annotations

import glob
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths


# ---- locations ----
def venv_dir(settings) -> Path:
    p = settings.venv_dir.strip()
    return Path(paths.resolve(p)) if p else paths.data_dir() / "venv"


def _bin_dir(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def venv_python(venv: Path) -> Path:
    return _bin_dir(venv) / ("python.exe" if os.name == "nt" else "python3")


def site_dir(venv: Path) -> Path | None:
    for pattern in ("lib/python*/site-packages", "lib64/python*/site-packages", "Lib/site-packages"):
        found = sorted(Path(p) for p in glob.glob(str(venv / pattern)))
        if found:
            return found[0]
    return None


def system_site() -> str:
    try:
        import site
        return site.getusersitepackages()
    except Exception:
        return ""


def _version(venv: Path) -> str:
    """'3.13' from pyvenv.cfg; '' when the file is missing."""
    try:
        for line in (venv / "pyvenv.cfg").read_text("utf-8").splitlines():
            if line.lower().startswith("version_info"):
                return ".".join(line.split("=", 1)[1].strip().split(".")[:2])
    except OSError:
        pass
    return ""


def _matches_runtime(venv: Path) -> bool:
    want = f"{sys.version_info.major}.{sys.version_info.minor}"
    have = _version(venv)
    return not have or have == want          # no cfg = probably not a venv we created; try anyway


def describe_target(settings) -> str:
    return "this user's system site-packages" if settings.install_target == "system" else f"virtual environment at {venv_dir(settings)}"


def describe(settings) -> str:
    if settings.install_target == "system":
        return f"system site-packages ({system_site() or 'unknown'})"
    v = venv_dir(settings)
    return f"virtual environment {v}" + ("" if v.exists() else " (not created yet)")


def activate(settings) -> str:
    """Put the chosen environment on sys.path. Returns a short note for the log ('' when nothing happened)."""
    extra = ""
    if settings.install_target == "venv":
        venv = venv_dir(settings)
        d = site_dir(venv)
        if not d or not d.is_dir():
            return ""
        if not _matches_runtime(venv):
            return (f"Ignoring {venv}: it was built with Python {_version(venv)}, this app runs "
                    f"{sys.version_info.major}.{sys.version_info.minor}.")
        extra = str(d)
    else:
        extra = system_site()
    if extra and os.path.isdir(extra) and extra not in sys.path:
        sys.path.append(extra)                 # append: never shadow what the runtime already ships
    importlib.invalidate_caches()
    return ""


# ---- running pip ----
def _pip_works(python: str) -> bool:
    try:
        return subprocess.run([python, "-m", "pip", "--version"], capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _bootstrap_pip(python: str, user: bool, log) -> None:
    cmd = [python, "-m", "ensurepip", "--upgrade"] + (["--user"] if user else [])
    log("Bootstrapping pip: " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    for line in (proc.stdout + proc.stderr).strip().splitlines()[-6:]:
        log("  " + line)


def _run(cmd: list[str], log, ctx=None) -> int:
    log("$ " + " ".join(cmd))
    env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    except OSError as exc:
        log(f"Could not start pip: {exc}")
        return 1
    for line in proc.stdout:
        if ctx is not None and ctx.cancelled:
            proc.terminate()
            log("Cancelled.")
            return 130
        line = line.rstrip()
        if line:
            log(line)
    return proc.wait()


def install(settings, packages: list[str], ctx) -> bool:
    """pip install the packages into the chosen target. Blocking; call it from a worker thread."""
    log = ctx.log
    if settings.install_target == "venv":
        venv = venv_dir(settings)
        python = str(venv_python(venv))
        if not Path(python).exists():
            venv.parent.mkdir(parents=True, exist_ok=True)
            log(f"Creating a virtual environment in {venv} …")
            code = _run([sys.executable, "-m", "venv", str(venv)], log, ctx)
            if code:
                log("Could not create the virtual environment (python3-venv / ensurepip missing?).")
                return False
        if not _pip_works(python):
            _bootstrap_pip(python, False, log)
        cmd = [python, "-m", "pip", "install", "--upgrade", *packages]
        user = False
    else:
        python = sys.executable
        if not _pip_works(python):
            _bootstrap_pip(python, True, log)
        cmd = [python, "-m", "pip", "install", "--upgrade", "--user", *packages]
        user = True
    code = _run(cmd, log, ctx)
    if code != 0:
        log(f"pip exited with {code}. Nothing else was changed.")
        return False
    note = activate(settings)
    if note:
        log(note)
    if user:                                   # user site may not have been on sys.path when the app started
        extra = system_site()
        if extra and os.path.isdir(extra) and extra not in sys.path:
            sys.path.append(extra)
        importlib.invalidate_caches()
    log("Installed: " + ", ".join(packages))
    return True


def run_extra(command: str, ctx) -> bool:
    """Post-install steps such as `playwright install chromium` (run with the active interpreter)."""
    exe = "playwright"
    venv = venv_dir(ctx.settings) if getattr(ctx, "settings", None) else None
    if venv is not None and (vb := _bin_dir(venv) / "playwright").exists():
        exe = str(vb)
    elif shutil.which("playwright"):
        exe = shutil.which("playwright")
    parts = command.split()
    if parts and parts[0] == "playwright":
        parts[0] = exe
    return _run(parts, ctx.log, ctx) == 0

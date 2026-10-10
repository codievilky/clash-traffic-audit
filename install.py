#!/usr/bin/env python3
"""Install or manage the user-level macOS LaunchAgent without changing proxy settings."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import socket
import subprocess
import sys

from clash_audit import BASE, DEFAULT_SOCKET, VERSION

LABEL = "cn.codievilky.clash-audit"
FILES = ("clash_audit.py", "index.html", "app.js", "style.css")


def build_job(base, interpreter, port, socket_path):
    return {
        "Label": LABEL,
        "ProgramArguments": [str(interpreter), str(base / "clash_audit.py"),
                             "--base", str(base), "--port", str(port), "--socket", str(socket_path)],
        "WorkingDirectory": str(base),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }


def write_atomic(path, data, mode=0o600):
    tmp = path.with_name(path.name + ".install-tmp")
    with tmp.open("xb") as f:
        f.write(data)
    try:
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def run(*args, check=True):
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, check=check)


def installed_base(agent):
    if not agent.exists():
        return None
    with agent.open("rb") as f:
        job = plistlib.load(f)
    args = job.get("ProgramArguments", [])
    if job.get("Label") != LABEL or len(args) < 2 or Path(args[1]).name != "clash_audit.py":
        raise ValueError("Existing LaunchAgent is not a recognized clash-audit installation")
    return Path(args[1]).parent.resolve()


def install(base, agent, job, source):
    existing = installed_base(agent)
    if existing is not None and existing != base:
        raise ValueError("An installation already exists at %s; use --base with that path" % existing)
    if base == source:
        raise ValueError("Install outside the source checkout")
    if existing is None and any((base / n).exists() for n in FILES):
        raise ValueError("Destination contains unmanaged program files; choose another --base")
    interpreter = job["ProgramArguments"][0]
    subprocess.run([interpreter, "-c", "import sys;sys.exit(0 if sys.version_info >= (3,9) else 1)"], check=True)
    target = "gui/%d/%s" % (os.getuid(), LABEL)
    loaded = run("print", target, check=False).returncode == 0
    # Retain the existing code/plist in memory so a failed installation can roll back.
    original = {n: (base / n).read_bytes() if (base / n).exists() else None for n in FILES}
    old_agent = agent.read_bytes() if agent.exists() else None
    if loaded:
        run("bootout", target)
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", int(job["ProgramArguments"][5])))
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        agent.parent.mkdir(parents=True, exist_ok=True)
        for n in FILES:
            write_atomic(base / n, (source / n).read_bytes(), 0o700 if n.endswith(".py") else 0o600)
        write_atomic(agent, plistlib.dumps(job))
        run("enable", target)
        run("bootstrap", "gui/%d" % os.getuid(), str(agent))
    except Exception:
        if run("print", target, check=False).returncode == 0:
            run("bootout", target, check=False)
        for n, old in original.items():
            if old is not None:
                write_atomic(base / n, old, 0o700 if n.endswith(".py") else 0o600)
            elif (base / n).exists():
                (base / n).unlink()
        if old_agent is not None:
            write_atomic(agent, old_agent)
        elif agent.exists():
            agent.unlink()
        if loaded and old_agent is not None:
            run("bootstrap", "gui/%d" % os.getuid(), str(agent), check=False)
        raise
    print("Installed %s at %s" % (VERSION, base))
    print("Dashboard: http://127.0.0.1:%s/" % job["ProgramArguments"][5])
    print("Starts after this user logs in. No proxy settings were modified.")


def uninstall(base, agent):
    existing = installed_base(agent)
    if existing is None:
        print("No LaunchAgent installed; no files removed.")
        return
    if existing != base:
        raise ValueError("Installed at %s; supply that path with --base" % existing)
    target = "gui/%d/%s" % (os.getuid(), LABEL)
    if run("print", target, check=False).returncode == 0:
        run("bootout", target)
    agent.unlink()
    for n in FILES:
        p = base / n
        if p.is_file():
            p.unlink()
    print("Removed the LaunchAgent and program files. Historical data preserved at %s" % (base / "data"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall", "start", "stop", "status"), nargs="?", default="install")
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--python", default=sys.executable, help="Python 3.9+ executable used by launchd")
    parser.add_argument("--port", type=int, default=18797)
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without writing or invoking launchctl")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    base = args.base.expanduser().resolve()
    if base in (Path.home().resolve(), Path("/")):
        parser.error("--base must be a dedicated application directory")
    interpreter = shutil.which(args.python)
    if not interpreter:
        parser.error("Python executable not found")
    agent = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    job = build_job(base, Path(interpreter).absolute(), args.port, args.socket.expanduser().resolve())
    if args.dry_run:
        print(json.dumps({"action": args.action, "base": str(base), "agent": str(agent), "job": job}, indent=2))
        return
    if sys.platform != "darwin":
        parser.error("The LaunchAgent installer supports macOS only")
    if os.getuid() == 0:
        parser.error("Run as your logged-in user, without sudo")
    os.umask(0o077)
    target = "gui/%d/%s" % (os.getuid(), LABEL)
    if args.action == "install":
        install(base, agent, job, Path(__file__).resolve().parent)
    elif args.action == "uninstall":
        uninstall(base, agent)
    elif args.action == "status":
        result = run("print", target, check=False)
        print(result.stdout or result.stderr)
        sys.exit(result.returncode)
    elif args.action == "stop":
        run("disable", target)
        if run("print", target, check=False).returncode == 0:
            run("bootout", target)
        print("Stopped and disabled automatic startup. Data retained.")
    elif args.action == "start":
        if installed_base(agent) != base:
            raise ValueError("Install first, or specify the installed --base")
        run("enable", target)
        if run("print", target, check=False).returncode != 0:
            run("bootstrap", "gui/%d" % os.getuid(), str(agent))
        print("Started and enabled automatic startup.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print("Error: %s" % exc, file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        sys.exit(1)

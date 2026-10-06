"""Detached local supervision and an optional managed cron startup entry."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import threading
import time

REPO = Path(__file__).resolve().parent.parent
PYTHON = REPO / ".venv/bin/python"


def _runtime_path():
    directories = [str(PYTHON.parent), str(Path.home() / ".local/bin")]
    for executable in ("codex", "node"):
        found = shutil.which(executable)
        if found:
            directories.append(str(Path(found).parent))
    directories += os.get_exec_path() + ["/usr/local/bin", "/usr/bin", "/bin"]
    safe = (directory for directory in directories if os.path.isabs(directory)
            and all(character.isalnum() or character in "/._-+@ " for character in directory))
    return os.pathsep.join(dict.fromkeys(safe))


def _paths(config_path):
    config = Path(config_path).expanduser().resolve(strict=True)
    settings = json.loads(config.read_text())
    state = Path(settings.get("state_dir", ".state")).expanduser()
    if not state.is_absolute():
        state = config.parent / state
    state = state.resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    return config, state


def _private_file(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "a", buffering=1)


def _locked(path):
    with _private_file(path) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


def _command(config, supervisor=False):
    module = ["oak.service", "--supervise"] if supervisor else ["oak", "run"]
    return [str(PYTHON), "-u", "-m", *module, "--config", str(config)]


def _identity(pid):
    try:
        process = Path("/proc") / str(int(pid))
        start = process.joinpath("stat").read_text().rsplit(")", 1)[1].split()[19]
        args = process.joinpath("cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        return start, [os.fsdecode(arg) for arg in args], process.joinpath("cwd").resolve(strict=True)
    except (OSError, TypeError, ValueError, IndexError):
        return None


def _record(state):
    try:
        value = json.loads((state / "oak-service.json").read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _verified(record, config):
    if not record or record.get("repo") != str(REPO) or record.get("config") != str(config):
        return False
    pid = record.get("pid")
    identity = _identity(pid)
    if identity != (record.get("start"), _command(config, supervisor=True), REPO):
        return False
    try:
        return os.getpgid(pid) == pid and os.getsid(pid) == pid
    except (OSError, TypeError):
        return False


def _status(config, state):
    record = _record(state)
    if not _verified(record, config):
        return {"status": "unverified" if _locked(state / "oak-service.lock") else "stopped"}
    worker = record.get("worker_pid")
    worker_running = bool(worker and _identity(worker) == (
        record.get("worker_start"), _command(config), REPO))
    return {"status": "running", "pid": record["pid"], "worker_pid": worker,
            "worker_running": worker_running, "restarts": record.get("restarts", 0),
            "phase": record.get("phase"), "log": str(state / "oak-service.log")}


def service(action, config_path):
    """Return management status; never read or print credentials or private logs."""
    config, state = _paths(config_path)
    if action == "status":
        return _status(config, state)
    if action == "start":
        status = _status(config, state)
        if status["status"] == "running":
            return status
        if status["status"] == "unverified" or _locked(state / "gateway.lock"):
            raise RuntimeError("A service or gateway already owns this state directory.")
        if not os.access(PYTHON, os.X_OK):
            raise RuntimeError("Run Oak bootstrap first; .venv/bin/python is missing.")
        env = os.environ.copy()
        env["PATH"] = _runtime_path()
        with _private_file(state / "oak-service.log") as log:
            child = subprocess.Popen(_command(config, supervisor=True), cwd=REPO,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     start_new_session=True, close_fds=True, env=env)
        for _ in range(50):
            status = _status(config, state)
            if status["status"] == "running":
                return status
            if child.poll() is not None:
                break
            time.sleep(0.1)
        raise RuntimeError("Oak supervisor did not start; inspect its private service log.")
    if action == "stop":
        record = _record(state)
        if not _verified(record, config):
            if _locked(state / "oak-service.lock"):
                raise RuntimeError("Cannot verify the service process; refusing to signal it.")
            return {"status": "stopped"}
        with contextlib.suppress(ProcessLookupError):
            # Only the supervisor signals the worker. A second SIGTERM can
            # cancel the worker again while its async cleanup is in progress.
            os.kill(record["pid"], signal.SIGTERM)
        for _ in range(200):
            if not _verified(record, config):
                return {"status": "stopped"}
            time.sleep(0.1)
        if _verified(record, config):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(record["pid"], signal.SIGKILL)
        for _ in range(30):
            if not _verified(record, config):
                return {"status": "stopped"}
            time.sleep(0.1)
        raise RuntimeError("Oak supervisor has not stopped.")
    if action == "install-autostart":
        if not os.access(PYTHON, os.X_OK):
            raise RuntimeError("Run Oak bootstrap before installing autostart.")
        marker = "# OAK_BOT_MANAGED:" + hashlib.sha256(str(config).encode()).hexdigest()[:16]
        # crontab contents may contain secrets; retain them in memory, never log.
        current = subprocess.run(["crontab", "-l"], capture_output=True)
        if current.returncode == 0:
            previous = current.stdout.decode()
        elif current.returncode == 1 and b"no crontab" in current.stderr.lower():
            previous = ""
        else:
            raise RuntimeError("Cannot read crontab; existing entries were left unchanged.")
        retained = "".join(line for line in previous.splitlines(keepends=True)
                           if not line.rstrip().endswith(marker))
        if retained and not retained.endswith("\n"):
            retained += "\n"
        startup = [str(PYTHON), "-u", "-m", "oak.service", "--start", "--config", str(config)]
        command = ("cd " + shlex.quote(str(REPO)) + " && PATH=" + shlex.quote(_runtime_path()) + " " +
                   shlex.join(startup) + " >> " +
                   shlex.quote(str(state / "oak-service.log")) + " 2>&1")
        # Cron treats percent as a newline even inside shell quotes.
        updated = retained + "@reboot " + command.replace("%", "\\%") + " " + marker + "\n"
        with _private_file(state / "oak-service.log"):
            pass
        installed = subprocess.run(["crontab", "-"], input=updated.encode(), capture_output=True)
        if installed.returncode:
            raise RuntimeError("Could not install the managed Oak autostart entry.")
        return {"status": "autostart-installed", "manager": "cron", "marker": marker}
    raise ValueError("Unknown service action")


def _supervise(config_path):
    os.umask(0o077)
    config, state = _paths(config_path)
    with _private_file(state / "oak-service.lock") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        stop = threading.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop.set())
        record = {"pid": os.getpid(), "start": _identity(os.getpid())[0],
                  "repo": str(REPO), "config": str(config), "restarts": 0}
        pid_file = state / "oak-service.json"

        def save(phase, worker=None):
            identity = _identity(worker.pid) if worker else None
            record.update(phase=phase, worker_pid=worker.pid if worker else None,
                          worker_start=identity[0] if identity else None)
            temporary = state / f".oak-service-{os.getpid()}.tmp"
            temporary.write_text(json.dumps(record) + "\n")
            temporary.chmod(0o600)
            temporary.replace(pid_file)

        child = None
        delay = 1
        try:
            save("starting")
            with _private_file(state / "oak-service.log") as log:
                while not stop.is_set():
                    if _locked(state / "gateway.lock"):
                        save("waiting-for-gateway-lock")
                        stop.wait(5)
                        continue
                    launched = time.monotonic()
                    child = subprocess.Popen(_command(config), cwd=REPO, stdin=subprocess.DEVNULL,
                                             stdout=log, stderr=log, close_fds=True)
                    save("running", child)
                    while child.poll() is None and not stop.wait(0.5):
                        pass
                    if stop.is_set():
                        break
                    record["restarts"] += 1
                    log.write(f"Oak gateway exited ({child.returncode}); restarting.\n")
                    save("restarting")
                    if time.monotonic() - launched > 30:
                        delay = 1
                    if stop.wait(delay):
                        break
                    delay = min(delay * 2, 30)
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    # start_new_session gives this supervisor an owned group.
                    # Killing only the worker could leave its runtime alive.
                    os.killpg(os.getpid(), signal.SIGKILL)
            current = _record(state)
            if current and current.get("pid") == os.getpid():
                pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--supervise", action="store_true")
    action.add_argument("--start", action="store_true")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    if args.start:
        service("start", args.config)
    else:
        _supervise(args.config)

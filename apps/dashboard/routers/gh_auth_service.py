"""Interactive ``gh auth login`` for the dashboard Global Settings pane."""

from __future__ import annotations

import os
import pty
import re
import select
import subprocess
import threading
import time
from typing import Any

_DEVICE_CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
_DEVICE_URL_RE = re.compile(r"(https://github\.com/login/device\S*)")

_GH_LOGIN_CMD = [
    "gh",
    "auth",
    "login",
    "-h",
    "github.com",
    "-p",
    "https",
    "-s",
    "repo",
    "-s",
    "read:org",
    "-s",
    "gist",
    "-w",
]

_lock = threading.Lock()
_job: dict[str, Any] | None = None


def _new_job() -> dict[str, Any]:
    return {
        "running": False,
        "done": False,
        "ok": False,
        "error": None,
        "lines": [],
        "device_code": None,
        "device_url": "https://github.com/login/device",
        "proc": None,
        "master_fd": None,
        "thread": None,
        "started_at": None,
    }


def _append_line(job: dict[str, Any], text: str, *, kind: str = "") -> None:
    line = text.rstrip()
    if not line:
        return
    job["lines"].append({"text": line, "kind": kind})
    m = _DEVICE_CODE_RE.search(line)
    if m:
        job["device_code"] = m.group(1)
    m = _DEVICE_URL_RE.search(line)
    if m:
        job["device_url"] = m.group(1).rstrip(").")


def _job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "running": bool(job.get("running")),
        "done": bool(job.get("done")),
        "ok": bool(job.get("ok")),
        "error": job.get("error"),
        "lines": list(job.get("lines") or []),
        "device_code": job.get("device_code"),
        "device_url": job.get("device_url"),
    }


def _reader_loop(job: dict[str, Any]) -> None:
    master = job.get("master_fd")
    proc = job.get("proc")
    if master is None or proc is None:
        return
    buf = ""
    try:
        while True:
            if proc.poll() is not None:
                try:
                    while True:
                        r, _, _ = select.select([master], [], [], 0)
                        if not r:
                            break
                        chunk = os.read(master, 4096)
                        if not chunk:
                            break
                        buf += chunk.decode("utf-8", errors="replace")
                except OSError:
                    pass
                break
            r, _, _ = select.select([master], [], [], 0.25)
            if not r:
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                _append_line(job, line)
        if buf.strip():
            _append_line(job, buf)
    finally:
        try:
            os.close(master)
        except OSError:
            pass
        job["master_fd"] = None
        rc = proc.wait()
        job["running"] = False
        job["done"] = True
        job["ok"] = rc == 0
        if rc != 0 and not job.get("error"):
            job["error"] = f"gh auth login exited with code {rc}"
        if job["ok"]:
            _append_line(job, "GitHub authentication succeeded.", kind="ok")
        elif job.get("error"):
            _append_line(job, str(job["error"]), kind="err")


def start_login() -> dict[str, Any]:
    global _job
    with _lock:
        if _job and _job.get("running"):
            return {"started": False, "error": "login_already_running", **_job_snapshot(_job)}
        cancel_login_locked()
        job = _new_job()
        _job = job
        try:
            master, slave = pty.openpty()
            proc = subprocess.Popen(
                _GH_LOGIN_CMD,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
            )
            os.close(slave)
        except FileNotFoundError:
            job["done"] = True
            job["error"] = "gh CLI not found on PATH"
            _append_line(job, job["error"], kind="err")
            return {"started": False, "error": job["error"], **_job_snapshot(job)}
        except OSError as exc:
            job["done"] = True
            job["error"] = str(exc)
            _append_line(job, f"Failed to start gh auth login: {exc}", kind="err")
            return {"started": False, "error": job["error"], **_job_snapshot(job)}

        job["proc"] = proc
        job["master_fd"] = master
        job["running"] = True
        job["started_at"] = time.time()
        _append_line(job, "Starting GitHub device login…", kind="step")
        t = threading.Thread(target=_reader_loop, args=(job,), daemon=True)
        job["thread"] = t
        t.start()
        return {"started": True, **_job_snapshot(job)}


def send_input(text: str = "\n") -> dict[str, Any]:
    with _lock:
        job = _job
        if not job or not job.get("running"):
            return {"sent": False, "error": "no_active_login", **_job_snapshot(job or _new_job())}
        master = job.get("master_fd")
        if master is None:
            return {"sent": False, "error": "no_tty", **_job_snapshot(job)}
        try:
            os.write(master, text.encode("utf-8"))
        except OSError as exc:
            return {"sent": False, "error": str(exc), **_job_snapshot(job)}
        if text == "\n":
            _append_line(job, "Sent Enter (open browser / continue).", kind="step")
        return {"sent": True, **_job_snapshot(job)}


def cancel_login_locked() -> None:
    global _job
    job = _job
    if not job:
        return
    proc = job.get("proc")
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    master = job.get("master_fd")
    if master is not None:
        try:
            os.close(master)
        except OSError:
            pass
    job["running"] = False
    if not job.get("done"):
        job["done"] = True
        job["ok"] = False
        job["error"] = "cancelled"
        _append_line(job, "Login cancelled.", kind="err")


def cancel_login() -> dict[str, Any]:
    with _lock:
        cancel_login_locked()
        return get_login_status()


def login_with_token(token: str) -> dict[str, Any]:
    global _job
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "token_required"}
    with _lock:
        if _job and _job.get("running"):
            return {"ok": False, "error": "login_already_running"}
        job = _new_job()
        _job = job
        _append_line(job, "Authenticating with personal access token…", kind="step")
        try:
            proc = subprocess.run(
                [
                    "gh",
                    "auth",
                    "login",
                    "-h",
                    "github.com",
                    "-p",
                    "https",
                    "--with-token",
                ],
                input=token + "\n",
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            job["done"] = True
            job["error"] = "gh CLI not found on PATH"
            _append_line(job, job["error"], kind="err")
            return {"ok": False, "error": job["error"], **_job_snapshot(job)}
        except subprocess.TimeoutExpired:
            job["done"] = True
            job["error"] = "gh auth login timed out"
            _append_line(job, job["error"], kind="err")
            return {"ok": False, "error": job["error"], **_job_snapshot(job)}

        if proc.stdout:
            for line in proc.stdout.splitlines():
                _append_line(job, line)
        if proc.stderr:
            for line in proc.stderr.splitlines():
                _append_line(job, line, kind="err")
        job["done"] = True
        job["ok"] = proc.returncode == 0
        if not job["ok"]:
            job["error"] = (proc.stderr or proc.stdout or "token login failed").strip()
            _append_line(job, job["error"], kind="err")
        else:
            _append_line(job, "GitHub authentication succeeded.", kind="ok")
        return {"ok": job["ok"], "error": job.get("error"), **_job_snapshot(job)}


def get_login_status() -> dict[str, Any]:
    with _lock:
        job = _job or _new_job()
        return _job_snapshot(job)


def refresh_server_gh_auth() -> None:
    """Re-run dashboard startup gh auth preflight after a successful login."""
    try:
        import server as srv

        srv._check_gh_auth()
    except Exception:
        pass

"""Google Colab CLI 底层调用、空闲看门狗与会话管理（统一供 h3studio.py 与 backends.py 调用）。"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "h3skill-"
KEEP = Path.home() / ".config" / "h3skill" / "keep"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def colab_exe():
    for c in (shutil.which("colab"), Path.home() / ".local/bin/colab.exe", Path.home() / ".local/bin/colab"):
        if c and Path(c).is_file():
            return str(c)
    raise RuntimeError("找不到 colab 指令，请先运行 uv tool install google-colab-cli")


def colab(*args, timeout=600, check=True):
    try:
        p = subprocess.run(
            [colab_exe(), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=ENV, timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        if check:
            raise RuntimeError(f"colab {' '.join(map(str, args))} 超过 {timeout} 秒没有回应")
        return ""
    out = (p.stdout or "") + (p.stderr or "")
    if check and p.returncode != 0:
        raise RuntimeError(f"colab {' '.join(map(str, args))} 失败（{p.returncode}）：\n{out[-2000:]}")
    return out


def token_ok():
    return (Path.home() / ".config/colab-cli/token.json").is_file()


def usage():
    out = colab("usage", timeout=120, check=False)
    m = re.search(r"([\d.]+)\s*(?:compute units|运算单元|units)", out, re.I)
    return (float(m.group(1)) if m else None), out.strip()


def sessions():
    out = colab("sessions", timeout=120, check=False)
    return re.findall(rf"({PREFIX}[\w-]+)", out), out.strip()


def keep_file(session):
    return KEEP / f"{session}.json"


def set_keep(session, expires, pid=None):
    KEEP.mkdir(parents=True, exist_ok=True)
    old = json.loads(keep_file(session).read_text(encoding="utf-8")) if keep_file(session).exists() else {}
    keep_file(session).write_text(
        json.dumps({"session": session, "expires": expires, "watchdog_pid": pid or old.get("watchdog_pid")}),
        encoding="utf-8",
    )


def drop_keep(session):
    keep_file(session).unlink(missing_ok=True)


def pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_watchdog(session):
    info = json.loads(keep_file(session).read_text(encoding="utf-8"))
    if pid_alive(info.get("watchdog_pid")):
        return
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200
    else:
        kw["start_new_session"] = True
    cli_script = ROOT / "scripts/h3studio.py"
    p = subprocess.Popen([sys.executable, str(cli_script), "_watchdog", "--session", session], env=ENV, **kw)
    set_keep(session, info.get("expires"), p.pid)


def remote_exec(session, code, timeout=120, tries=3):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code.rstrip("\n") + "\nprint('H3EXEC_OK')\n")
    try:
        for k in range(tries):
            out = colab("exec", "-s", session, "-f", f.name, "--timeout", timeout, timeout=timeout + 30, check=False)
            if "H3EXEC_OK" in out:
                return out.replace("H3EXEC_OK", "").strip()
            print(f"…exec 没有回应，重试（{k + 1}/{tries}）", file=sys.stderr, flush=True)
        raise RuntimeError("colab exec 连续没有回应")
    finally:
        os.unlink(f.name)


def watchdog(session):
    from .backends import idle_probe
    log = KEEP / f"{session}.log"

    def say(msg):
        with log.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")

    say("watchdog started")
    while True:
        time.sleep(30)
        if not keep_file(session).exists():
            say("keep file removed: exit")
            return
        info = json.loads(keep_file(session).read_text(encoding="utf-8"))
        if info.get("expires") is None or time.time() < info["expires"]:
            continue
        try:
            if "H3STOP True" not in remote_exec(session, idle_probe(), tries=1):
                continue
        except RuntimeError:
            continue
        if not keep_file(session).exists() or json.loads(keep_file(session).read_text(encoding="utf-8")) != info:
            continue
        say("time is up: releasing GPU")
        colab("stop", "-s", session, timeout=300, check=False)
        drop_keep(session)
        say("released")
        return

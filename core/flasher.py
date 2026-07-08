import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional


def _esptool_cmd(args: list) -> list:
    """Build an esptool subprocess command that works both frozen and unfrozen."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-esptool"] + args
    return [sys.executable, "-m", "esptool"] + args


@dataclass
class FlashTask:
    chip: str = "esp32"
    port: str = ""
    baud: int = 921600
    flash_mode: str = "dio"
    flash_freq: str = "40m"
    flash_size: str = "detect"
    files: Dict[int, str] = field(default_factory=dict)       # offset -> path (main write)
    extra_files: Dict[int, str] = field(default_factory=dict) # offset -> path (separate write)
    erase_first: bool = False


class Flasher:
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None

    def flash(
        self,
        task: FlashTask,
        log_cb: Callable[[str], None],
        progress_cb: Callable[[int], None],
        done_cb: Callable[[bool, str], None],
    ):
        threading.Thread(
            target=self._run,
            args=(task, log_cb, progress_cb, done_cb),
            daemon=True,
        ).start()

    def cancel(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()

    # ── internals ─────────────────────────────────────────────────────

    def _run(self, task, log_cb, progress_cb, done_cb):
        try:
            if task.erase_first:
                log_cb("Erasing flash — this may take a moment...")
                ok, msg = self._exec(self._base(task) + ["erase_flash"], log_cb, None)
                if not ok:
                    done_cb(False, f"Erase failed: {msg}")
                    return
                log_cb("Erase complete.")

            cmd = self._base(task) + self._write_args(task)
            log_cb("Starting flash...")
            ok, msg = self._exec(cmd, log_cb, progress_cb)

            if ok and task.extra_files:
                log_cb("Writing additional partition(s)...")
                extra_cmd = self._base(task) + self._write_args_extra(task)
                ok, msg = self._exec(extra_cmd, log_cb, progress_cb)

            done_cb(ok, msg)

        except Exception as exc:
            done_cb(False, str(exc))

    def _base(self, task):
        return _esptool_cmd([
            "--chip", task.chip,
            "--port", task.port,
            "--baud", str(task.baud),
        ])

    def _write_args(self, task):
        args = [
            "write_flash", "-z",
            "--flash_mode", task.flash_mode,
            "--flash_freq", task.flash_freq,
            "--flash_size", task.flash_size,
        ]
        for offset, path in sorted(task.files.items()):
            args += [hex(offset), path]
        return args

    def _write_args_extra(self, task):
        args = [
            "write_flash", "-z",
            "--flash_mode", task.flash_mode,
            "--flash_freq", task.flash_freq,
            "--flash_size", task.flash_size,
        ]
        for offset, path in sorted(task.extra_files.items()):
            args += [hex(offset), path]
        return args

    def _exec(self, cmd, log_cb, progress_cb):
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        self._process = subprocess.Popen(cmd, **popen_kwargs)

        for raw in self._process.stdout:
            line = raw.rstrip()
            if not line:
                continue
            log_cb(line)
            if progress_cb:
                m = re.search(r"\((\d+) %\)", line)
                if m:
                    progress_cb(int(m.group(1)))

        self._process.wait()
        success = self._process.returncode == 0
        return success, ("Done" if success else "esptool exited with errors — see log above")

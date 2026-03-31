"""Ripgrep installation workflow service."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass
class InstallRipgrepResult:
    success: bool
    message: str


def install_ripgrep_with_winget(emit_log: Callable[[str], None]) -> InstallRipgrepResult:
    if not shutil.which("winget"):
        emit_log("找不到 winget，無法自動安裝。")
        return InstallRipgrepResult(False, "找不到 winget，請先安裝 App Installer 或手動安裝 ripgrep。")

    package_ids = [
        "BurntSushi.ripgrep.MSVC",
        "BurntSushi.ripgrep.GNU",
        "BurntSushi.ripgrep",
    ]
    last_exit_code: int | None = None
    last_lines: list[str] = []

    for package_id in package_ids:
        try:
            cmd = ["winget", "install", "-e", "--id", package_id]
            emit_log(f"執行命令: {' '.join(cmd)}")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            captured_lines: list[str] = []
            if process.stdout is not None:
                for raw_line in iter(process.stdout.readline, ""):
                    line = raw_line.rstrip("\r\n")
                    if not line:
                        continue
                    captured_lines.append(line)
                    emit_log(line)
            return_code = process.wait()
        except Exception as exc:
            emit_log(f"安裝失敗: {exc}")
            return InstallRipgrepResult(False, f"安裝失敗：{exc}")

        if return_code in {0, 2316632107}:
            if return_code == 2316632107:
                emit_log(f"{package_id} 已安裝且無可升級版本，視為成功。")
            emit_log(f"安裝流程結束: {package_id} exit code {return_code}")
            return InstallRipgrepResult(True, f"ripgrep 可用（{package_id}），已刷新引擎狀態。")

        last_exit_code = return_code
        last_lines = captured_lines
        emit_log(f"{package_id} 安裝失敗，exit code {return_code}，改試下一個 ID。")

    message = ""
    if last_lines:
        message = last_lines[-1].strip()
    if not message:
        message = f"winget 安裝失敗，exit code={last_exit_code}"
    emit_log(f"安裝流程結束: 全部候選 ID 失敗，最後 exit code {last_exit_code}")
    return InstallRipgrepResult(False, message)

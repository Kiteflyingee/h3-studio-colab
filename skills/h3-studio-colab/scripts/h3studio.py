#!/usr/bin/env python3
"""Plugin-mode forwarding entrypoint -> delegates to repository root scripts/h3studio.py."""
from __future__ import annotations
import os
import runpy
import sys
from pathlib import Path


def _resolve_repo_root() -> Path:
    env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env_root:
        candidate = Path(env_root).resolve()
        if (candidate / "scripts" / "h3studio.py").is_file() and (candidate / "studio").is_dir():
            return candidate
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "scripts" / "h3studio.py").is_file() and (candidate / "studio").is_dir():
        return candidate
    raise FileNotFoundError(
        "未找到插件根目录下的 scripts/h3studio.py；请完整克隆本仓库或通过插件市场命令安装，不要只单独复制 skills/h3-studio-colab 子目录。"
    )


def main() -> None:
    repo_root = _resolve_repo_root()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    runpy.run_path(str(repo_root / "scripts" / "h3studio.py"), run_name="__main__")


if __name__ == "__main__":
    main()

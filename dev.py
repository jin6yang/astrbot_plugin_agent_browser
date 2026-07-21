"""开发环境启动器：使用项目根目录 .venv 的 Python 启动 WebUI 或 CLI。

用法：
    python dev.py webui    # 启动管理面板 WebUI
    python dev.py cli      # 启动 CLI

本脚本自身只依赖标准库，任意 Python 均可运行；
它会自动查找项目根目录 .venv 中的解释器来启动真正的服务。
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

MODULES = {
    "webui": "obscura_manager.server",
    "cli": "obscura_manager.cli",
}


def find_venv_python() -> Path | None:
    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",  # Windows
        ROOT / ".venv" / "bin" / "python",          # Linux / macOS
    ]
    return next((path for path in candidates if path.is_file()), None)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", choices=sorted(MODULES), help="要启动的目标")
    parser.add_argument("--python", default="", help="显式指定 Python 解释器路径（默认自动查找 .venv）")
    args = parser.parse_args()

    if args.python:
        python = args.python
    else:
        venv_python = find_venv_python()
        if venv_python:
            python = str(venv_python)
        else:
            python = sys.executable
            print(f"[dev] 未找到 .venv，回退到当前解释器: {python}")

    print(f"[dev] Python: {python}")
    print(f"[dev] 启动: python -m {MODULES[args.target]}")
    return subprocess.call([python, "-m", MODULES[args.target]], cwd=str(ROOT))


if __name__ == "__main__":
    sys.exit(main())

"""PyInstaller 独立 EXE 启动入口（默认启动图形界面，传入 --cli 可调用命令行模式）。"""

from __future__ import annotations

import sys


def _entrypoint() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from korabli_minimap.cli import main as cli_main

        return cli_main(sys.argv[2:])

    from korabli_minimap.gui import main as gui_main

    return gui_main()


if __name__ == "__main__":
    sys.exit(_entrypoint())

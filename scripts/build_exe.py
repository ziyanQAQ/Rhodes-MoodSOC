"""scripts/build_exe.py —— 把**图形界面**打包成一个 Windows exe（PyInstaller，onedir）。

用法（在仓库根）：

```bash
.venv/Scripts/python.exe -m pip install pyinstaller     # 构建期工具，只需装一次（≥6.15 才支持 Python 3.14）
.venv/Scripts/python.exe scripts/build_exe.py           # → dist/RhodesMoodSOC/RhodesMoodSOC.exe
.venv/Scripts/python.exe scripts/build_exe.py --clean   # 先删 build/ 与 dist/
```

## 打进去什么 / 不打进去什么

用户口径（2026-09）：**"exe 文件内不保留测试数据"**。所以这里是"白名单"打法 ——
只有显式 `--add-data` 的东西才会进包，别的一律没有：

| | 内容 |
|---|---|
| **打进** | Python 运行时 + tkinter/tcl；本项目的包（`data` / `mood_soc` / `store` / `ui` / `api`）；`data/operators.txt`（**运行时唯一要读的文本**：干员名册，92 KB） |
| **不打** | `tests/`、`scripts/`、`documents/`、`scenarios/`、`.venv/`、`.git/`、`__pycache__/`；**`resources/` 整目录**（MAA 样例、v3/v4 导入样例、需求 docx、核对报告、数据字典） |

⚠️ 连带后果：界面冷启动**不再自动载入示例排班**（`ui/app.py` 里那句 `if SAMPLE.exists()`
在 exe 里恒为 False）⇒ 双击后是空界面，用户在界面里「导入排班…」。
`data/paths.py: project_root()` 负责让 exe 认得自己的解包目录（`sys._MEIPASS`）。

## 打完怎么验

```bash
dist/RhodesMoodSOC/RhodesMoodSOC.exe --smoke    # 退出码 0 ＝ 窗口建起来了（--windowed 没控制台，只能看退出码）
dist/RhodesMoodSOC/RhodesMoodSOC.exe            # 真实启动：应当出一个空界面
```
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "RhodesMoodSOC"

#: 要放进包里的**数据文件**（源路径, 包内目录）——只有这些，别的一律不进。
ADD_DATA = (
    (ROOT / "data" / "operators.txt", "data"),
)

#: 明确排除的模块（PyInstaller 只跟 import 走，这几项本来也不会进；写出来是为了"意图可见"，
#: 万一哪天有人加了 `import tests...` 也会立刻在构建期报出来而不是悄悄把测试塞进包）。
EXCLUDES = ("tests", "scripts", "documents", "scenarios", "unittest",
            "pydoc", "doctest", "tkinter.test")


def _pyinstaller_available() -> bool:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        return False
    return True


def _strip_runtime_tests(app_dir: Path) -> list:
    """删掉随 Tcl/Tk 一起被收进来的**测试用文件**（用户口径：exe 里不留测试数据）。

    目前只有一样：Tcl 标准库自带的 `tcltest`（`_internal/tcl8/8.5/tcltest-2.5.7.tm` 等）——
    它只在"跑 Tcl 自己的测试套件"时才会被 source，界面运行完全用不到。
    （注意：Python 的 `tkinter.test` 已经用 `--exclude-module` 排掉了，这里是 Tcl 那一侧。）
    """
    removed = []
    for path in app_dir.rglob("tcltest*"):
        try:
            path.unlink()
            removed.append(str(path.relative_to(app_dir)))
        except OSError:
            pass
    return removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="打包图形界面为 exe（onedir）")
    parser.add_argument("--clean", action="store_true", help="先删 build/ 与 dist/")
    parser.add_argument("--name", default=NAME, help=f"exe 名字（默认 {NAME}）")
    parser.add_argument("--icon", default=None, help="可选：.ico 路径")
    parser.add_argument("--console", action="store_true",
                        help="保留控制台窗口（排查启动问题时用）")
    args = parser.parse_args(argv)

    if not _pyinstaller_available():
        print("缺少 PyInstaller（构建期工具，不在运行时依赖里）：\n"
              "    .venv/Scripts/python.exe -m pip install pyinstaller\n"
              "（Python 3.14 需要 PyInstaller ≥ 6.15）", file=sys.stderr)
        return 2

    entry = ROOT / "ui" / "__main__.py"          # 与 `python -m ui` 同一条入口
    for src, _dest in ADD_DATA:
        if not src.exists():
            print(f"要打进包的数据文件不存在：{src}", file=sys.stderr)
            return 2

    if args.clean:
        for d in (ROOT / "build", ROOT / "dist"):
            shutil.rmtree(d, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",                                   # 文件夹形态：启动快、杀软误报少
        "--console" if args.console else "--windowed",
        "--name", args.name,
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),             # .spec 也放 build/（别脏仓库根）
        "--paths", str(ROOT),                          # 让分析器找到 data/ mood_soc/ store/ ui/
    ]
    for src, dest in ADD_DATA:
        cmd += ["--add-data", f"{src}{os.pathsep}{dest}"]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    if args.icon:
        cmd += ["--icon", str(args.icon)]
    cmd.append(str(entry))

    print("执行：", " ".join(cmd), "\n")
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        return r.returncode

    app_dir = ROOT / "dist" / args.name
    exe = app_dir / f"{args.name}.exe"

    stripped = _strip_runtime_tests(app_dir)
    if stripped:
        print("已从包里删掉运行时自带的测试文件：")
        for name in stripped:
            print("   ", name)

    print(f"\n完成：{exe}")
    print(f"自检：{exe} --smoke   （退出码 0 ＝ 界面建得起来）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

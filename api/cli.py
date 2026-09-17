"""api/cli.py —— **一次性调用**（脚本 / 调试 / 不想管进程生命周期时用）。

```bash
# 握手：看协议版本与全部可用 op
python -m api.cli --op capabilities

# 直接算：内联布局 → 所有干员心情（此刻 + 周期末 + 曲线 + 红脸 + 瓶颈）
python -m api.cli --op load_schedule --args '{"facilities":[{"type":"制造站","level":3,"operators":["泡泡","黍","路人甲"]}]}' \
                  --then '{"op":"moods","args":{"at":[0,8,24],"include_trajectory":true}}'

# args 从文件读（`@路径`）或从管道读（`@-`）
python -m api.cli --op load_file --args @my_layout.json --then '{"op":"moods"}'
cat req.json | python -m api.cli --op moods --args @-

# 一次性做完一串（按顺序执行，共享同一个会话）
python -m api.cli --script steps.json          # steps.json = [{"op":...}, {"op":...}]
```

输出：**成功 = 最后一步的结果 JSON（stdout）**；`--all` 时输出每一步的完整响应数组。
失败 = `{"ok": false, "error": {...}}` 打到 **stderr** 并返回退出码 1
（这样 `moods = $(python -m api.cli ...)` 这种写法不会把错误当数据）。

⚠️ 每次调用都是**新会话**：`--op` 与 `--then` 之间共享状态，跨进程不共享。
要连续交互请用 `python -m api.server`。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from store.session import Session

from . import protocol
from .ops import handle


def _read_args(spec: Optional[str]) -> dict:
    """`--args` 的三种写法：内联 JSON / `@文件` / `@-`（stdin）。"""
    if not spec:
        return {}
    text = spec
    if spec == "@-":
        text = sys.stdin.read()
    elif spec.startswith("@"):
        text = Path(spec[1:]).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--args 不是合法 JSON：{exc}")
    if not isinstance(data, dict):
        raise SystemExit("--args 应当是一个 JSON 对象")
    return data


def run(steps: Sequence[dict], *, show_all: bool = False, quiet: bool = False) -> int:
    """按顺序执行若干步（共享一个会话），打印结果。返回退出码。"""
    session = Session()
    responses: List[dict] = []
    for step in steps:
        op = step.get("op")
        if not isinstance(op, str) or not op:
            raise SystemExit(f"步骤缺少 op：{step!r}")
        req_id = step.get("id")
        try:
            data = handle(session, op, step.get("args") or {})
        except BaseException as exc:                      # noqa: BLE001 —— 统一转成协议错误
            resp = protocol.make_response(op, error=protocol.error_of(exc), req_id=req_id)
            print(protocol.dumps(resp), file=sys.stderr)
            return 1
        resp = protocol.make_response(op, data, req_id=req_id)
        responses.append(resp)
        if not quiet:
            print(f"[api] {op} ok", file=sys.stderr, flush=True)
    out = responses if show_all else (responses[-1]["data"] if responses else {})
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Rhodes-MoodSOC 程序接口（一次性调用）")
    ap.add_argument("--op", help="要执行的操作名（见 --op capabilities）")
    ap.add_argument("--args", help='参数 JSON；也支持 @文件 或 @-（stdin）')
    ap.add_argument("--then", action="append", default=[],
                    help="再执行一步（可重复）；写法同请求对象：'{\"op\":\"moods\"}'")
    ap.add_argument("--script", help="步骤数组的 JSON 文件：[{op,args}, ...]")
    ap.add_argument("--all", action="store_true", help="输出每一步的完整响应数组")
    ap.add_argument("--quiet", action="store_true", help="不往 stderr 打进度")
    args = ap.parse_args(argv)

    steps: List[dict] = []
    if args.script:
        loaded = json.loads(Path(args.script).read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            loaded = [loaded]
        if not isinstance(loaded, list):
            raise SystemExit("--script 应当是一个步骤数组")
        steps.extend(loaded)
    if args.op:
        steps.append({"op": args.op, "args": _read_args(args.args)})
    for extra in args.then:
        try:
            steps.append(json.loads(extra))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--then 不是合法 JSON：{exc}")
    if not steps:
        ap.print_help()
        return 0
    return run(steps, show_all=args.all, quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())

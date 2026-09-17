"""api/server.py —— **常驻 NDJSON 服务**（推荐给 Rust 的用法）。

```bash
python -m api.server            # stdin/stdout 都是 NDJSON；stderr 是日志
python -m api.server --quiet    # 不往 stderr 打握手信息
```

## 为什么常驻

`data/skills_data.py`（755 条 buff / 250 条 clause 的对象图）import 一次约 0.3~0.5s，
一次性 CLI 每次都要付这笔钱；常驻进程只付一次，之后每次 `moods` 的耗时就是
"重算一轮"的耗时（周期 1 约 0.2s、周期 3 约 0.7s）。

## 会话是**有状态的**

一次进程 = 一个 `store.session.Session`。调用顺序有意义：

```
capabilities → load_schedule / load_file → set_*（改设置）→ moods / trajectory
```

`load_*` 会**重置**会话（起点心情、锚点、设置都从新排班重新读）。

## 与 Rust 的握手（务必做）

```jsonc
{"id": 1, "op": "capabilities"}
// → {"id":1,"op":"capabilities","ok":true,"protocol":1,
//    "data":{"protocol":1,"operations":[...],"stateful":true,"loaded":false}}
```

两边 `protocol` 不一致就**别继续**——否则会悄悄按不同语义算。

实现细节：stdout **行缓冲**（每行 flush，Rust 侧 `read_line` 不会卡）；
`errors="replace"` 防止非法 UTF-8 输入把进程打崩；任何 op 的异常都转成
`{"ok":false,"error":{...}}` 并且**进程继续活着**（一个坏请求不该杀掉长连接）。
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional, TextIO

from store.session import Session

from . import protocol
from .ops import handle


def serve(stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None,
          stderr: Optional[TextIO] = None, *, quiet: bool = False) -> int:
    """跑常驻循环，返回退出码（0 = 正常收到 quit/EOF）。"""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    session = Session()
    if not quiet:
        print("api.server 就绪：一行一个 JSON 请求（{\"op\":\"capabilities\"} 开始）。"
              "结束：{\"op\":\"quit\"} 或 EOF", file=stderr, flush=True)

    for line in stdin:
        try:
            req = protocol.parse_request(line)
        except ValueError as exc:
            _write(stdout, protocol.make_response(None, error=protocol.error_of(exc)))
            continue
        if req is None:
            continue
        op = req.get("op")
        req_id = req.get("id")
        if op in protocol.QUIT_OPS:
            _write(stdout, protocol.make_response(op, {"bye": True}, req_id=req_id))
            return 0
        if not isinstance(op, str) or not op:
            _write(stdout, protocol.make_response(
                op, error=protocol.error_of(ValueError('缺 "op" 字段')),
                req_id=req_id))
            continue
        try:
            data = handle(session, op, req.get("args"))
            resp = protocol.make_response(op, data, req_id=req_id)
        except BaseException as exc:                      # noqa: BLE001 —— 一个坏请求不许杀进程
            resp = protocol.make_response(op, error=protocol.error_of(exc), req_id=req_id)
            if not quiet:
                print(f"[api] {op} 失败：{resp['error']['type']}: {resp['error']['message']}",
                      file=stderr, flush=True)
        _write(stdout, resp)
    return 0


def _write(stdout: TextIO, obj: dict) -> None:
    stdout.write(protocol.dumps(obj) + "\n")
    stdout.flush()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rhodes-MoodSOC 程序接口（NDJSON 常驻服务）")
    ap.add_argument("--quiet", action="store_true", help="不往 stderr 打日志")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    return serve(quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())

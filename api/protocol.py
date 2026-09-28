"""api/protocol.py —— **线协议**：请求 / 响应的解析与组装（server 与 cli 共用一处）。

## 线格式：NDJSON（一行一个 JSON 对象，UTF-8，不转义中文）

```
→ {"id": 1, "op": "moods", "args": {"at": [0, 12], "cycles": 2}}
← {"id": 1, "op": "moods", "ok": true, "protocol": 1, "data": {...}}
← {"id": 2, "op": "nope",  "ok": false, "protocol": 1,
   "error": {"type": "bad_request", "message": "未知操作 'nope'；可用：…"}}
```

选择 NDJSON 而不是 LSP 的 `Content-Length` 头：**一行一请求**可以用 `echo` / `nc` /
`jq` 直接手测，Rust 侧也只需要 `BufReader::read_line` + `serde_json::from_str`。

## 约定

- `id` 原样回显（字符串/数字/对象都行），缺省不回填；
- **一次一行，绝不把响应写到 stderr**：stderr 留给日志（双方都能看见）；
- 空行忽略；`{"op":"quit"}` 或 EOF 结束进程；
- 每个响应都带 `protocol`，长连接里调用方可以随时发现版本漂移。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from . import PROTOCOL_VERSION


class ProtocolMismatch(Exception):
    """调用方声明的协议版本与服务端不一致。"""

    def __init__(self, expected: object, actual: int = PROTOCOL_VERSION):
        self.expected = expected
        self.actual = actual
        super().__init__(f"协议版本不匹配：调用方 {expected!r}，服务端 {actual}")

#: 结束常驻进程的伪 op（不查 op 表）。
QUIT_OPS = ("quit", "exit", "__quit__")


def parse_request(line: str) -> Optional[dict]:
    """一行文本 → 请求 dict；空行返回 `None`；不是 JSON 对象则抛 `ValueError`。"""
    text = (line or "").strip()
    if not text:
        return None
    try:
        req = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"不是合法 JSON：{exc}") from exc
    if not isinstance(req, dict):
        raise ValueError("请求应当是一个 JSON 对象，如 {\"op\": \"capabilities\"}")
    return req


def make_response(op: Optional[str], data: Any = None, *, error: Optional[dict] = None,
                  req_id: Any = None) -> dict:
    """组装响应（成功 / 失败同一个形状，只差 `ok` 与 `data`/`error`）。"""
    out: dict = {"op": op, "ok": error is None, "protocol": PROTOCOL_VERSION}
    if req_id is not None:
        out["id"] = req_id
    if error is None:
        out["data"] = data
    else:
        out["error"] = error
    return out


def error_of(exc: BaseException) -> dict:
    """异常 → `{"type","message"}`。参数类错误归 `bad_request`，其余用类名。"""
    if isinstance(exc, ProtocolMismatch):
        return {
            "type": "protocol_mismatch",
            "message": str(exc),
            "expected": exc.expected,
            "actual": exc.actual,
        }
    kind = "bad_request" if isinstance(exc, (ValueError, KeyError, TypeError)) else type(exc).__name__
    return {"type": kind, "message": str(exc) or type(exc).__name__}


def dumps(obj: Any) -> str:
    """一行 JSON（不转义中文、不留多余空白）。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


__all__ = ["QUIT_OPS", "ProtocolMismatch", "parse_request", "make_response", "error_of", "dumps"]

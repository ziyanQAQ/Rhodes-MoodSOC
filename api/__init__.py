"""api —— **程序接口**：把图形界面能做的事全部暴露成「JSON 进 / JSON 出」。

给外部程序（目前主要是 Rust 侧的求解器）调用，**不依赖 tkinter**，可以在无图形环境跑。

## 两种用法（同一套 op，同一份状态语义）

### 1. 常驻进程（推荐：省掉每次冷启动的 0.3~0.5s import）

```bash
python -m api.server          # 一行一个请求，一行一个响应（NDJSON over stdio）
```

```jsonc
// → 请求（stdin 一行）
{"id": 1, "op": "load_schedule", "args": {"facilities": [...]}}
// ← 响应（stdout 一行）
{"id": 1, "op": "load_schedule", "ok": true, "data": {...}}
```

### 2. 一次性调用（脚本 / 调试 / 不想管进程生命周期）

```bash
python -m api.cli --op capabilities
python -m api.cli --op moods --args '{"at": [0, 8, 24], "cycles": 2}'
python -m api.cli --op load_schedule --args @layout.json --then '{"op":"moods"}'
```

## 契约（稳定面，改这里要升 `PROTOCOL_VERSION`）

| 请求字段 | 必填 | 说明 |
|---|---|---|
| `op` | ✓ | 操作名，见 `api.ops.OPERATIONS` |
| `args` | | 该操作的参数对象（缺省 `{}`） |
| `id` | | 原样回显，便于调用方配对（任意 JSON 值） |

| 响应字段 | 说明 |
|---|---|
| `id` / `op` | 与请求相同 |
| `ok` | `true` 成功 / `false` 失败 |
| `data` | 成功时的结果 |
| `error` | 失败时的 `{"type","message"}`（`ValueError` → `"bad_request"`，其余 → 类名） |
| `protocol` | 协议版本号（每次响应都带，便于长连接中途校验） |

**先把 `capabilities` 调一次**：它返回 `protocol` 与全部 op 名，版本不一致就报错，
别让双方"悄悄按不同语义算"。

## 每个 op 都兼容两种"时刻"写法

- **绝对小时**：`0` / `8.5` / `"36"`（从周期起点算起，跨周期连续递增）；
- **钟点字符串**：`"08:30"` / `"1:05"`（**周期内**时刻；`"24:00"` = 周期末），
  与图形界面上的时刻标签同义（`start_clock` 只影响显示，不影响换算）。

## 文档

完整字段表、逐 op 示例、Rust 侧接入样例见 `documents/08-程序接口.md`。
"""
from __future__ import annotations

#: 线协议版本。**4 → 5（2026-10）**：新增 `set_seat_lock` / `clear_seat_locks` 两个 op，
#: 并给 `set_slots` 加了 `manual` 参数（信封形状没变 ⇒ 老调用方照旧可用，但**能力表变了**，
#: 所以按项目先例升一号：v1.0→v1.1 加 op 时也是 3→4）。调用方用 `capabilities` 判断。
PROTOCOL_VERSION = 5

__all__ = ["PROTOCOL_VERSION"]

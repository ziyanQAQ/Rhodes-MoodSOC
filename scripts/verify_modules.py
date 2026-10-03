"""scripts/verify_modules.py —— **模块边界自检**（七模块重构第 1 步的产物；只读扫描源码）。

用法：
    .venv/Scripts/python.exe scripts/verify_modules.py          # 全绿 → 退出码 0，有红 → 1
    .venv/Scripts/python.exe scripts/verify_modules.py -v       # 打印每一条通过项
    .venv/Scripts/python.exe scripts/verify_modules.py --list   # 只列"现状待办"（白名单）

口径见 `documents/14-架构总览.md` §9~§13。一句话：

> **每个模块独占一份数据，别的模块只读；要动别人的数据，必须调人家的写入口。**

## 六条检查（对应文档 §9.4 的 R1~R8）

| # | 检查 | 说明 |
|---|---|---|
| C1 | **写权限唯一** | 找出"某个函数写了**别的模块**的字段"的每一处；未登记的 ⇒ 红 |
| C2 | **白名单不过期** | 登记过但已不违规的 ⇒ 红（逼着修完就删，防止"临时例外"变永久） |
| C3 | **依赖只向下** | `mood_soc/` 不许 import `store/`/`ui/`/`api/`（转发壳登记在案） |
| C4 | **转发壳不长大** | 转发壳文件 ≤ 40 行 |
| C5 | **模块定义自洽** | 写入口表里的模块名必须都在 `MODULE_FIELDS` 里；六个功能模块各有数据 |
| C6 | **与文档一致** | `14-架构总览.md` §9 的模块清单与三条边界规则必须与本文件一致 |

## 怎么判"写了这个字段"

只认**真正的状态载体**上的写，避免误报：

| 文件 | 判定 |
|---|---|
| `store/session.py` | `self.<字段> = …`（`self` 就是那个状态载体） |
| 其他任何文件 | `<...>session.<字段> = …`（`session` / `self.session` / `app.session`） |

于是 `BaseLayout` 实例自己的配置字段（`world.idle_to_dorm = …`）、
图表的自有属性（`self.traj = …`）都不会被误判 —— 它们不是 `Session` 的字段。

⚠️ **`schedule` 是唯一的共享字段**：它同时装着"时间轴的班次元信息"与"布局的 facilities"，
所以 `时间轴` 与 `布局` 都算它的主人（`SHARED_FIELDS`）。第 2 步按 §10 把它拆开。

⚠️ **白名单是"待办清单"，不是免罪符**：`KNOWN_CROSS_WRITES` 里的每一条都是已知不合规，
第 2 步修掉一条就删一条（C2 会盯着你删）。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:                                  # Windows 控制台默认 GBK，打不出「」这类字符
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                     # noqa: BLE001
    pass

# ============================================================================
# 一、模块定义：每个模块"拥有"哪些数据字段
# ============================================================================
#: 模块 → 它**独占**的 `Session` 字段（只有本模块的实现可以写）
MODULE_FIELDS = {
    "时间轴": {"schedule", "cycles", "start_clock"},
    "布局": {"facilities", "manual"},
    "心情": {"initial_moods", "mood_events"},
    "名册": {"detached", "pool", "training", "elite", "level"},
    "自动化": {
        "idle_to_dorm", "idle_protected_slots", "idle_blacklist",
        "idle_globals", "idle_entries",
        "entry_events", "entry_swap_with", "entry_scope",
        "entry_restore_back", "entry_when", "entry_per_shift",
    },
    "编排": {"traj", "loaded", "_resume_from", "last_recompute_ms"},
}

#: 字段 → 拥有它的模块（一个字段可能有多个主人，见 `SHARED_FIELDS`）
FIELD_OWNER = {f: {m} for m, fields in MODULE_FIELDS.items() for f in fields}
#: `schedule` 同时装"时间轴的班次元信息"与"布局的 facilities" ⇒ 两家都是主人
SHARED_FIELDS = {"schedule"}
FIELD_OWNER["schedule"] = {"时间轴", "布局"}

#: **编排**是唯一允许跨块写的模块（导入要重置全部状态、重算要落轨迹）
ORCHESTRATOR = "编排"
#: **外壳**：只读，一个数据字段都不许写（它只有视图状态）
SHELL = "外壳"

# ============================================================================
# 二、写入口 → 它所属的模块（这份表就是"边界"的可执行形式）
# ============================================================================
#: `store/session.py` 里 Session 的方法 → 它服务的模块（未列出的按"只读方法"处理）
SESSION_METHOD_OWNER = {
    # —— 时间轴 ——
    "set_cycles": "时间轴", "set_timeline": "时间轴", "set_start_clock": "时间轴",
    # —— 布局 ——
    "set_slots": "布局", "set_facility_slots": "布局", "set_room_level": "布局",
    "replace_facilities": "布局", "facilities_of": "布局",
    # 2026-10：显式上锁 / 解锁（只改 `manual` 台账，不动占位）
    "set_seat_lock": "布局", "clear_seat_locks": "布局", "locked_seats_of": "布局",
    # 2026-10：界面整批摆位的落地口（Q15=(a)：界面摆位也写手动台账）
    "apply_manual_shifts": "布局", "manual_dorm_editor_state": "布局",
    # —— 心情 ——
    "set_initial_mood": "心情", "set_initial_moods": "心情", "set_mood_at": "心情",
    "clear_mood_events": "心情", "restore_imported_moods": "心情",
    # —— 名册 ——
    "set_detached": "名册", "add_detached": "名册", "remove_detached": "名册",
    "set_training": "名册", "_remove_from_slots": "名册", "fill_from_pool": "名册",
    "elite_badges": "名册", "operator_obj": "名册", "elite_text": "名册",
    # —— 自动化 ——
    "idle_entry_list": "自动化", "idle_groups": "自动化", "idle_count": "自动化",
    "entry_candidates": "自动化",
    # —— 编排 ——
    "load_paths": "编排", "load_data": "编排", "load_layout": "编排",
    "_sync_from_schedule": "编排", "recompute": "编排", "recompute_inputs": "编排",
    # 2026-10：导入装配时解决"名单里的人却占着位置"（名单优先：摘人 + 解该位锁）
    "_resolve_imported_detached": "编排",
    "compute_trajectory": "编排", "adopt": "编排", "closure": "编排",
    "_world_digest": "编排", "_segment_signatures": "编排",
    "describe": "编排", "settings_dict": "编排", "status_text": "编排",
}

#: 其他层里的写入口 → 它服务的模块
WRITE_ENTRY_OWNER = {
    # —— 时间轴 ——
    "apply_shift_hours": "时间轴", "apply_start_clock": "时间轴", "op_set_timeline": "时间轴",
    "_apply_clock": "时间轴", "_on_cycles": "时间轴", "on_cycles_changed": "时间轴",
    # —— 布局 ——
    "op_set_slots": "布局", "op_set_room_level": "布局", "_apply_facilities": "布局",
    # 2026-10：显式上锁 / 解锁的两个 op（它们只调 `Session` 的锁原语，不直接写字段，
    #          所以 C1 本来不会抓；按"登记在案"的惯例一并列出）
    "op_set_seat_lock": "布局", "op_clear_seat_locks": "布局",
    # 2026-10：看板只做展示 ⇒ `on_room_left`（点房间头改等级）已删，登记一并撤掉；
    #           `on_slot_left` 保留（界面不再绑定，但它是"手动入宿写进布局"的程序化入口）
    "on_slot_left": "布局",
    # —— 心情 ——
    "op_set_initial_moods": "心情", "op_set_moods": "心情", "op_set_mood_at": "心情",
    "op_clear_mood_events": "心情", "op_restore_imported_moods": "心情",
    "_ask_and_set_mood": "心情",
    # —— 名册 ——
    "op_set_training": "名册", "op_fill_from_pool": "名册", "op_set_detached": "名册",
    # —— 自动化 ——
    "op_set_idle_to_dorm": "自动化", "op_set_entry_events": "自动化",
    "apply_idle_to_dorm": "自动化", "apply_entry_event": "自动化",
    # —— 编排 ——
    "op_load_schedule": "编排", "op_load_file": "编排", "op_load_files": "编排",
    "op_load_json": "编排", "op_moods": "编排", "op_closure": "编排",
    "load_paths": "编排", "load_data": "编排", "load_layout": "编排",
    "load_schedule": "编排", "load_schedule_ex": "编排", "import_data": "编排",
    "import_file": "编排", "load_schedule_from_imports": "编排",
    "shifts_from_import": "编排", "shift_from_facilities": "编排",
    "_publish_recompute": "编排", "_settle_recalc": "编排",
}

#: `ui/app.py` 的 `apply_*` 落地回调 → 模块（**混合型的要拆，见白名单**）
APPLY_CALLBACK_OWNER = {
    "apply_batch": "布局",            # ⚠️ 实际三合一（布局+心情+名册），第 3 步拆开
    "apply_idle_to_dorm": "自动化",
    "apply_entry_event": "自动化",
    # 2026-10：设置中心「闲置入宿」页 ③ 手动入宿编辑器的落地口（写的是布局 + 手动台账：
    #          `Session.set_facility_slots` / `set_seat_lock`）⇒ 归「布局」
    "apply_manual_dorm": "布局",
    "apply_start_clock": "时间轴",
    "apply_shift_hours": "时间轴",
}

#: `ui/app.py` 里的 `Session` 代理 setter → 它写的那些字段的归属模块。
#: ⚠️ 外壳不该碰数据，但**这层代理是有意为之的接缝**（`ui/app.py:311-349` 的注释：
#: "状态与重算都在 Session，界面只是它的视图"）。第 3 步把这层换成"每模块一个
#: 只读视图 + 显式写入口"后，这份表应随之清空。
APP_SESSION_PROXY_OWNER = {
    "schedule": "布局", "traj": "编排", "initial_moods": "心情", "mood_events": "心情",
    "cycles": "时间轴", "idle_entries": "自动化", "idle_globals": "自动化",
    "entry_swap_with": "自动化", "entry_scope": "自动化", "entry_restore_back": "自动化",
    "entry_when": "自动化", "entry_per_shift": "自动化",
}

#: 编排的入口（唯一允许写任何模块字段的一族）
ORCHESTRATOR_FUNCS = {
    "load_paths", "load_data", "load_layout", "load_schedule", "load_schedule_ex",
    "recompute", "recompute_inputs", "compute_trajectory", "adopt", "_sync_from_schedule",
    "closure", "import_data", "import_file", "load_schedule_from_imports",
    "shifts_from_import", "shift_from_facilities", "_publish_recompute", "_settle_recalc",
    "op_load_schedule", "op_load_file", "op_load_files", "op_load_json",
}

# ============================================================================
# 三、现状白名单：**第 2 步的待办清单**
# ============================================================================
#: `(文件, 函数, 被写的字段)`；每条都是**已知不合规**，修掉一条删一条（C2 会盯着）
KNOWN_CROSS_WRITES = {
    # ① 名册的动作直接改布局（第 2 步：改成调布局模块的写入口）
    ("store/session.py", "_remove_from_slots", "schedule"),
    ("store/session.py", "set_detached", "schedule"),
    ("store/session.py", "set_training", "schedule"),
    ("store/session.py", "fill_from_pool", "schedule"),
    # ② `apply_batch` 是"三合一"回调（布局 + 心情 + 名册）⇒ 第 3 步拆成三个
    ("ui/app.py", "apply_batch", "initial_moods"),
    ("ui/app.py", "apply_batch", "mood_events"),
}

#: 已知的向上 import（第 2 步修：把 `build_operator` 下沉到引擎自己能拿到的地方）
KNOWN_UPWARD_IMPORTS = {
    ("mood_soc/__init__.py", "store.layout"),      # PEP 562 惰性转发（兼容契约）
    ("mood_soc/scenario.py", "store.layout"),      # 转发壳
    ("mood_soc/importer.py", "store.sources"),     # 转发壳
    ("mood_soc/output.py", "store.serialize"),     # 转发壳
    ("mood_soc/maa.py", "store.maa"),              # 转发壳
}
#: 转发壳允许保留（它们是兼容契约，不是"逻辑"）
SHIM_FILES = {"mood_soc/scenario.py", "mood_soc/importer.py", "mood_soc/output.py",
              "mood_soc/maa.py", "ui/schedule.py"}

SCAN_DIRS = ("store", "api", "ui")
SCAN_ROOT_FILES = ("main.py",)
_SESSION_FILE = "store/session.py"
_DOC = "documents/14-架构总览.md"

PASS = 0
FAIL = 0
VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
LIST_ONLY = "--list" in sys.argv


def check(name: str, ok: bool, detail: str = "") -> None:
    """一条断言：通过就 +1（`-v` 时打印），失败打印原因并计入失败数。"""
    global PASS, FAIL
    if ok:
        PASS += 1
        if VERBOSE:
            print(f"  ok   {name}")
        return
    FAIL += 1
    print(f"  FAIL {name}" + (f"\n       {detail}" if detail else ""))


# ============================================================================
# 四、扫描实现
# ============================================================================
def _iter_py_files():
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            yield p
    for name in SCAN_ROOT_FILES:
        p = ROOT / name
        if p.exists():
            yield p


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _func_spans(tree: ast.Module):
    """`[(函数短名, 起行, 止行)]`（模块级函数 + 类方法都收）。"""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node.lineno, node.end_lineno or node.lineno))
    return out


def _toplevel_assignments(tree: ast.Module):
    """模块级赋值 `字段 = …` → `[(字段名, 行号)]`。"""
    out = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.append((t.id, node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.append((node.target.id, node.lineno))
    return out


#: 写操作：`X.<字段> = / += / …`，或 `X.<字段>.append(...)` 这类原地改
_WRITE_RE = re.compile(
    r"(?P<base>[A-Za-z_][\w\.]*?)\s*\.\s*(?P<field>[A-Za-z_]\w*)\s*"
    r"(?:=(?!=)|\+=|-=|\*=|/=|\|=|&=|"
    r"\.\s*(?:append|extend|insert|pop|remove|clear|update|discard|add|sort|setdefault)\s*\()")


def _func_module(rel: str, func: str) -> str:
    """函数属于哪个模块（查表；查不到返回空串＝未登记 ⇒ 只许只读）。"""
    if rel == _SESSION_FILE and func in SESSION_METHOD_OWNER:
        return SESSION_METHOD_OWNER[func]
    if rel == "ui/app.py" and func in APP_SESSION_PROXY_OWNER:
        return APP_SESSION_PROXY_OWNER[func]
    if func in APPLY_CALLBACK_OWNER:
        return APPLY_CALLBACK_OWNER[func]
    if func in WRITE_ENTRY_OWNER:
        return WRITE_ENTRY_OWNER[func]
    return ""


def _is_state_base(rel: str, base: str) -> bool:
    """这个 `base` 是不是"状态载体"（只有它上面的写才算模块越界）。"""
    if rel == _SESSION_FILE:
        return base == "self"
    return base == "session" or base.endswith(".session")


def collect_cross_writes():
    """→ `(violations, entries)`：未登记的跨模块写 / 全部跨模块写（含已登记）。"""
    violations, entries = set(), set()
    for path in _iter_py_files():
        rel = _rel(path)
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:                       # pragma: no cover
            raise AssertionError(f"{rel} 解析失败：{exc}") from exc
        lines = src.splitlines()

        for func, start, end in _func_spans(tree):
            owner = _func_module(rel, func)
            is_orchestrator = (owner == ORCHESTRATOR) or (func in ORCHESTRATOR_FUNCS)
            body = "\n".join(lines[start - 1:end])
            for m in _WRITE_RE.finditer(body):
                base, field = m.group("base"), m.group("field")
                if field not in FIELD_OWNER or not _is_state_base(rel, base):
                    continue
                if is_orchestrator or owner in FIELD_OWNER[field]:
                    continue
                key = (rel, func, field)
                entries.add(key)
                if key not in KNOWN_CROSS_WRITES:
                    violations.add(key)

        for field, _lineno in _toplevel_assignments(tree):
            if field not in FIELD_OWNER or rel == _SESSION_FILE:
                continue
            key = (rel, "<模块级>", field)
            entries.add(key)
            if key not in KNOWN_CROSS_WRITES:
                violations.add(key)
    return violations, entries


def collect_upward_imports():
    """`mood_soc/` 里 import `store`/`ui`/`api` 的地方 → `{(文件, 模块名), …}`。"""
    found = set()
    for path in sorted((ROOT / "mood_soc").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in ("store", "ui", "api"):
                    found.add((_rel(path), node.module))
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in ("store", "ui", "api"):
                        found.add((_rel(path), a.name))
    return found


# ============================================================================
# 五、六条检查
# ============================================================================
def check_write_authority():
    print("C1/C2 写权限唯一（含白名单不过期）")
    violations, entries = collect_cross_writes()
    check("没有未登记的跨模块写", not violations,
          "未登记：" + "；".join(f"{f} 的 {fn}() 写 {fld}" for f, fn, fld in sorted(violations)))
    stale = sorted(KNOWN_CROSS_WRITES - entries)
    check("KNOWN_CROSS_WRITES 没有过期条目（修完就删）", not stale,
          "已不违规：" + "；".join(f"{f} 的 {fn}() 写 {fld}" for f, fn, fld in stale))
    return violations, entries


def check_dependency_direction():
    print("C3 依赖只向下（mood_soc 不许 import 上层）")
    found = collect_upward_imports()
    bad = {x for x in found if x not in KNOWN_UPWARD_IMPORTS}
    check("没有未登记的向上 import", not bad,
          "未登记：" + "；".join(f"{f} imports {m}" for f, m in sorted(bad)))
    stale = sorted(KNOWN_UPWARD_IMPORTS - found)
    check("KNOWN_UPWARD_IMPORTS 没有过期条目", not stale,
          "已不再 import：" + "；".join(f"{f} → {m}" for f, m in stale))


def check_shims():
    print("C4 转发壳不长大（≤ 40 行）")
    for rel in sorted(SHIM_FILES):
        p = ROOT / rel
        if not p.exists():
            continue
        n = len(p.read_text(encoding="utf-8").splitlines())
        check(f"{rel} 仍只是转发（{n} 行）", n <= 40, f"{n} 行 —— 转发壳不该长逻辑")


def check_module_definitions():
    print("C5 模块定义自洽")
    known = set(MODULE_FIELDS) | {ORCHESTRATOR, SHELL}
    bad = {m for m in SESSION_METHOD_OWNER.values() if m not in known}
    bad |= {m for m in WRITE_ENTRY_OWNER.values() if m not in known}
    bad |= {m for m in APPLY_CALLBACK_OWNER.values() if m not in known}
    bad |= {m for m in APP_SESSION_PROXY_OWNER.values() if m not in known}
    check("写入口表里的模块名都在 MODULE_FIELDS 里", not bad, f"未定义：{sorted(bad)}")
    empty = [m for m in ("时间轴", "布局", "心情", "名册", "自动化", "编排")
             if not MODULE_FIELDS.get(m)]
    check("六个功能模块各有自有数据", not empty, f"没登记字段：{empty}")
    check("外壳不拥有数据字段（它只读）", SHELL not in MODULE_FIELDS)


def check_doc_consistency():
    print("C6 与文档一致（14-架构总览.md）")
    p = ROOT / _DOC
    if not p.exists():
        check("文档存在", False, f"缺少 {_DOC}")
        return
    doc = p.read_text(encoding="utf-8")
    missing = [m for m in list(MODULE_FIELDS) + [SHELL] if m not in doc]
    check("七个模块都在文档 §9 里", not missing, f"文档里找不到：{missing}")
    for phrase in ("跨模块只读", "编排是唯一的", "外壳只读", "彼此互不影响"):
        check(f"边界规则「{phrase}」在文档里", phrase in doc)


def main() -> int:
    if LIST_ONLY:
        print(f"现状待办（KNOWN_CROSS_WRITES，共 {len(KNOWN_CROSS_WRITES)} 条）：")
        for f, fn, fld in sorted(KNOWN_CROSS_WRITES):
            print(f"  · {f} 的 {fn}() 写 {fld}")
        print(f"\n已知向上 import（{len(KNOWN_UPWARD_IMPORTS)} 条，含 4 个转发壳）：")
        for f, m in sorted(KNOWN_UPWARD_IMPORTS):
            print(f"  · {f} → {m}")
        return 0
    check_write_authority()
    check_dependency_direction()
    check_shims()
    check_module_definitions()
    check_doc_consistency()
    print(f"\n模块边界自检：通过 {PASS} 条，失败 {FAIL} 条")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

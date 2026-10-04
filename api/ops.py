"""api/ops.py —— **能力表**：op 名 → 处理函数 + 参数契约（图形界面能做的一切）。

设计约定（写清楚免得后来者加歪）：

1. **一个 op 一件事**，名字用下划线小写；op 表就是契约，`capabilities` 直接把它报给调用方。
2. **入参一律 JSON 原生类型**（数字/字符串/数组/对象），进来立刻 `to_decimal()` 收敛，
   不要求调用方懂 `Decimal`；数值出参统一经 `store.serialize._num()`（6 位小数舍入，
   `inf → null`），与 CLI / 文件输出**同一套口径**。
3. **状态在 `Session` 里**：op 只做"取值 → 改 Session → 返回结果"，不自己存状态。
   需要重算的 op 内部调 `session.recompute()`（或走会重算的 Session 方法）。
4. **错误用异常**：参数不对抛 `ValueError`（调用方收到 `error.type == "bad_request"`）；
   未载入排班这类前置条件也抛 `ValueError`，信息里写清"先调哪个 op"。
5. **不许 import tkinter / `ui`**：程序接口要能在无图形环境跑（有测试盯着）。
"""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Sequence, Tuple

from mood_soc.battery import to_decimal
from mood_soc.models import normalize_entry_when

from store import serialize
from store.session import Session

from . import PROTOCOL_VERSION
from .protocol import ProtocolMismatch

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
_CLOCK_SEP = (":", "：")


def _require(**kwargs) -> None:
    """必填参数校验（缺了就报清楚是哪一个）。"""
    missing = [k for k, v in kwargs.items() if v is None]
    if missing:
        raise ValueError("缺少必填参数：" + "、".join(missing))


def parse_time(value, cycle_hours=None) -> Decimal:
    """把"时刻"参数解析成**绝对小时**。

    接受：数字 / 数字字符串（＝绝对小时，可为小数） / `"HH:MM"`（＝**周期内**时刻）。
    钟点写法：`"08:30"` → 8.5；`"1:05"` → 1.083333…；`"24:00"` → 周期末（= cycle_hours）。
    """
    if isinstance(value, str) and any(sep in value for sep in _CLOCK_SEP):
        text = value.replace("：", ":")
        hh, _, mm = text.partition(":")
        try:
            hours = Decimal(hh or "0")
            minutes = Decimal(mm or "0")
        except InvalidOperation as exc:
            raise ValueError(f"时刻写法无法识别：{value!r}（用 \"HH:MM\" 或小时数）") from exc
        if minutes < 0 or minutes >= 60:
            raise ValueError(f"分钟要在 0~59：{value!r}")
        total = hours + minutes / Decimal(60)
        if cycle_hours is not None and total > to_decimal(cycle_hours):
            raise ValueError(f"周期内时刻超出周期 {cycle_hours}h：{value!r}")
        return total
    try:
        return to_decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"时刻写法无法识别：{value!r}") from exc


def _times(value, session: Session, default=(0,)) -> List[Decimal]:
    """把 `at` 参数（单个 / 数组 / 钟点字符串）解析成**绝对小时**列表。"""
    cycle = session.schedule.cycle_hours if session.schedule else None
    if value is None:
        return [parse_time(v, cycle) for v in default]
    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError("`at` 不能是空数组")
        return [parse_time(v, cycle) for v in value]
    return [parse_time(value, cycle)]


def _require_session(session: Session) -> None:
    if session.schedule is None:
        raise ValueError("尚未载入排班：先调 load_json（或 load_schedule / load_file / load_files）")


def _num(value):
    """数值出参统一走 `store.serialize._num`（6 位小数舍入；inf → None）。"""
    return serialize._num(value)


def _num_map(values: Dict[str, object]) -> Dict[str, object]:
    return {k: _num(v) for k, v in values.items()}


def _red_face(session: Session, name: str) -> List[List[object]]:
    return [[_num(a), _num(b)] for a, b in session.red_face_spans(name)]


# ---------------------------------------------------------------------------
# 只读 op
# ---------------------------------------------------------------------------
def op_capabilities(session: Session, args: dict) -> dict:
    """**握手**：协议版本 + 全部 op 名 + 本排班是否已载入。"""
    if "expected_protocol" in args and args["expected_protocol"] != PROTOCOL_VERSION:
        raise ProtocolMismatch(args["expected_protocol"])
    return {
        "protocol": PROTOCOL_VERSION,
        "operations": sorted(OPERATIONS),
        "stateful": True,          # op 会改变会话状态（不是纯函数），调用方需保证顺序
        "loaded": session.schedule is not None,
        "docs": "documents/11-程序接口.md",
    }


def op_describe(session: Session, args: dict) -> dict:
    """当前会话的完整描述：班次 / 干员 / 设置 / 自检 / 导入报告。"""
    return session.describe()


def op_list_shifts(session: Session, args: dict) -> dict:
    """只看班次表（图形界面底部那一排班次按钮的数据）。"""
    _require_session(session)
    sch = session.schedule
    return {
        "cycle_hours": _num(sch.cycle_hours),
        "start_clock": _num(sch.start_clock),
        "shifts": [{
            "index": i + 1,
            "label": s.label,
            "hours": _num(s.hours),
            "start_in_cycle": _num(sch.starts[i]),
            "start_clock": _num((sch.starts[i] + sch.start_clock) % Decimal(24)),
            "operators": list(s.operators),
        } for i, s in enumerate(sch.shifts)],
    }


def op_validate(session: Session, args: dict) -> dict:
    """布局自检（房间数上限 / 建造位 9 / 人数 ≤ 等级容量）。"""
    return session.validate().to_dict()


def op_get_settings(session: Session, args: dict) -> dict:
    """读出全部可调项（方便调用方"改一项、留其余"）。"""
    return session.settings_dict()


def op_operator_names(session: Session, args: dict) -> dict:
    """全部可选干员名（选人下拉用）。"""
    extra = args.get("extra") or []
    return {"names": session.all_operator_names(extra)}


def op_operator_detail(session: Session, args: dict) -> dict:
    """某人的详情：所在房间、练度、解锁的心情技能数、此刻速率/心情。

    「不在基建」的人（既不在工作设施、也不在宿舍）也查得到：`in_base=false`、
    `facility=null`、速率 0、心情恒等于起点值（见 `set_detached`）。
    """
    _require_session(session)
    name = args.get("name")
    _require(name=name)
    idx = int(args.get("shift_index", 1)) - 1
    idx = min(max(idx, 0), len(session.shifts()) - 1)
    t = parse_time(args.get("at", 0), session.schedule.cycle_hours)
    op = session.operator_obj(name, idx)
    if op is None:
        if name not in session.operator_names():
            raise ValueError(f"排班里没有干员「{name}」"
                             f"（也不在「不在基建」名单里；要加请用 set_detached）")
        return {
            "name": name, "in_base": False, "facility": None,
            "detached": True, "elite": None, "level": None,
            "skills_unlocked": 0, "skills_locked": [], "training_text": "",
            "mood": _num(session.mood_at(name, t)), "rate": 0,
            "red_face_spans": [],
            "note": "既不在工作设施、也不在宿舍：心情整段不变，不参与任何技能计数",
        }
    fac = session.shifts()[idx].world.facility_of(name)
    from mood_soc.rules import mood_skill_summary
    unlocked, locked = mood_skill_summary(op)
    return {
        "name": name,
        "in_base": fac is not None,
        "detached": False,
        "facility": fac.display_name if fac else None,
        "elite": op.elite,
        "level": op.level,
        "skills_unlocked": unlocked,
        "skills_locked": [{"name": n, "need_elite": e, "need_level": lv}
                          for n, e, lv in locked],
        "training_text": session.elite_text(name),
        "mood": _num(session.mood_at(name, t)),
        "rate": _num(session.rate_at(name, t)),
        "red_face_spans": _red_face(session, name),
    }


def op_import_report(session: Session, args: dict) -> dict:
    """最后一次导入的报告（识别成什么、读到什么、推断/忽略了什么）。"""
    reports = list(getattr(session.loaded, "reports", []) or [])
    return {"reports": [r.to_dict() for r in reports],
            "summary": getattr(session.loaded, "summary", lambda: "")() if reports else ""}


# ---------------------------------------------------------------------------
# 载入 op
# ---------------------------------------------------------------------------
def op_load_schedule(session: Session, args: dict) -> dict:
    """**内联布局 → 单班排班**（最常用：调用方直接把 JSON 布局塞进来）。

    `args`：
      - `facilities`（必需）：房间数组，与本工具场景 JSON 完全同构；
      - `hours`：这一班的时长（默认 24）；
      - `label`：班次名（默认「班次 1」）；
      - 顶层还可带 `entry_events` / `idle_to_dorm` / `initial_global` / `detached`
        （与场景 JSON 同义；见 `store/layout.build_base_layout`）。
    """
    facilities = args.get("facilities")
    _require(facilities=facilities)
    if not isinstance(facilities, list):
        raise ValueError("facilities 应当是数组")
    data = {"facilities": facilities}
    for key in ("entry_events", "idle_to_dorm", "initial_global", "detached"):
        if key in args:
            data[key] = args[key]
    session.load_layout(data, hours=args.get("hours"), label=args.get("label") or "班次 1")
    return {"loaded": True, "schedule": session.describe()["shifts"],
            "operators": session.operator_names(),
            "validation": session.validate().to_dict()}


def op_load_file(session: Session, args: dict) -> dict:
    """**按文件载入**（自动识别 4 种格式：本工具场景 / MAA / v3 输出 / v4 蓝图）。"""
    path = args.get("path")
    _require(path=path)
    ld = session.load_paths([path])
    return {"loaded": True, "import": ld.summary(),
            "shifts": session.describe()["shifts"],
            "pool": list(ld.pool),
            "validation": session.validate().to_dict()}


def op_load_files(session: Session, args: dict) -> dict:
    """按**多文件**载入（12h / 6h / 6h 三个文件那种）。"""
    paths = args.get("paths")
    _require(paths=paths)
    if not isinstance(paths, list) or not paths:
        raise ValueError("paths 应当是非空数组")
    ld = session.load_paths(list(paths))
    return {"loaded": True, "import": ld.summary(),
            "shifts": session.describe()["shifts"],
            "pool": list(ld.pool),
            "validation": session.validate().to_dict()}


def op_load_json(session: Session, args: dict) -> dict:
    """**内联 JSON → 排班**（不落临时文件；把内存里的求解结果直接喂进来）。

    参数：
      - `data`（必需）：任意一种受支持的 JSON —— 本工具场景 / MAA 排班 /
        **v3 求解输出**（`{"result": {...}}` 信封）/ v4 蓝图+干员池，自动识别；
      - `hours`：各班时长（数组；只一个班时可写标量）。给了就按顺序覆盖文件里的时长；
      - `cycle_hours`：只做一致性校验（与各班时长之和不相等就报错）；
      - `cycles`：连跑几个周期（默认 1）；
      - `apply_file_settings`：是否沿用**文件里的开关**（**默认 `false`**：不继承 v3 的
        `Fiammetta.enable`，「换心情」保持关闭）；
      - `entry_events`：显式开关「换心情」（缺省＝沿用上面那条规则）；
      - `idle_to_dorm`：显式开关「闲置入宿」（**本入口缺省＝开**；`None` 时按本入口口径取开，
        见 `store.session.load_data` 的说明表）；给了 `apply_file_settings: true` 才按文件里那个
        `idle_to_dorm` 走。

    与 `load_file` 走**同一条装配路径**，同一份 JSON 走文件或走内存必须给出同一批数值。
    """
    data = args.get("data")
    _require(data=data)
    if not isinstance(data, dict):
        raise ValueError("data 应当是一个 JSON 对象（不是数组 / 字符串）")
    hours = args.get("hours")
    if hours is not None and not isinstance(hours, (list, tuple)):
        hours = [hours]                 # 单班时允许写标量（与 load_schedule 的手感一致）
    ld = session.load_data(data, hours=hours,
                           cycle_hours=args.get("cycle_hours"),
                           cycles=args.get("cycles"),
                           apply_file_settings=bool(args.get("apply_file_settings", False)),
                           entry_events=args.get("entry_events"),
                           idle_to_dorm=args.get("idle_to_dorm"))
    return {"loaded": True, "import": ld.summary(),
            "shifts": session.describe()["shifts"],
            "pool": list(ld.pool),
            "settings": {"cycles": session.cycles,
                         "entry_events": session.entry_events,
                         "idle_to_dorm": session.idle_to_dorm},
            "validation": session.validate().to_dict()}


# ---------------------------------------------------------------------------
# 编辑 op（都改 Session 状态；多数会顺带重算）
# ---------------------------------------------------------------------------
def op_set_timeline(session: Session, args: dict) -> dict:
    """「时间轴」：改各班长 / 周期校验 / 周期数 / 周期起点钟点。

    - `hours`：各班时长数组（长度 == 班次数），改完周期自动 = 各班长之和；
    - `cycle_hours`：只做一致性校验（不等就报错，不改任何东西）；
    - `cycles`：连跑几个周期（1~`store.session.MAX_CYCLES`＝7；越界夹到边界、不报错）；
    - `start_clock`：周期起点是几点（**纯显示口径**，引擎数值不变）。
    """
    _require_session(session)
    if args.get("hours"):
        session.set_timeline(hours=args["hours"])
    if args.get("cycle_hours") is not None:
        session.set_timeline(cycle_hours=args["cycle_hours"])
    if args.get("start_clock") is not None:
        session.set_start_clock(args["start_clock"])
    if args.get("cycles") is not None:
        session.set_cycles(int(args["cycles"]))
        session.recompute()
    return session.describe()


def op_set_slots(session: Session, args: dict) -> dict:
    """改某班次某间房的**进驻干员**（`operators: []` = 清空这间房）。

    `shift_index` / `facility_index` 都是 **1 基**（与界面上看到的顺序一致）。

    `manual`（默认 `true`）：这次改动里**摆了人的那些位次**要不要记进「手动台账」＝**上锁**
    （自动入宿从此不占这些位次、不换这些人）；⚠️ **被清空的位次不进台账** ⇒
    **交还自动入宿**（2026-10 口径＝**摆位即上锁、清空即解锁**，旧口径"清空即上锁 /
    保持空着"已作废）。
    传 `manual: false` ＝"**我只改布局、不上锁**"：这些位次仍然交给自动入宿调度。
    ⚠️ 默认 `true` 是为了**不改变既有调用方的行为**；要单独"锁一个**空位**"（＝"预留空位"，
    界面上做不到）就用 `set_seat_lock`，要整份交还就用 `clear_seat_locks`。
    """
    _require_session(session)
    _require(shift_index=args.get("shift_index"), facility_index=args.get("facility_index"))
    ops = args.get("operators")
    if ops is None:
        raise ValueError("缺少必填参数：operators（数组，可为空数组表示清空）")
    session.set_slots(int(args["shift_index"]) - 1, int(args["facility_index"]) - 1, ops,
                      manual=bool(args.get("manual", True)))
    return session.describe()


def op_set_seat_lock(session: Session, args: dict) -> dict:
    """给**某班某房的某一位**单独上锁 / 解锁（**不动**这一位坐的是谁）。

    参数 `shift_index` / `facility_index` / `slot` 都是 **1 基**（`slot` = 第几位）；
    `locked` 默认 `true`。

    · `locked=true`：该位次进手动台账 ⇒ 自动入宿既不占它、也不换里面的人；
      **该位是空的，效果就是"保持空着"**（＝"预留空位"）。
      ⚠️ **这是 API 专属的能力**：界面上没有锁控件（摆位即上锁、清空即解锁），
      所以"把一个空位单独锁住"只有走这条 op 才做得到。
    · `locked=false`：把该位次摘掉，**并且**把这一位的人从"手动放的人"里一起去掉 ——
      否则裁决点第 1 层会继续按名字护着她，用户会以为"解锁没生效"。
    """
    _require_session(session)
    _require(shift_index=args.get("shift_index"), facility_index=args.get("facility_index"),
             slot=args.get("slot"))
    session.set_seat_lock(int(args["shift_index"]) - 1, int(args["facility_index"]) - 1,
                          int(args["slot"]) - 1, bool(args.get("locked", True)))
    return session.describe()


def op_clear_seat_locks(session: Session, args: dict) -> dict:
    """**全部解锁**（逃生门）：整份清掉手动台账，返回动过几个班次。

    `shift_index` 缺省＝**这份排班的所有班次**（1 基）；传了就只清那一班。
    它连 `names`（"这个人是我手动放的"）**一起清** —— 只清位次的话裁决点仍会按名字护着人。
    """
    _require_session(session)
    si = args.get("shift_index")
    n = session.clear_seat_locks(None if si is None else int(si) - 1)
    return {"cleared_shifts": n}


def op_set_room_level(session: Session, args: dict) -> dict:
    """改某班次某间房的**等级**（容量随之变化）。索引 1 基。"""
    _require_session(session)
    _require(shift_index=args.get("shift_index"), facility_index=args.get("facility_index"),
             level=args.get("level"))
    session.set_room_level(int(args["shift_index"]) - 1, int(args["facility_index"]) - 1,
                           int(args["level"]))
    return {"ok": session.validate().ok, "issues": session.validate().messages(),
            "validation": session.validate().to_dict()}


def op_set_initial_moods(session: Session, args: dict) -> dict:
    """整份替换**周期起点心情**（`moods: {}` = 全部回到导入值）。

    也接受 `{"全部": 24}` 这种特殊写法（等价于"所有人都设为 24"）。
    """
    _require_session(session)
    moods = args.get("moods")
    _require(moods=moods)
    if not isinstance(moods, dict):
        raise ValueError("moods 应当是对象：{干员名: 心情}")
    if moods.get("全部") is not None:
        value = moods["全部"]
        moods = {n: value for n in session.operator_names()}
    session.set_initial_moods(moods)
    session.recompute()
    return {"initial_moods": {n: _num(v) for n, v in session.initial_moods.items()}}


def op_set_moods(session: Session, args: dict) -> dict:
    """**批量设心情**：`{"泡泡": 12, "火神": 24}`，等价于逐个 `set_mood_at(at=0)`。"""
    return op_set_initial_moods(session, args)


def op_set_mood_at(session: Session, args: dict) -> dict:
    """在**指定时刻**把某人心情置为给定值（心情指定事件 / 锚点）。

    第 1 周期 0:00 会被自动路由成"起点心情"；其余时刻是**同刻跳变**，之后按正常速率演化。
    """
    _require_session(session)
    _require(name=args.get("name"), mood=args.get("mood"))
    t = parse_time(args.get("at", 0), session.schedule.cycle_hours)
    ev = session.set_mood_at(args["name"], args["mood"], t)
    session.recompute()
    out = {"applied": True, "at": _num(t), "operator": args["name"], "mood": _num(to_decimal(args["mood"]))}
    out["is_initial"] = ev is None
    return out


def op_clear_mood_events(session: Session, args: dict) -> dict:
    """清空全部「心情指定事件」锚点（不动起点心情）。"""
    session.clear_mood_events()
    session.recompute()
    return {"mood_events": 0}


def op_restore_imported_moods(session: Session, args: dict) -> dict:
    """「恢复导入值」：清手动起点心情 + 清全部锚点。"""
    _require_session(session)
    session.restore_imported_moods()
    session.recompute()
    return {"initial_moods": 0, "mood_events": 0}


def op_set_training(session: Session, args: dict) -> dict:
    """改练度（精英化 / 等级）：`names` 缺省 = 全基建所有人；返回改动的**位置数**。"""
    _require_session(session)
    elite = args.get("elite")
    level = args.get("level")
    if elite is None and level is None:
        raise ValueError("至少要给 elite 或 level 之一")
    if elite is not None and int(elite) not in (0, 1, 2):
        raise ValueError("elite 只能是 0 / 1 / 2")
    changed = session.set_training(elite=elite, level=level, names=args.get("names"))
    return {"changed": changed, "badges": session.elite_badges()}


def op_fill_from_pool(session: Session, args: dict) -> dict:
    """「从池中依次填入」：把干员池按顺序铺满空位（只有 v4 蓝图那类文件才有池）。"""
    _require_session(session)
    idx = args.get("shift_index")
    filled = session.fill_from_pool(None if idx is None else int(idx) - 1)
    return {"filled": filled, "note": "" if filled else "没有干员池（该文件不带 operbox）"}


def op_set_entry_events(session: Session, args: dict) -> dict:
    """「换心情」（进驻事件 M15a）：开关 / 换谁 / 范围 / 什么时候换 / 按班次覆盖。

    - `enabled`：总开关；
    - `swap_with`：人名、`"any"`（全基建最累的）、`null`（同宿舍前一位进驻）；
    - `scope`：`"dorm"`（默认）或 `"anywhere"`；
    - `when`：`"immediate"` / `"wait"` / `"full"` —— **三档都要求"她满 24"**（2026-10 用户裁决），
      差别只在"她没满时等不等"（`wait` 等回满、另两档这一次不换）；
    - `restore_back`：默认 `true`（只换心情、两人留原位）；
    - `per_shift`：按班次覆盖（见 `mood_soc.models.build_entry_shift_overrides` 的写法）。
    """
    _require_session(session)
    if "enabled" in args:
        session.entry_events = bool(args["enabled"])
    if "swap_with" in args:
        session.entry_swap_with = args["swap_with"] or None
    if args.get("scope") is not None:
        scope = str(args["scope"]).lower()
        if scope not in ("dorm", "anywhere"):
            raise ValueError('scope 只能是 "dorm" 或 "anywhere"')
        session.entry_scope = scope
    if args.get("when") is not None:
        when = normalize_entry_when(args["when"])
        if when is None:
            raise ValueError('when 只能是 "immediate" / "wait" / "full"')
        session.entry_when = when
    if args.get("restore_back") is not None:
        session.entry_restore_back = bool(args["restore_back"])
    if args.get("per_shift") is not None:
        from mood_soc.models import build_entry_shift_overrides
        session.entry_per_shift = build_entry_shift_overrides(args["per_shift"])
    session.recompute()
    holders, mates = session.entry_candidates()
    return {"settings": session.settings_dict()["entry_events"],
            "holders": holders, "targets": mates}


def op_set_idle_to_dorm(session: Session, args: dict) -> dict:
    """「闲置入宿」：**每个换班执行点**把"这一班完全没出现在任何设施、心情未满"的干员安排进宿舍。

    口径（三层解耦：**手动编辑 > 自动入宿 > 导入布局**，见 `mood_soc.rules.apply_idle_to_dorm`）：
    竖向正序填空床；全满后取**心情最低**的候选，换出"锁定区外、心情 ≥ 她、心情最大"的住户。
    ⚠️ **不设班次数量门槛**（1 个班次也执行）。

    - `enabled`：总开关；
    - `protected_slots`：**锁定位置数**（默认 5）：按竖向正序（位次优先、宿舍序号其次）
      锁前 N 个位置，自动入宿不换锁定区里的人（锁定区的空位照样能入住）；
    - `blacklist`：**黑名单**：永远不能通过闲置入宿进宿舍的人（可被换出）；
    - `per_operator`：逐人设置，**只有一种含义** —— `{"跃跃": false}`（这一位不参与）/
      `[{"name": "跃跃", "enabled": false, "cycle": 2, "shift": 3}]`（限定某几次）。
      ⚠️ **旧写法已作废**：`target` / `dorm` / `slot` / `swap_with`（手动指定位置、手动点名）
      读得进来但**被忽略**，另回一条 `notes`。要在某一刻把某人放进某个宿舍位次，请走**布局编辑**：
      `load_json` 的 `facilities[].slots` 写死该位次，或 `set_slots`（见那两个 op）。
    """
    _require_session(session)
    if "enabled" in args:
        session.idle_to_dorm = bool(args["enabled"])
    if args.get("protected_slots") is not None:
        session.idle_protected_slots = max(0, int(args["protected_slots"]))
    if args.get("blacklist") is not None:
        session.idle_blacklist = [str(n) for n in (args["blacklist"] or [])]
    per = args.get("per_operator")
    notes: List[str] = []
    if per is not None:
        globals_: Dict[str, bool] = {}
        entries: Dict[Tuple[int, int, str], bool] = {}
        legacy = ("target", "swap_with", "swapWith", "dorm", "dormIndex", "slot", "slotIndex")
        items: Sequence = list(per.items()) if isinstance(per, dict) else list(per)
        for item in items:
            if isinstance(item, tuple):                     # 字典写法 {名字: 值}
                name, value = item[0], item[1]
                if isinstance(value, bool):
                    use, cyc, shf = value, None, None
                elif isinstance(value, str) or value is None:
                    notes.append(f"per_operator[{name}] 的「手动点名/指定位置」写法已作废"
                                 f"（{value!r}）—— 手动入宿请改用布局编辑（facilities[].slots）")
                    use, cyc, shf = True, None, None
                elif isinstance(value, dict):
                    use = bool(value.get("enabled", True))
                    cyc = value.get("cycle")
                    shf = value.get("shift")
                    if any(value.get(k) is not None for k in legacy):
                        notes.append(f"per_operator[{name}] 的 target/swap_with/dorm/slot 已作废"
                                     f"（手动入宿请改用布局编辑）")
                else:
                    raise ValueError(f"per_operator[{name!r}] 格式无法识别：{value!r}")
            else:                                           # 数组写法 [{name, ...}]
                if not isinstance(item, dict) or not item.get("name"):
                    raise ValueError(f"per_operator 数组里每项都要有 name：{item!r}")
                name = item["name"]
                use = bool(item.get("enabled", True))
                cyc, shf = item.get("cycle"), item.get("shift")
                if any(item.get(k) is not None for k in legacy):
                    notes.append(f"per_operator[{name}] 的 target/swap_with/dorm/slot 已作废"
                                 f"（手动入宿请改用布局编辑）")
            # ⚠️ 只记**改过默认**的（`use is False`）：`idle_globals` 的语义就是"这些人不参与"，
            #    记一堆 `True` 会污染增量指纹与导出（`Session.idle_entry_list` 也只收 False）。
            if not use:
                if cyc is None and shf is None:
                    globals_[str(name)] = False
                else:
                    entries[(int(cyc or 1), int(shf or 1), str(name))] = False
        session.idle_globals = globals_
        session.idle_entries = entries
    session.recompute()
    out = {"enabled": session.idle_to_dorm,
           "protected_slots": int(session.idle_protected_slots),
           "blacklist": list(session.idle_blacklist),
           "effective": _idle_effective(session),
           "count": session.idle_count() if session.idle_to_dorm else 0,
           "groups": [_idle_group_dict(g) for g in session.idle_groups()]}
    if notes:
        out["notes"] = notes
    return out


def _idle_effective(session: Session) -> bool:
    """闲置入宿此刻**真的会不会生效**。

    ⚠️ 文档《闲置入宿完整逻辑》第二版**取消了"至少 3 个班次"的门槛**（1 个班次也执行），
    所以现在它就等于总开关；保留这个字段只是让调用方少判一次 `enabled`。
    """
    return bool(session.idle_to_dorm and session.schedule is not None)


def _idle_group_dict(group: tuple) -> dict:
    """一组逐次表 → API 的 JSON（含**换班执行点**的标题/起止时刻，供调用方渲染）。

    `target` / `options` 恒为 `null` / `[]`：三层解耦后手动入宿归**布局编辑**
    （看板 / 「干员与心情」写班次快照 + 手动台账），这里不再提供二次选择。
    """
    title, scope, rows, t0, t1 = group
    return {"title": title, "scope": list(scope),
            "start": _num(t0), "end": _num(t1),
            "rows": [{"name": n, "mood": _num(m), "where": w,
                      "use": u, "target": None, "options": []}
                     for n, m, w, u, _tg, _opts in rows]}


def op_idle_to_dorm_groups(session: Session, args: dict) -> dict:
    """只读：当前设置下的逐次入宿表（候选 + 「这一位参不参与」），供调用方做交互。

    ⚠️ **一个换班执行点一组**（班初 + 长班的每个内部换班点）；同班各执行点**共用一份**
    逐人设置（组里的 `scope` 相同 = `[周期, 班次]`），所以改一组会影响同班其他组。
    ⚠️ 每行的 `target` / `options` 恒为 `null` / `[]`（三层解耦后这里不再有手动位置/点名；
    手动入宿＝**布局编辑**，见 `set_idle_to_dorm` 的说明），字段保留只为兼容旧调用方。
    """
    _require_session(session)
    groups = session.idle_groups(cycles=args.get("cycles"))
    return {"enabled": session.idle_to_dorm,
            "protected_slots": int(session.idle_protected_slots),
            "blacklist": list(session.idle_blacklist),
            "effective": _idle_effective(session),
            "groups": [_idle_group_dict(g) for g in groups]}


def op_entry_candidates(session: Session, args: dict) -> dict:
    """只读：谁可能触发进驻事件、可以和谁换（界面下拉的数据）。"""
    _require_session(session)
    holders, targets = session.entry_candidates()
    return {"holders": holders, "targets": targets}


# ---------------------------------------------------------------------------
# 心情与轨迹（**核心输出**）
# ---------------------------------------------------------------------------
def op_moods(session: Session, args: dict) -> dict:
    """**输出所有干员的心情**（默认给整周期 + 每个周期末的落点）。

    参数：
      - `at`：时刻，可单个也可数组；支持小时数（`8.5`，**绝对**）与钟点（`"08:30"`，周期内）。
        缺省 = 每个周期末（第 1 周期末、第 2 周期末…）；
      - `cycles`：临时用几个周期跑（不改会话的 `cycles`）；
      - `include_trajectory`：是否附带整条折线（节点即事件时刻）；
      - `include_red_face`：是否附带红脸区间（默认 `true`）。

    返回：
      ```jsonc
      {"moods": {"0": {"泡泡": 24, ...}, "24": {...}},   // 键是绝对小时
       "operators": ["泡泡", ...],
       "trajectory": {"times": [...], "operators": {"泡泡": [...]}},   // 可选
       "red_face": {"泡泡": [[21.0, 24.0]]},                          // 可选
       "layout_sustain_hours": 32, "bottleneck": "路人1"}
      ```
    """
    _require_session(session)
    cycles = args.get("cycles")
    if cycles is not None and int(cycles) != session.cycles:
        session.set_cycles(int(cycles))
        session.recompute()
    cycle_hours = session.schedule.cycle_hours
    default = [cycle_hours * Decimal(k) for k in range(1, session.cycles + 1)]
    times = _times(args.get("at"), session, default=default)
    traj = session.traj
    out: dict = {
        "operators": list(traj.names),
        "detached": session.bench_names(),     # 其中这些人不在基建（心情一条平线）
        "cycle_hours": _num(cycle_hours),
        "cycles": session.cycles,
        "total_hours": _num(session.total_hours),
        "moods": {str(_num(t)): _num_map(session.moods_at(t)) for t in times},
    }
    if args.get("include_trajectory"):
        out["trajectory"] = {
            "times": [_num(t) for t in traj.times],
            "operators": {n: [_num(v) for v in traj.moods[n]] for n in traj.names},
        }
    if args.get("include_red_face", True):
        out["red_face"] = {n: _red_face(session, n) for n in traj.names
                           if session.red_face_spans(n)}
    # 布局可持续性（base 模式口径）：把"整周期内不会有人红脸"换算成一个时长
    sustain = _layout_sustain(session)
    out.update(sustain)
    return out


def _layout_sustain(session: Session) -> dict:
    """整周期口径的"还能撑多久"：第一个进入红脸的时刻，以及是谁。"""
    worst_t: Optional[Decimal] = None
    bottleneck: Optional[str] = None
    for n in session.traj.names:
        spans = session.red_face_spans(n)
        if not spans:
            continue
        start = spans[0][0]
        if worst_t is None or start < worst_t:
            worst_t, bottleneck = start, n
    if worst_t is None:
        return {"layout_sustain_hours": None, "bottleneck": None,
                "note": "整段轨迹内没有人红脸"}
    return {"layout_sustain_hours": _num(worst_t), "bottleneck": bottleneck}


def op_trajectory(session: Session, args: dict) -> dict:
    """只要整条折线（`times` + 每人一条数列），适合画图或做数值比对。"""
    _require_session(session)
    traj = session.traj
    return {"times": [_num(t) for t in traj.times],
            "operators": {n: [_num(v) for v in traj.moods[n]] for n in traj.names}}


def _person_out(row: dict) -> dict:
    """`Session.layout_at` 里的一行（干员）→ JSON（数值 6 位小数）。"""
    out = {"name": row["name"], "mood": _num(row["mood"])}
    if "slot" in row:
        out["slot"] = row["slot"]
    if "rate" in row:
        out["rate"] = _num(row["rate"])
    if "elite" in row:
        out["elite"] = row["elite"]
        out["level"] = row["level"]
    return out


def op_layout_at(session: Session, args: dict) -> dict:
    """**时间节点 → 整座基地的布局 + 全员心情**（只给一个 `at`）。

    ```jsonc
    {"at": 8, "cycle": 1, "in_cycle": 8, "shift_index": 1, "shift_label": "1. alpha/beta · 12h",
     "rooms": [{"index": 1, "type": "贸易站", "name": "贸易站#1", "level": 3, "capacity": 3,
                "enabled": true,
                "operators": [{"slot": 1, "name": "但书", "mood": 22.4, "rate": 0.85,
                               "elite": 2, "level": 30}],
                "deputies": []}],
     "detached": [{"name": "歌蕾蒂娅", "mood": 24, "rate": 0}],   // 不在基建（心情一条平线）
     "moods": {"但书": 22.4, ...}}
    ```

    ⚠️ 读的是**引擎那份布局副本**（`Trajectory.world_at`）——换心情 / 闲置入宿换过人之
    后的**真实**排布，**不是**你导入的那份快照；落在班次边界取**右侧**。
    `at` 支持小时数（绝对值，可跨周期）与钟点字符串（`"08:30"`，周期内）。
    """
    _require_session(session)
    t = parse_time(args.get("at", 0), session.schedule.cycle_hours)
    lay = session.layout_at(t)
    if lay is None:
        raise ValueError("尚未载入排班：先调 load_json（或 load_schedule / load_file）")
    return {
        "at": _num(lay["at"]),
        "cycle": lay["cycle"],
        "in_cycle": _num(lay["in_cycle"]),
        "shift_index": lay["shift_index"],
        "shift_label": lay["shift_label"],
        "rooms": [{"index": r["index"], "type": r["type"], "name": r["name"],
                   "level": r["level"], "capacity": r["capacity"], "enabled": r["enabled"],
                   "operators": [_person_out(o) for o in r["operators"]],
                   "deputies": [_person_out(o) for o in r["deputies"]]}
                  for r in lay["rooms"]],
        "detached": [_person_out(d) for d in lay["detached"]],
        "moods": _num_map(lay["moods"]),
    }


def op_closure(session: Session, args: dict) -> dict:
    """**闭环体检**：同一排班连跑 `cycles` 个周期，看心情是否收敛（＝能不能长期跑）。

    - `cycles`：跑几个周期（默认 **3**；上限 `store.session.MAX_CYCLES`＝7，越界夹到 7；
      心情跨周期连续，所以第 k 个周期末＝下一轮开局）；
    - 判据：第 k 与 k−1 个周期末**逐人相同**（容差 1e-9）⇒ 第 k 轮起进入固定点；
    - 另附「周期末 vs 周期初」的逐人差值、最早红脸时刻与瓶颈。

    ⚠️ **会改会话的 `cycles` 并重算**（与 `moods` 的 `cycles` 参数同一口径）。
    """
    _require_session(session)
    cycles = int(args.get("cycles", 3))
    if cycles < 1:
        raise ValueError("cycles 至少为 1")
    out = session.closure(cycles)
    out["cycle_hours"] = _num(out["cycle_hours"])
    out["cycle_start_moods"] = {k: _num_map(v) for k, v in out["cycle_start_moods"].items()}
    out["cycle_end_moods"] = {k: _num_map(v) for k, v in out["cycle_end_moods"].items()}
    out["delta_last_vs_first"] = _num_map(out["delta_last_vs_first"])
    out["layout_sustain_hours"] = _num(out["layout_sustain_hours"])
    out["red_face"] = {n: [[_num(a), _num(b)] for a, b in spans]
                       for n, spans in out["red_face"].items()}
    return out


def op_set_detached(session: Session, args: dict) -> dict:
    """**「不在基建」名单**（既不在工作设施、也不在宿舍的干员）。

    - `names`（必需）：整份替换的名单数组（`[]` = 清空）；支持 `{"name": "某人"}` 写法；
    - `add` / `remove`：增量写法（可选，与 `names` 二选一）——
      `add` 默认**连位置一起摘**（`remove_from_slots` 可关）；
    - `moods`：顺便把这些人的心情设成**周期起点**值：
      `{"names": ["某人"], "moods": {"某人": 12}}`。

    语义：这些人在整条轨迹里**心情恒定**（净速率 0），且**不参与任何技能计数**
    （他们不在基建的房间列表里）。他们照样出现在 `moods` / `trajectory` 里（一条平线）。
    """
    _require_session(session)
    if args.get("names") is not None:
        names = args["names"]
        if not isinstance(names, (list, tuple)):
            raise ValueError("names 应当是数组（可为空数组表示清空）")
        session.set_detached([n.get("name") if isinstance(n, dict) else n for n in names])
    if args.get("add"):
        for n in (args["add"] if isinstance(args["add"], (list, tuple)) else [args["add"]]):
            session.add_detached(n.get("name") if isinstance(n, dict) else n,
                                 remove_from_slots=args.get("remove_from_slots", True))
    if args.get("remove"):
        for n in (args["remove"] if isinstance(args["remove"], (list, tuple)) else [args["remove"]]):
            session.remove_detached(n.get("name") if isinstance(n, dict) else n)
    for name, value in (args.get("moods") or {}).items():
        session.set_initial_mood(name, value)
    session.recompute()
    return {"detached": session.bench_names(),
            "detached_explicit": list(session.detached),
            "not_in_shift": session.not_in_shift(0),
            "initial_moods": {n: session.initial_moods[n] for n in session.bench_names()
                              if n in session.initial_moods}}


def op_bench_names(session: Session, args: dict) -> dict:
    """只读：**「不在基建」的人**（名单点名的 ∪ 整个排班都没排到位置的人）。

    `shift_index` 给了就顺带返回"该班次没排到位置的人"（界面那一段的自动名单）。
    """
    _require_session(session)
    out = {"detached": session.bench_names(),
           "explicit": list(session.detached),
           "stationed": session.schedule.stationed_names()}
    if args.get("shift_index") is not None:
        idx = int(args["shift_index"]) - 1
        out["shift_index"] = idx + 1
        out["not_in_shift"] = session.not_in_shift(idx)
    return out


def op_mood_ledger(session: Session, args: dict) -> dict:
    """**流水账**：某人此刻的净速率是怎么来的（逐条来源 + 叠加规则），可解释性入口。"""
    _require_session(session)
    name = args.get("name")
    _require(name=name)
    idx = int(args.get("shift_index", 1)) - 1
    worlds = session.shifts()
    idx = min(max(idx, 0), len(worlds) - 1)
    if worlds[idx].world.get_operator(name) is None:
        raise ValueError(f"第 {idx + 1} 班没有干员「{name}」")
    from mood_soc.rules import mood_ledger
    lg = mood_ledger(worlds[idx].world, name)
    out = lg.to_dict()
    out["facility"] = worlds[idx].world.facility_of(name).display_name
    out["explain"] = lg.explain()
    return out


def op_time_to_mood(session: Session, args: dict) -> dict:
    """某人从当前心情变成目标心情需要多久（解析解；工作=下降、宿舍=上升）。"""
    _require_session(session)
    _require(name=args.get("name"), mood=args.get("mood"))
    idx = int(args.get("shift_index", 1)) - 1
    worlds = session.shifts()
    idx = min(max(idx, 0), len(worlds) - 1)
    from mood_soc.rules import time_to_mood
    hours = time_to_mood(worlds[idx].world, args["name"], args["mood"])
    return {"name": args["name"], "target_mood": _num(to_decimal(args["mood"])),
            "hours": _num(hours)}


def op_bottleneck(session: Session, args: dict) -> dict:
    """谁最先红脸、什么时候、各人的最低点（排班体检用）。"""
    _require_session(session)
    lows = {}
    for n in session.traj.names:
        vals = session.traj.moods[n]
        low = min(vals)
        t = session.traj.times[vals.index(low)]
        lows[n] = {"min": _num(low), "at": _num(t),
                   "red_face_spans": _red_face(session, n)}
    return {"layout_sustain_hours": _layout_sustain(session)["layout_sustain_hours"],
            "bottleneck": _layout_sustain(session)["bottleneck"],
            "operators": lows}


def op_export_schedule(session: Session, args: dict) -> dict:
    """把当前排班导回**本工具场景 JSON**（每班一份 `{"facilities": [...]}`）。

    `detached`（不在基建名单）与 `initial_global` 写在**场景顶层**，与导入时同键同层，
    所以「导出 → 再导入」能原样还原（含这些人）。
    """
    _require_session(session)
    return {"shifts": [{"label": s.label, "hours": _num(s.hours),
                        "scenario": {"facilities": s.facilities,
                                     **({"detached": list(s.detached)}
                                        if getattr(s, "detached", None) else {}),
                                     **({"initial_global": dict(s.initial_global)}
                                        if getattr(s, "initial_global", None) else {})}}
                       for s in session.shifts()],
            "detached": session.bench_names(),
            "start_clock": _num(session.schedule.start_clock),
            "cycles": session.cycles}


# ---------------------------------------------------------------------------
# op 表
# ---------------------------------------------------------------------------
OPERATIONS = {
    # 只读
    "capabilities": op_capabilities,
    "describe": op_describe,
    "list_shifts": op_list_shifts,
    "validate": op_validate,
    "get_settings": op_get_settings,
    "operator_names": op_operator_names,
    "operator_detail": op_operator_detail,
    "import_report": op_import_report,
    "idle_to_dorm_groups": op_idle_to_dorm_groups,
    "entry_candidates": op_entry_candidates,
    # 载入
    "load_schedule": op_load_schedule,
    "load_file": op_load_file,
    "load_files": op_load_files,
    "load_json": op_load_json,
    # 编辑
    "set_timeline": op_set_timeline,
    "set_slots": op_set_slots,
    "set_room_level": op_set_room_level,
    # 2026-10：显式的"上锁/解锁"（手动入宿与自动入宿解耦的唯一控制面）
    "set_seat_lock": op_set_seat_lock,
    "clear_seat_locks": op_clear_seat_locks,
    "set_moods": op_set_moods,
    "set_initial_moods": op_set_initial_moods,
    "set_mood_at": op_set_mood_at,
    "clear_mood_events": op_clear_mood_events,
    "restore_imported_moods": op_restore_imported_moods,
    "set_training": op_set_training,
    "fill_from_pool": op_fill_from_pool,
    "set_entry_events": op_set_entry_events,
    "set_idle_to_dorm": op_set_idle_to_dorm,
    "set_detached": op_set_detached,
    "bench_names": op_bench_names,
    # 结果
    "moods": op_moods,
    "trajectory": op_trajectory,
    "layout_at": op_layout_at,
    "closure": op_closure,
    "mood_ledger": op_mood_ledger,
    "time_to_mood": op_time_to_mood,
    "bottleneck": op_bottleneck,
    "export_schedule": op_export_schedule,
}


def handle(session: Session, op: str, args: Optional[dict] = None) -> object:
    """执行一个 op（供 server / cli 共用）。未知 op 报 `ValueError`。"""
    if op not in OPERATIONS:
        raise ValueError(f"未知操作 {op!r}；可用：{'、'.join(sorted(OPERATIONS))}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("args 应当是对象（{}）")
    return OPERATIONS[op](session, args)


__all__ = ["OPERATIONS", "handle", "parse_time"]

"""scripts/verify_settings.py —— **设置面自检**（时间轴 / 干员与心情 / 锁定入宿 / 换心情 / 闲置入宿）。

用法：
    .venv/Scripts/python.exe scripts/verify_settings.py          # 全绿 → 退出码 0，有红 → 1
    .venv/Scripts/python.exe scripts/verify_settings.py -v       # 顺带打印每一条通过项

与 `scripts/verify_idle.py` 同骨架：分节（`=== §N 段名 ===`）、`check(条件, 说明)` 计成败、
末尾打印 `N 条 / M fail`、失败打印**实际值 vs 期望值**、退出码非 0 表示有红。

## 它测什么（按设置分区组织，`ui/settings.py::PAGES` + 全局）

    §0 设置面清单（分页 / 上限常量唯一口径 / 每个设置字段都有写入口）
    §1 时间轴与全局（周期数边界 / 起始时钟只改显示 / 各班时长 / 容量 / 氛围 / 缩容）
    §2 干员与心情（练度 / 起点心情 / 心情锚点 / 房间等级）
    §3 锁定入宿（摆位即上锁 / 清空即解锁 / 粒度累积 / 后者覆盖 / 回原位 / 缩容）
    §4 换心情（触发者三口径 / 「满 24」门 / 三种 when / scope / 找不到目标 / 逐班覆盖）
    §5 闲置入宿（开关 / 黑名单 / 已撤的 per_operator / 锁定位置数 / 两相 / 每个执行点）
    §6 导入不变量（两个导入入口逐字段自动比对 / 文件级设置真的生效）
    §7 导出 → 再导入往返（哪些保持、哪些丢失、已知例外钉住）
    §8 「改完设置再触发各种动作，设置还在不在」（静默覆盖扫描）
    §9 被移动 / 移除 / 换位之后，设置是否仍然生效（菲亚梅塔那一类）
    §10 多周期 / 多班次 / 长班内部换班点
    §11 导入设置 vs 界面改设置（同一批落地面）的一致性

## 口径与红线

- **只读**现有模块（不改任何代码）；本脚本只加文件。
- 发现**真 bug** 时**不修改代码**，而是把现状**显式钉成一条 `⚠已知缺陷` 的 check**
  （条件＝"现状确实如此"，说明里写清期望与报告条目），并在末尾汇总打印条数。
  ⇒ 本脚本 0 fail ≠ 没有缺陷，**真缺陷清单在运行报告里**（父任务报告 §2）。
- 比小数一律用容差（`close`）或比量化后的值，不做浮点/Decimal 硬碰。
"""
from __future__ import annotations

import copy
import inspect
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:                                  # Windows 控制台默认 GBK，打不出「⇒」这类字符
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                     # noqa: BLE001 —— 老解释器/被重定向时忽略
    pass

from data.paths import MAA_SAMPLE, ROOT, SAMPLES                       # noqa: E402
from data.domain import facility_slots                                 # noqa: E402
from mood_soc.battery import to_decimal                                # noqa: E402
from mood_soc.config import FacilityType, dormitory_recovery           # noqa: E402
from mood_soc.models import (ENTRY_WHEN_MODES,                        # noqa: E402
                             build_entry_event_config, build_entry_shift_overrides,
                             build_idle_to_dorm_config, normalize_entry_when,
                             resolve_entry_config)
from mood_soc.rules import (DEFAULT_PROTECTED_SLOTS, _seat_verdict,     # noqa: E402
                            apply_entry_events, apply_idle_to_dorm,
                            entry_event_holders, find_entry_target,
                            reset_entry_events)
from store.layout import build_base_layout                             # noqa: E402
from store.schedule import (LoadedSchedule, MoodSetEvent, Schedule,     # noqa: E402
                            Shift, execution_offsets, execution_points,
                            load_schedule_ex, simulate_schedule)
from store.session import (MAX_CYCLES, Session, seat_specs,             # noqa: E402
                           seat_values)
from api.ops import OPERATIONS, handle, op_export_schedule              # noqa: E402

ZERO = Decimal("0")
D = Decimal

PASS = 0
FAIL = 0
KNOWN = 0                                  # 「已知缺陷 / 已知例外」check 的条数（也计入 PASS）
VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
KNOWN_ISSUES: list = []                    # [(编号, 一句话)]


def _short(value, limit: int = 240) -> str:
    """把实际值/期望值截断到可读长度（失败信息里不许刷屏）。"""
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[:limit] + f"…（共 {len(text)} 字）"


def check(name: str, ok: bool, detail: str = "") -> bool:
    """一条断言：通过就 +1（`-v` 时打印），失败打印原因并计入失败数。"""
    global PASS, FAIL
    if ok:
        PASS += 1
        if VERBOSE:
            print(f"  ok   {name}")
        return True
    FAIL += 1
    print(f"  FAIL {name}" + (f"　—— {detail}" if detail else ""))
    return False


def eq(name: str, got, want) -> bool:
    """相等断言：失败时打印**实际值 vs 期望值**（长值截断）。"""
    return check(name, got == want, f"实际 {_short(got)} vs 期望 {_short(want)}")


def close(name: str, got, want, tol="1e-6") -> bool:
    """数值断言（容差）：`got`/`want` 走 `to_decimal`，差 > tol 就算失败。"""
    g, w, t = to_decimal(got), to_decimal(want), to_decimal(tol)
    return check(name, abs(g - w) <= t, f"实际 {g} vs 期望 {w}（容差 {t}）")


def known(name: str, ok: bool, detail: str = "", issue: str = "") -> bool:
    """**已知缺陷 / 已知例外**：条件＝"现状确实如此"；说明里写明期望与报告条目。

    这些条也计入 PASS（脚本要 0 fail），但它们**不代表行为正确** ——
    运行末尾会汇总打印，真缺陷清单在父任务报告 §2。
    """
    global KNOWN
    if ok:
        KNOWN += 1
        if issue:
            KNOWN_ISSUES.append((issue, name))
    return check(f"⚠ {name}" + (f"〔{issue}〕" if issue else ""), ok, detail)


def raises(func, *a, **kw) -> str:
    """调一次，抛异常返回异常文本；没抛返回 `""`。"""
    try:
        func(*a, **kw)
    except Exception as exc:          # noqa: BLE001 —— 故意的
        return f"{type(exc).__name__}: {exc}"
    return ""


# ============================================================================
# 通用夹具
# ============================================================================
def sess_from_data(data: dict, **kw) -> Session:
    """建一个会话（`load_data`；可传 `idle_to_dorm=False` 等）。"""
    s = Session()
    s.load_data(data, **kw)
    return s


def sess_from_file(data: dict, name: str = "scene", **attrs) -> Session:
    """写一份场景 JSON 到 `_scratch` 再**按文件载入**（测「文件级设置」那条路）。

    ⚠️ 为什么不能用 `load_data` 代替：那是给 v3 的**内联适配入口**，它有自己的默认口径
    （不继承文件里的开关），所以"文件里写了什么"只能用 `load_paths` 验。
    """
    d = make_tmpdir("files")
    p = d / f"{name}.json"
    write_json(p, data)
    s = Session()
    s.load_paths([p])
    for k, v in attrs.items():
        setattr(s, k, v)
    return s


def sess_from_shifts(shifts, cycle_hours=None, detached=()) -> Session:
    """由现成的 `Shift` 列表建会话（等价 `load_layout` 的装配那几步，用于多班合成场景）。"""
    total = cycle_hours if cycle_hours is not None else sum((s.hours for s in shifts), ZERO)
    s = Session()
    s.schedule = Schedule(list(shifts), to_decimal(total), ZERO, list(detached))
    s.loaded = LoadedSchedule(schedule=s.schedule)
    s._sync_from_schedule(from_import=True)
    s._capture_import_layouts()
    s.recompute()
    return s


def shift(label, hours, facilities, **kw) -> Shift:
    return Shift(label=label, hours=to_decimal(hours), facilities=list(facilities), **kw)


def fac_of(s: Session, shift_index=0, fac_index=0) -> dict:
    return s.facilities_of(shift_index)[fac_index]


def manual_of(fac: dict) -> dict:
    return dict(fac.get("manual") or {})


def settings_snapshot(s: Session) -> dict:
    """归一化的**设置快照**（逐字段可比；顺序无关、Decimal 转字符串）。"""
    d = s.settings_dict()
    return {
        "cycles": int(d["cycles"]),
        "start_clock": str(to_decimal(d["start_clock"])),
        "initial_moods": {str(k): str(to_decimal(v)) for k, v in sorted(d["initial_moods"].items())},
        "detached": [str(n) for n in d["detached"]],
        "mood_events": sorted((str(e["name"]), int(e["cycle"]), str(to_decimal(e["t"])),
                               str(to_decimal(e["mood"]))) for e in d["mood_events"]),
        "entry_events": {
            "enabled": bool(d["entry_events"]["enabled"]),
            "swap_with": d["entry_events"]["swap_with"],
            "scope": d["entry_events"]["scope"],
            "restore_back": bool(d["entry_events"]["restore_back"]),
            "when": d["entry_events"]["when"],
            "per_shift": [dict(p) for p in d["entry_events"]["per_shift"]],
        },
        "idle_to_dorm": {
            "enabled": bool(d["idle_to_dorm"]["enabled"]),
            "protected_slots": int(d["idle_to_dorm"]["protected_slots"]),
            "blacklist": [str(n) for n in d["idle_to_dorm"]["blacklist"]],
            # ⚠️ 2026-10：`per_operator` **已从 `settings_dict()` 撤掉**（逐人「参不参与」
            #    随功能整条撤销）—— 原来这里把它的每一条 JSON 化后排序进快照，
            #    现在字段本身不存在，快照里也不该再出现它（下面 291 那条钉子键数）。
        },
    }


def diff_settings(a: dict, b: dict) -> list:
    """两个设置快照的**差异键**（自动比对，不手抄字段名）。"""
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def facilities_json(s: Session) -> list:
    """排班快照的布局（逐班次逐位次的 **JSON 形态**，含 `manual` 台账）。"""
    return [json.dumps(s.schedule.shifts[i].facilities, sort_keys=True, ensure_ascii=False)
            for i in range(len(s.schedule.shifts))]


def layout_names(s: Session, t, shift_index=None) -> dict:
    """引擎在时刻 t 的"谁在哪"（`{房间名: [人]}`，读 `layout_at` = 模拟副本那份）。"""
    info = s.layout_at(t)
    out = {}
    for room in info["rooms"]:
        out[room["name"]] = [o["name"] for o in room["operators"]]
    return out


def entry_marks(traj_or_session) -> list:
    traj = getattr(traj_or_session, "traj", traj_or_session)
    return [m for m in (traj.marks or []) if m.kind == "entry"]


def entry_labels(traj_or_session) -> list:
    return [m.label for m in entry_marks(traj_or_session)]


def idle_labels(traj_or_session) -> list:
    traj = getattr(traj_or_session, "traj", traj_or_session)
    return [m.label for m in (traj.marks or []) if m.kind == "idle"]


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def make_tmpdir(name: str) -> Path:
    """**在仓库内**建临时目录（沙箱只允许工作区内写；`tempfile.mkdtemp` 的 0o700 会被拒）。"""
    d = ROOT / "_scratch" / f"verify_settings_{name}"
    os.makedirs(d, exist_ok=True)
    return d


#: 换心情测试用的真实干员（菲亚梅塔持 M15a「患难之交」）
FEI, DRAGON, WULIAN, MUR, PAO = "菲亚梅塔", "龙舌兰", "巫恋", "缪尔赛思", "泡泡"


# ============================================================================
# §0 设置面清单（静态：分页 / 上限常量 / 每个字段都有写入口）
# ============================================================================
def test_section0_surface():
    print("\n=== §0 设置面清单（分页 / 上限常量 / 每个设置字段都有写入口） ===")
    from ui import settings as ui_settings

    eq("PAGES 四个分区、顺序＝时间轴→干员与心情→入宿设置→换心情",
       [p[0] for p in ui_settings.PAGES], ["timeline", "batch", "dorm", "entry"])
    eq("每个分区都有标题与一行说明",
       all(len(p) == 3 and p[1] and p[2] for p in ui_settings.PAGES), True)
    eq("MAX_CYCLES 是 7（唯一口径）", MAX_CYCLES, 7)

    # 「唯一口径」：界面下拉不许自己写死 1~7（源码里必须引用 MAX_CYCLES）
    src = (ROOT / "ui" / "settings.py").read_text(encoding="utf-8")
    check("界面周期下拉读 MAX_CYCLES（不是自己写死 7）",
          "MAX_CYCLES" in src and "range(1, MAX_CYCLES + 1)" in src)
    src_app = (ROOT / "ui" / "app.py").read_text(encoding="utf-8")
    check("ui/app.py 不写死周期上限（不含 `range(1, 8)` / `> 7` 之类）",
          "range(1, 8)" not in src_app and "cycles > 7" not in src_app)

    # settings_dict 的键集合（改设置面就要同步这里）
    s = sess_from_data({"facilities": [{"type": "贸易站", "level": 3,
                                        "operators": [PAO]}]})
    eq("settings_dict 的顶层键＝七个可调项",
       sorted(s.settings_dict()),
       ["cycles", "detached", "entry_events", "idle_to_dorm", "initial_moods",
        "mood_events", "start_clock"])
    eq("entry_events 子键六个（enabled/swap_with/scope/restore_back/when/per_shift）",
       sorted(s.settings_dict()["entry_events"]),
       ["enabled", "per_shift", "restore_back", "scope", "swap_with", "when"])
    # ⚠️ 2026-10 改断言：原来是"子键**四个**（含 `per_operator`）"—— `per_operator`
    #    随逐人「参不参与」整条撤销（`Session.idle_globals` / `.idle_entries` 已删），
    #    **字段本身不存在**，不是被放宽。同一条用例下面新增了"传了它只回 note"的钉子。
    eq("idle_to_dorm 子键三个（enabled/protected_slots/blacklist；`per_operator` 已撤）",
       sorted(s.settings_dict()["idle_to_dorm"]),
       ["blacklist", "enabled", "protected_slots"])

    # —— 「每个设置都有出口」：逐字段写一遍，读回来必须变（设置静默不生效的探针）——
    def _probe(label, mutate, read, want):
        t = sess_from_data({"facilities": [
            {"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": [PAO]},
            {"type": "贸易站", "level": 3, "operators": [DRAGON]}]})
        t.idle_to_dorm = False
        mutate(t)
        got = read(t)
        eq(f"设置字段可写可读：{label}", got, want)

    _probe("cycles（set_cycles）", lambda t: t.set_cycles(5), lambda t: t.cycles, 5)
    _probe("start_clock（set_start_clock）", lambda t: t.set_start_clock(3),
           lambda t: str(t.schedule.start_clock), "3")
    _probe("initial_moods（set_initial_mood）", lambda t: t.set_initial_mood(PAO, 7),
           lambda t: str(t.initial_moods[PAO]), "7")
    _probe("mood_events（set_mood_at）", lambda t: t.set_mood_at(PAO, 9, 6),
           lambda t: len(t.mood_events), 1)
    _probe("detached（set_detached）", lambda t: t.set_detached([MUR]),
           lambda t: t.bench_names(), [MUR])
    _probe("entry_events.enabled",
           lambda t: handle(t, "set_entry_events", {"enabled": True}),
           lambda t: t.entry_events, True)
    _probe("entry_events.swap_with",
           lambda t: handle(t, "set_entry_events", {"swap_with": WULIAN}),
           lambda t: t.entry_swap_with, WULIAN)
    _probe("entry_events.scope",
           lambda t: handle(t, "set_entry_events", {"scope": "anywhere"}),
           lambda t: t.entry_scope, "anywhere")
    _probe("entry_events.when",
           lambda t: handle(t, "set_entry_events", {"when": "wait"}),
           lambda t: t.entry_when, "wait")
    _probe("entry_events.per_shift",
           lambda t: handle(t, "set_entry_events",
                            {"per_shift": [{"key": 1, "enabled": True}]}),
           lambda t: [o.key for o in t.entry_per_shift], [1])
    _probe("idle_to_dorm.enabled",
           lambda t: handle(t, "set_idle_to_dorm", {"enabled": True}),
           lambda t: t.idle_to_dorm, True)
    _probe("idle_to_dorm.protected_slots",
           lambda t: handle(t, "set_idle_to_dorm", {"protected_slots": 3}),
           lambda t: t.idle_protected_slots, 3)
    _probe("idle_to_dorm.blacklist",
           lambda t: handle(t, "set_idle_to_dorm", {"blacklist": [MUR]}),
           lambda t: t.idle_blacklist, [MUR])
    # ⚠️ 2026-10 改断言：原来这一条是 `_probe("idle_to_dorm.per_operator", …)`，
    #    钉"传 `per_operator` ⇒ 落进 `t.idle_globals == {PAO: False}`"。
    #    **为什么变**：那个字段随功能整条撤销（不是"探针不生效"），所以改成钉**撤除口径**：
    #    传进来只回一条 `notes`、会话里**没有任何**状态被写（`hasattr` 都应为假）。
    t = sess_from_data({"facilities": [
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": [PAO]},
        {"type": "贸易站", "level": 3, "operators": [DRAGON]}]})
    t.idle_to_dorm = False
    out = handle(t, "set_idle_to_dorm", {"per_operator": [{"name": PAO, "enabled": False}]})
    eq("已撤的 `per_operator`：API 回一条「该设置已撤、已忽略」",
       out.get("notes"), ["per_operator（逐人「参不参与」）该设置已撤、已忽略"])
    eq("已撤的 `per_operator`：不落任何会话状态（字段本身不存在）",
       [hasattr(t, "idle_globals"), hasattr(t, "idle_entries")], [False, False])

    # ⚠️ `load_schedule` 那条路的 note 也要透出来：`load_file(s)` / `load_json` 走
    #    `LoadedSchedule.summary()`（note 在里面），只有这一个 op 的返回体原本没有它
    #    ⇒ 纯 API 调用方会看不到"自己带了个被忽略的键"。**additive**：不带旧键时**没有**这个键。
    u = sess_from_data({"facilities": [
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": [PAO]}]})
    out_n = handle(u, "load_schedule", {
        "facilities": [{"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": [PAO]}],
        "idle_to_dorm": {"per_operator": [{"name": PAO, "enabled": False}]}})
    eq("已撤的 `per_operator`：`load_schedule` 也回一条同措辞的 `notes`",
       out_n.get("notes"), ["per_operator（逐人「参不参与」）该设置已撤、已忽略"])
    out_plain = handle(u, "load_schedule", {
        "facilities": [{"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": [PAO]}]})
    eq("不带旧键时 `load_schedule` **没有** `notes` 键（additive、不动既有形状）",
       "notes" in out_plain, False)

    # 每个 op 都在 op 表里（别只写了函数忘了注册）
    for op in ("set_timeline", "set_slots", "set_room_level", "set_seat_lock",
               "clear_seat_locks", "set_moods", "set_initial_moods", "set_mood_at",
               "clear_mood_events", "restore_imported_moods", "set_training",
               "fill_from_pool", "set_entry_events", "set_idle_to_dorm", "set_detached",
               "export_schedule", "get_settings"):
        check(f"op 表里有 {op}", op in OPERATIONS)

    # 项目里**没有**「昼夜」这个设置面（用户清单里点名的"昼夜"实际不存在）
    blob = json.dumps(s.settings_dict(), ensure_ascii=False)
    check("设置面里没有「昼夜」键（该项目无昼夜设置；等效面＝宿舍氛围 atmosphere）",
          not any(w in blob for w in ("昼夜", "白天", "夜晚", "daynight", "is_day")))


# ============================================================================
# §1 时间轴与全局（周期数边界 / 起始时钟 / 时长 / 容量 / 氛围 / 缩容）
# ============================================================================
def test_section1_timeline():
    print("\n=== §1 时间轴与全局 ===")

    base = {"facilities": [{"type": "宿舍", "level": 5, "name": "宿舍#1",
                            "operators": [{"name": PAO, "mood": "10"}]},
                           {"type": "贸易站", "level": 3, "operators": [DRAGON]}]}

    # —— 周期数：越界取边界、不报错 ——
    for given, want in ((1, 1), (7, 7), (8, 7), (99, 7), (0, 1), (-3, 1)):
        s = sess_from_data(base)
        s.set_cycles(given)
        eq(f"set_cycles({given}) ⇒ {want}（越界取边界，不报错）", s.cycles, want)
    s = sess_from_data(base)
    handle(s, "set_timeline", {"cycles": 9})
    eq("API set_timeline(cycles=9) 也夹到 7", s.cycles, MAX_CYCLES)
    s.set_cycles(1)
    s.closure(cycles=99)
    eq("closure(cycles=99) 夹到 7 并改会话", s.cycles, MAX_CYCLES)
    eq("idle_groups(cycles=99) 也夹到 7（组数 ≤ 7×班次数）",
       len({g[1][0] for g in s.idle_groups(cycles=99)}) <= MAX_CYCLES, True)
    eq("float('3') 之类字符串也能夹（int(cycles)）",
       (lambda t: (t.set_cycles("5"), t.cycles)[1])(Session()), 5)

    # —— 起始时钟：只改显示口径 ——
    a = sess_from_data(base)
    a.set_cycles(2)
    a.recompute()
    before = {n: list(a.traj.moods[n]) for n in a.traj.names}
    a.set_start_clock(25)
    eq("set_start_clock(25) ⇒ 取模成 01:00（周期起点只认一天内的钟点）",
       str(a.schedule.start_clock), "1")
    a.recompute()
    eq("起始时钟不改引擎数值（逐位相同）",
       {n: list(a.traj.moods[n]) for n in a.traj.names}, before)
    eq("起始时钟不影响总时长", str(a.total_hours), str(a.schedule.cycle_hours * 2))

    # —— 各班时长 / 周期自洽 ——
    s = sess_from_data(base)
    eq("单班 24h 的周期＝24", str(s.schedule.cycle_hours), "24")
    bad = raises(s.set_timeline, hours=[12, 12])
    check("set_timeline(hours) 长度不符 ⇒ ValueError（不静默截断）",
          bad.startswith("ValueError"), f"实际 {bad!r}")
    bad = raises(s.set_timeline, cycle_hours=99)
    check("set_timeline(cycle_hours) 与各班长之和不符 ⇒ ValueError",
          bad.startswith("ValueError"), f"实际 {bad!r}")
    s.set_timeline(hours=[8])
    eq("改完班长时间 ⇒ 周期自动＝各班长之和", str(s.schedule.cycle_hours), "8")
    named = sess_from_shifts([shift("Shift 1 · 22h", 22, base["facilities"])])
    named.set_timeline(hours=[8])
    eq("改完班次名里的旧时长跟着改（Shift 1 · 22h → Shift 1 · 8h）",
       named.schedule.shift_labels(), ["Shift 1 · 8h"])
    kept = sess_from_shifts([shift("我的班", 22, base["facilities"])])
    kept.set_timeline(hours=[8])
    eq("名字里不含（或不等旧时长的）数字时**一字不动**",
       kept.schedule.shift_labels(), ["我的班"])

    # 按**班次名**写的逐班覆盖：改时长改名后仍要命中（否则静默失效）
    cfg = build_entry_event_config({"enabled": True,
                                    "per_shift": [{"key": "Shift 1 · 12h", "enabled": False}]})
    sch = Schedule([shift("Shift 1 · 12h", 12, base["facilities"], entry_events=cfg),
                    shift("Shift 2 · 12h", 12, base["facilities"], entry_events=cfg)], D(24))
    eq("改时长前：按名字的逐班覆盖命中第 1 班（关）",
       [sch.entry_config_for_shift(i).enabled for i in range(2)], [False, True])
    sch2 = sch.with_hours([6, 18])
    eq("改时长后班次名变成 6h/18h", sch2.shift_labels(), ["Shift 1 · 6h", "Shift 2 · 18h"])
    eq("按名字写的覆盖键跟着改名 ⇒ 仍命中第 1 班",
       [sch2.entry_config_for_shift(i).enabled for i in range(2)], [False, True])

    # —— 容量口径 ——
    eq("容量表：贸易站 1/2/3 级 = 1/2/3",
       [facility_slots(FacilityType.TRADING, k) for k in (1, 2, 3)], [1, 2, 3])
    eq("容量表：宿舍各等级都是 5",
       [facility_slots(FacilityType.DORMITORY, k) for k in (1, 2, 3, 4, 5)], [5] * 5)
    eq("容量表：超等级的等级取最后一档（贸易站 9 级 ⇒ 3）",
       facility_slots(FacilityType.TRADING, 9), 3)
    s = sess_from_data({"facilities": [{"type": "贸易站", "level": 3, "name": "贸易站#1",
                                        "operators": []}]})
    s.idle_to_dorm = False
    s.set_room_level(0, 0, 1)
    eq("set_room_level 改等级 ⇒ 容量跟着变（贸易站 3→1 级 = 3→1）",
       s.schedule.shifts[0].world.facilities[0].capacity, 1)
    bad = raises(s.set_room_level, 0, 99, 2)
    check("set_room_level 设施下标越界 ⇒ ValueError", bad.startswith("ValueError"),
          f"实际 {bad!r}")
    s = sess_from_data({"facilities": [{"type": "贸易站", "level": 3, "name": "贸易站#1",
                                        "capacity": 2, "operators": []}]})
    s.idle_to_dorm = False
    eq("`capacity` 覆盖优先于等级表（等级 3 但容量 2）",
       s.schedule.shifts[0].world.facilities[0].capacity, 2)

    # —— 氛围：进增量指纹，改完增量重算必须逐位等于全量 ——
    a = sess_from_data({"facilities": [
        {"type": "宿舍", "level": 1, "name": "宿舍#1", "operators": [{"name": PAO, "mood": "5"}]},
        {"type": "贸易站", "level": 3, "operators": [DRAGON]}]})
    a.idle_to_dorm = False
    a.set_cycles(3)
    a.recompute()
    facs = a.facilities_of(0)
    facs[0]["atmosphere"] = 5000
    a.replace_facilities(0, facs)                       # 走增量（前缀复用）
    inc = {n: list(a.traj.moods[n]) for n in a.traj.names}
    b = sess_from_data({"facilities": [
        {"type": "宿舍", "level": 1, "name": "宿舍#1", "operators": [{"name": PAO, "mood": "5"}],
         "atmosphere": 5000},
        {"type": "贸易站", "level": 3, "operators": [DRAGON]}]})
    b.idle_to_dorm = False
    b.set_cycles(3)
    b.recompute()
    eq("改宿舍氛围后：增量重算逐位等于全量（摘要覆盖 atmosphere）",
       inc, {n: list(b.traj.moods[n]) for n in b.traj.names})
    close("氛围影响回复（5000 氛围 ⇒ 1.5+0.1+2.0＝3.6/h 回复，净变化对得上）",
          a.rate_at(PAO, 1), -(dormitory_recovery(1, 5000)), "1e-9")
    check("宿舍回复是**负速率**（= 净回复，口径 I = 消耗 − 回复）",
          a.rate_at(PAO, 1) < ZERO, f"实际 {a.rate_at(PAO, 1)}")


def test_section1_shrink():
    print("\n=== §1b 容量收缩（_trim_to_capacity） ===")

    def with_slots(fac, level=3):
        s = sess_from_data({"facilities": [dict({"type": "贸易站", "name": "贸易站#1",
                                                 "level": level}, **fac)]})
        s.idle_to_dorm = False
        return s

    # ① 越界格子**全是人** ⇒ 一个人都不许删（交给自检报超容量）
    s = with_slots({"operators": ["甲", "乙", "丙", "丁"]})
    s.set_room_level(0, 0, 2)
    eq("缩容：越界全是人 ⇒ 4 人原样保留",
       seat_values(fac_of(s)), ["甲", "乙", "丙", "丁"])
    msgs = s.validate().messages()
    check("缩容：越界全是人 ⇒ 自检报出「超过 Lv2 容量」（不静默）",
          any("超过 Lv2 容量" in m for m in msgs), f"实际 {msgs}")

    # ② ⚠ 越界处**既有空洞又有人** —— 2026-10 修（A2 工单）：旧实现只看"越界段里有没有
    #    **空位**"，有一个空位就放行，接着 `keep = values[:capacity]` 把整段（**连带里面的人**）
    #    截断 ⇒ 静默删掉「丁」，而且 `validate()` 干净、0 告警。
    #    新判据＝"越界段里**还有没有人**"：有人就一律保留，交给自检报超容量（与 ① 同口径）。
    s = with_slots({"slots": ["甲", "乙", None, "丁"]})
    s.set_room_level(0, 0, 2)
    got = seat_values(fac_of(s))
    eq("缩容：越界处有空洞+后面有人 ⇒ **保留 4 人**（位次留在原位，不左移、不删人）",
       got, ["甲", "乙", "", "丁"])
    msgs = s.validate().messages()
    check("缩容：越界处有空洞+后面有人 ⇒ 自检报出「超过 Lv2 容量」（不静默）",
          any("超过 Lv2 容量" in m for m in msgs), f"实际 {msgs}")

    # ③ 容量没变小 ⇒ 不动数据（含台账与练度对象）
    s = with_slots({"slots": [{"name": "甲", "elite": 1}, "乙"]})
    s.set_room_level(0, 0, 3)
    eq("缩容：容量没变小 ⇒ 一字不动（练度对象写法保住）",
       seat_specs(fac_of(s)), [{"name": "甲", "elite": 1}, "乙"])

    # ④ 容量**覆盖**（`capacity`）优先于等级表：同一个 level 不触发裁剪
    s = with_slots({"capacity": 2, "slots": ["甲", None, "丙"]})
    s.set_room_level(0, 0, 3)
    eq("缩容：`capacity` 覆盖恒定 ⇒ 不触发裁剪（3 位原样）",
       seat_values(fac_of(s)), ["甲", "", "丙"])


# ============================================================================
# §2 干员与心情（练度 / 起点心情 / 心情锚点 / 房间等级）
# ============================================================================
def test_section2_batch():
    print("\n=== §2 干员与心情（练度 / 心情 / 房间等级） ===")

    base = {"facilities": [
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": [PAO, MUR]},
        {"type": "贸易站", "level": 3, "operators": [DRAGON]}]}

    # —— 练度 ——
    s = sess_from_data(base)
    s.idle_to_dorm = False
    n = s.set_training(elite=1)
    eq("set_training(elite=1) 改动位置数＝在场 3 人", n, 3)
    eq("非满练写成对象（`{name, elite, level}`）",
       seat_specs(fac_of(s)), [{"name": PAO, "elite": 1, "level": 30},
                               {"name": MUR, "elite": 1, "level": 30}])
    n = s.set_training(elite=2, level=30)
    eq("改回满练 ⇒ 又变回纯字符串（JSON 保持简洁）", seat_specs(fac_of(s)), [PAO, MUR])
    eq("满练时改动数＝3（值真的变了才算改动）", n, 3)

    s = sess_from_data(base)
    s.idle_to_dorm = False
    s.set_training(elite=1, names=[PAO])
    eq("按名单改练度：只动她", seat_specs(fac_of(s)), [{"name": PAO, "elite": 1, "level": 30},
                                                       MUR])
    eq("elite_badges 只列非满练", s.elite_badges(), {PAO: "E1"})
    check("elite_text 写明「因未满练少 N 条」", "练度 E1" in s.elite_text(PAO))

    # ⚠ 按**位次**读（`slots` 优先）：曾因只读 `operators` 而静默漏掉整房住户
    s = sess_from_data({"facilities": [
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "slots": [PAO, None, MUR]}]})
    s.idle_to_dorm = False
    s.set_training(elite=1)
    eq("slots 型设施：练度按位次写到**真的那两位**（空洞保留）",
       seat_specs(fac_of(s)),
       [{"name": PAO, "elite": 1, "level": 30}, "", {"name": MUR, "elite": 1, "level": 30}])
    n = s.set_training(level=1)
    eq("slots 型设施：第二次改也命中两位（漏读就会是 0）", n, 2)

    # 练度进增量指纹（它是**就地改干员对象**的，对象身份不变）
    a = sess_from_data(base)
    a.idle_to_dorm = False
    a.set_cycles(3)
    a.recompute()
    a.set_training(elite=1)
    inc = {n_: list(a.traj.moods[n_]) for n_ in a.traj.names}
    b = sess_from_data(base)
    b.idle_to_dorm = False
    b.set_cycles(3)
    b.set_training(elite=1)
    b.recompute()
    eq("改练度后：增量重算逐位等于全量（摘要覆盖 elite/level）",
       inc, {n_: list(b.traj.moods[n_]) for n_ in b.traj.names})

    # —— 起点心情 / 心情锚点 ——
    s = sess_from_data(base)
    s.idle_to_dorm = False
    imported = s.imported_moods()
    eq("imported_moods 是「导入时」的起点（布局里写的值）", str(imported[PAO]), "24")
    s.set_initial_mood(PAO, 7)
    s.recompute()
    eq("set_initial_mood ⇒ 周期起点那一刻就是 7（比 Decimal 值，不比字符串）",
       to_decimal(s.mood_at(PAO, 0)), D(7))
    ev = s.set_mood_at(PAO, 3, 0)
    eq("第 1 周期 0:00 的锚点被**路由**成起点心情（不是锚点）", ev, None)
    eq("路由后 initial_moods 变 3、mood_events 仍空",
       (str(s.initial_moods[PAO]), len(s.mood_events)), ("3", 0))
    ev = s.set_mood_at(PAO, 20, 6)
    eq("周期内其他时刻 ⇒ 生成锚点（MoodSetEvent）", (ev.t, ev.mood, ev.cycle),
       (D(6), D(20), 1))
    s.set_mood_at(PAO, 15, 6)
    eq("同一(周期,时刻,人) 只留一条（后者覆盖）",
       [(str(e.t), str(e.mood)) for e in s.mood_events], [("6", "15")])
    s.set_mood_at(PAO, 15, 0)                       # 第 1 周期 0:00 → 起点
    eq("同刻再一次 set_mood_at(…, 0) 仍是起点路由", len(s.mood_events), 1)
    s.set_mood_at(PAO, 99, 6)
    eq("心情锚点钳位到 [0, 24]（99 → 24）", str(s.mood_events[0].mood), "24")
    s.recompute()
    eq("锚点在那一刻**同刻跳变**生效（mood_at(6) 就是设定值，且之后继续演化）",
       to_decimal(s.mood_at(PAO, 6)), D(24))
    s.set_mood_at(PAO, 4, 6)
    s.recompute()
    eq("改小锚点值后再算（同刻改写，不叠加）", to_decimal(s.mood_at(PAO, 6)), D(4))
    s.restore_imported_moods()
    s.recompute()
    eq("restore_imported_moods 清两组（起点回导入值、锚点清空）",
       (s.initial_moods, s.mood_events), ({}, []))

    # 周期数之外的锚点不生效
    s = sess_from_data(base)
    s.idle_to_dorm = False
    s.set_cycles(1)
    s.set_mood_at(PAO, 5, 6, ) if False else None
    ev = MoodSetEvent(name=PAO, t=D(6), mood=D(5), cycle=3)
    s.mood_events = [ev]
    s.recompute()
    check("周期数之外的锚点不生效（也不报错）",
          to_decimal(s.mood_at(PAO, 6)) != D(5), f"实际 {s.mood_at(PAO, 6)}")

    # —— API 心情写法 ——
    s = sess_from_data(base)
    s.idle_to_dorm = False
    handle(s, "set_initial_moods", {"moods": {"全部": 12}})
    eq("API set_initial_moods 的「全部」写法 ⇒ 全员 12",
       sorted({str(v) for v in s.initial_moods.values()}), ["12"])
    eq("「全部」覆盖全员（人数＝排班人数）", len(s.initial_moods), len(s.operator_names()))
    handle(s, "set_mood_at", {"name": PAO, "mood": 9, "at": "06:30"})
    eq("API 支持钟点写法（06:30 ⇒ t=6.5）",
       [(str(e.t), str(e.mood)) for e in s.mood_events], [("6.5", "9")])
    handle(s, "clear_mood_events", {})
    eq("clear_mood_events 只清锚点、不动起点", (len(s.mood_events), str(s.initial_moods[PAO])),
       (0, "12"))
    handle(s, "restore_imported_moods", {})
    eq("API restore_imported_moods 清两组", (s.initial_moods, s.mood_events), ({}, []))


# ============================================================================
# §3 锁定入宿（摆位即上锁 / 清空即解锁 / 粒度 / 后者覆盖 / 回原位）
# ============================================================================
def test_section3_lock():
    print("\n=== §3 锁定入宿（摆位即上锁 / 清空即解锁 / 粒度 / 后者覆盖） ===")

    base = {"facilities": [
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": ["甲", "乙", "丙"]},
        {"type": "贸易站", "level": 3, "name": "贸易站#1", "operators": ["戊"]}]}

    def fresh():
        s = sess_from_data(base)
        s.idle_to_dorm = False
        return s

    # —— 摆位即上锁 ——
    s = fresh()
    s.place_operator(0, 0, 3, "丁")                       # 第 4 位
    eq("place_operator：摆位即上锁（位次进 slots、人进 names）",
       manual_of(fac_of(s)), {"slots": [3], "names": ["丁"]})
    eq("place_operator：位次按 0 基记（第 4 位 ⇒ 3）", seat_values(fac_of(s))[3], "丁")
    s.place_operator(0, 0, 3, "")
    eq("清空该位 ⇒ 那一位**退出台账**（＝交还自动入宿）；尾部空槽也不会留在快照里",
       (manual_of(fac_of(s)), seat_values(fac_of(s))), ({}, ["甲", "乙", "丙"]))
    eq("清空后位次**不左移**（前面的人不动）",
       [n for n in seat_values(fac_of(s)) if n], ["甲", "乙", "丙"])

    # —— 逐位粒度：只锁碰过的位次（累积） ——
    s = fresh()
    s.set_facility_slots(0, 0, [None, "乙", None, "丁"], touched=[1, 3])
    eq("界面逐位摆位（touched）⇒ 台账只记碰过且现在有人的位次",
       manual_of(fac_of(s)), {"slots": [1, 3], "names": ["丁", "乙"]})
    s.set_facility_slots(0, 0, [None, "乙", None, "丁", "己"], touched=[4])
    eq("第二次碰第 5 位 ⇒ **累积**（旧锁保留 ＋ 新位次）",
       manual_of(fac_of(s)), {"slots": [1, 3, 4], "names": ["丁", "乙", "己"]})
    s.set_facility_slots(0, 0, [None, "乙", None, "丁", None], touched=[4])
    eq("清空第 5 位 ⇒ 只让**那一格**退出台账（前两次的锁还在）",
       manual_of(fac_of(s)), {"slots": [1, 3], "names": ["丁", "乙"]})

    # —— 老契约：不传 touched ⇒ 传进来的整段里"有名字的"全锁 ——
    s = fresh()
    s.set_slots(0, 0, ["甲", "", "丙", "丁"])
    eq("set_slots（不传 touched）⇒ 整段有名字的位次全进台账（既有 API 契约）",
       manual_of(fac_of(s)), {"slots": [0, 2, 3], "names": ["丁", "丙", "甲"]})
    s2 = fresh()
    s2.set_facility_slots(0, 0, ["甲", "", "丙", "丁"])          # 同样不传 touched
    eq("set_facility_slots（不传 touched）与 set_slots 同口径",
       manual_of(fac_of(s2)), manual_of(fac_of(s)))
    s3 = fresh()
    s3.set_slots(0, 0, ["甲", "乙", "丙"], manual=False)
    eq("manual=False：只改布局、**不打锁**", manual_of(fac_of(s3)), {})

    # —— 手动锁真的挡住自动入宿 ——
    # ⚠️ 2026-10：这一条原来**钉的是缺陷现状**（"被顶掉的甲在闲置入宿候选表里也查不到"）
    #    —— 同一根因（她已从整份排班消失）。修法＝`Schedule.roster`：她仍在心情表里，
    #    于是她照旧进"候选池"（`idle_groups` 逐行扫描 `traj.names`）。**没进那张表**的
    #    原因变成了**心情闸**（她缺省满 24、不需要恢复），不再是"人不见了"。
    s = sess_from_data(base)
    s.idle_protected_slots = 0
    s.place_operator(0, 0, 0, "庚")                       # 把庚钉在第 1 位（甲被顶掉）
    s.recompute()
    eq("被手动钉住的位次不进自动入宿的候选/目标（庚留在原位）",
       [n for n in layout_names(s, 0)["宿舍#1"]], ["庚", "乙", "丙"])
    eq("被顶掉的甲**仍在心情表里**（修前她整个人从排班消失 ⇒ 面板也查不到）",
       ("甲" in s.operator_names(), str(s.mood_at("甲", 0))), (True, "24"))
    eq("被顶掉的甲**不在 `facilities` 里**（不参与任何技能计数）",
       s.schedule.shifts[0].world.get_operator("甲"), None)
    # 把她的心情调到 < 24 ⇒ 她真的会进候选池（顺带证明"不在表里"的旧解释已不成立）
    s.initial_moods["甲"] = to_decimal("10")
    s.recompute()
    ids = [r[0] for g in s.idle_groups() for r in g[2]]
    check("心情 < 24 的被顶掉者出现在闲置入宿候选表里（候选池并上了导入名册）",
          "甲" in ids, f"实际候选 {ids}")

    # —— 锁空位（API 专属能力："预留空位"＝保持空着） ——
    s = sess_from_data(base)
    s.idle_protected_slots = 0
    s.set_seat_lock(0, 0, 1, True)                        # 第 2 位锁住（有人）
    s.set_seat_lock(0, 0, 2, True)                        # 第 3 位锁住（空）
    eq("set_seat_lock 只改锁、不动占位",
       (manual_of(fac_of(s)), seat_values(fac_of(s))[:3]),
       ({"slots": [1, 2], "names": []}, ["甲", "乙", "丙"][:3]))
    s.recompute()
    eq("锁住的座位保持原样（引擎不往里塞人）",
       [n for n in layout_names(s, 0)["宿舍#1"]], ["甲", "乙", "丙"])
    s.clear_seat_locks()
    eq("clear_seat_locks 连 names 一起清（只清 slots 会看起来像「解锁失败」）",
       manual_of(fac_of(s)), {})
    s = sess_from_data(base)
    s.idle_to_dorm = False
    bad = raises(s.set_seat_lock, 0, 0, 99, True)
    check("set_seat_lock 越界 ⇒ ValueError（写明容量），不静默忽略",
          bad.startswith("ValueError") and "容量" in bad, f"实际 {bad!r}")

    # —— 同班同人两位次 ⇒ 后者覆盖 ——
    s = fresh()
    s.place_operator(0, 0, 0, "甲")
    s.place_operator(0, 0, 4, "甲")
    vals = seat_values(fac_of(s))
    eq("place_operator 同一人两次 ⇒ **后者覆盖**（第一处腾空、不左移）",
       (vals[0], vals[4]), ("", "甲"))
    eq("台账只剩最后那一格", manual_of(fac_of(s))["slots"], [4])
    s = fresh()
    s.place_operators({0: [(0, 0, "甲"), (0, 3, "甲")]})
    eq("place_operators 一次调用里同一人两格 ⇒ 后写的那次说了算",
       (seat_values(fac_of(s))[0], seat_values(fac_of(s))[3]), ("", "甲"))
    s = fresh()
    # 「干员与心情」位置列：面板交来的整班布局，后者（面板顺序靠后的那一处）赢
    s.apply_manual_shifts({0: [{"type": "贸易站", "level": 3, "operators": ["甲"]},
                               {"type": "宿舍", "level": 5, "name": "宿舍#1",
                                "operators": ["甲", "乙", "丙"]}]})
    eq("apply_manual_shifts：同一人两处 ⇒ 按面板交来的**先后**（后者赢＝留在宿舍、"
       "贸易站那一处被摘空）",
       (seat_values(s.facilities_of(0)[0]), seat_values(s.facilities_of(0)[1])[0]),
       ([], "甲"))

    # —— 台账粒度：apply_manual_shifts 只锁"真的改了的那几位" ——
    s = fresh()
    before = s.facilities_of(0)
    before[1]["operators"] = ["乙", "丙", "丁"]            # 只动第 2 间（贸易站）
    s.apply_manual_shifts({0: before})
    eq("apply_manual_shifts 逐位差异 ⇒ 只锁改了的那几位（没动的宿舍不连坐）",
       (manual_of(fac_of(s, 0, 0)), manual_of(fac_of(s, 0, 1))),
       ({}, {"slots": [0, 1, 2], "names": ["丁", "丙", "乙"]}))

    # —— 「不在基建」名单 vs 锁：三条拒绝 ——
    s = fresh()
    s.set_detached(["甲"])                                # 甲 没被锁 ⇒ 允许
    eq("set_detached：没被锁的人可以进名单", s.detached, ["甲"])
    s = fresh()
    s.place_operator(0, 0, 0, "甲")                        # 甲 被锁在第 1 位
    bad = raises(s.set_detached, ["甲"])
    check("set_detached：**已被手动锁**的人 ⇒ 拒绝（ValueError，写明第 N 班 X 第 M 位）",
          bad.startswith("ValueError") and "第 1 班" in bad and "第 1 位" in bad,
          f"实际 {bad!r}")
    s = fresh()
    s.set_detached(["甲"], remove_from_slots=False)        # 造"名单 ∧ 在位"
    bad = raises(s.set_seat_lock, 0, 0, 0, True)
    check("set_seat_lock：要锁的那一格坐着名单里的人 ⇒ 拒绝（与上一条对称）",
          bad.startswith("ValueError") and "不在基建" in bad, f"实际 {bad!r}")
    s = fresh()
    s.set_detached(["甲"])
    note = []
    s.place_operator(0, 0, 0, "甲")                        # 把名单里的人放回位次
    eq("place_operator：名单里的人 ⇒ **先剔名单再写入**（不抛错）", s.detached, [])
    eq("写入照常生效（她回到第 1 位、且进台账）",
       (seat_values(fac_of(s))[0], manual_of(fac_of(s))["slots"]), ("甲", [0]))

    # —— locked_seats_of / 只读编辑器数据 ——
    s = fresh()
    s.set_seat_lock(0, 0, 2, True)                         # 锁一个空位 → 只 pins_slot
    eq("locked_seats_of 判据＝位次被钉 **或** 人被钉（锁空位只钉位次）",
       s.locked_seats_of("乙"), [])
    s.place_operator(0, 0, 1, "乙")
    eq("locked_seats_of：她自己被钉 ⇒ 列出 (班, 房, 显示名, 位次)",
       s.locked_seats_of("乙"), [(0, 0, "宿舍#1", 1)])
    st = s.manual_dorm_editor_state(0)
    eq("manual_dorm_editor_state：按**容量**铺满位次（宿舍 5 位）", len(st["dorms"][0]["seats"]), 5)
    eq("manual_dorm_editor_state：locked 与 _seat_verdict 第 1 层同口径（锁空位 + 锁人）",
       [d["locked"] for d in st["dorms"][0]["seats"]], [False, True, True, False, False])
    eq("manual_dorm_editor_state：显示名是补名之后的 `宿舍#1`", st["dorms"][0]["name"], "宿舍#1")

    # —— 显示名 / 补名（锁定区与锁的稳定键靠它） ——
    s = sess_from_data({"facilities": [{"type": "宿舍", "level": 1, "operators": []},
                                       {"type": "宿舍", "level": 1, "operators": []}]})
    eq("未命名设施自动补名（宿舍 ⇒ 宿舍#1 / 宿舍#2）",
       [f.display_name for f in s.schedule.shifts[0].world.facilities],
       ["宿舍#1", "宿舍#2"])
    s = sess_from_data({"facilities": [{"type": "宿舍", "level": 1, "name": "宿舍#2",
                                        "operators": []},
                                       {"type": "宿舍", "level": 1, "operators": []}]})
    eq("补名**跳过**用户显式占用的名字（⇒ 宿舍#1）",
       [f.display_name for f in s.schedule.shifts[0].world.facilities],
       ["宿舍#2", "宿舍#1"])

    # —— 回原位 / 恢复默认（轻量钉住，权威覆盖在 verify_idle.py ⑧） ——
    s = Session()
    s.load_paths([MAA_SAMPLE])
    dorm1 = next(i for i, f in enumerate(s.facilities_of(0))
                 if seat_values(f)[:1] == [FEI])
    s.place_operator(0, dorm1, 1, FEI)                     # 从第 1 位挪到第 2 位
    note = s.release_seat(0, dorm1, 1)
    eq("release_seat：取消指定 ⇒ 她回**导入原位**（回执写明）",
       (seat_values(fac_of(s, 0, dorm1))[0], "导入原位" in note), (FEI, True))
    eq("回退**不打新标**（她不再算我手动放的人）", manual_of(fac_of(s, 0, dorm1)).get("names"),
       None)

    # —— clear_seat_locks(shift_index) 只清那一班 ——
    s = sess_from_shifts([shift("A", 12, [{"type": "宿舍", "level": 5, "name": "宿舍#1",
                                           "operators": ["甲"]}]),
                          shift("B", 12, [{"type": "宿舍", "level": 5, "name": "宿舍#1",
                                           "operators": ["乙"]}])])
    s.idle_to_dorm = False
    s.place_operator(0, 0, 0, "甲")
    s.place_operator(1, 0, 0, "乙")
    eq("clear_seat_locks(0) 只清第 1 班",
       (s.clear_seat_locks(0), manual_of(fac_of(s, 0, 0)), manual_of(fac_of(s, 1, 0))),
       (1, {}, {"slots": [0], "names": ["乙"]}))


# ============================================================================
# §4 换心情（触发者口径 / 「满 24」门 / 三种 when / scope / 逐班覆盖）
# ============================================================================
ENTRY_FACS = [{"type": "宿舍", "level": 5, "name": "宿舍#1",
               "operators": [{"name": FEI, "mood": "24"}, {"name": MUR, "mood": "3"}]},
              {"type": "制造站", "level": 3, "operators": [{"name": DRAGON, "mood": "2"}]}]


def _entry_run(fei_mood="24", when="immediate", swap_with=DRAGON, scope="anywhere",
               detached=(), extra_shifts=(), cycles=1, **kw):
    """单班 12h + 菲亚梅塔在宿舍：跑一次，返回 (轨迹, 事件说明)。"""
    facs = copy.deepcopy(ENTRY_FACS)
    facs[0]["operators"][0]["mood"] = str(fei_mood)
    shifts = [shift("班1", 12, facs, detached=list(detached))]
    shifts += list(extra_shifts)
    sch = Schedule(shifts, sum((s.hours for s in shifts), ZERO), ZERO, list(detached))
    traj = simulate_schedule(sch, cycles=cycles, entry_events=True, entry_swap_with=swap_with,
                             entry_scope=scope, entry_when=when, idle_to_dorm=False, **kw)
    return traj


def test_section4_entry():
    print("\n=== §4 换心情（触发者 / 「满 24」门 / 三种 when / scope / 逐班覆盖） ===")

    # —— 触发者三口径：本班任一设施 / 本班未排班 / 显式名单 ——
    tr = _entry_run()
    check("触发者①她进驻在**宿舍**⇒ 换（与自动挑的龙舌兰互换）",
          any("互换" in s for s in entry_labels(tr)), f"实际 {entry_labels(tr)}")
    tr = _entry_run(swap_with=None, scope="anywhere")               # 她一个人也不在宿舍也行
    check("触发者①'她在**工作设施**也照样触发（不再要求宿舍）",
          any("互换" in s for s in entry_labels(tr)), f"实际 {entry_labels(tr)}")
    # 她本班未排班（不在任何设施），但在**实时心情表**里（另一个班有她）
    other = shift("班2", 12, [{"type": "贸易站", "level": 3, "operators": [FEI]}])
    facs = copy.deepcopy(ENTRY_FACS)
    facs[0]["operators"] = [{"name": MUR, "mood": "3"}]              # 第 1 班没有她
    sch = Schedule([shift("班1", 12, facs), other], D(24))
    traj = simulate_schedule(sch, cycles=1, entry_events=True, entry_swap_with=DRAGON,
                             entry_scope="anywhere", entry_when="immediate", idle_to_dorm=False,
                             initial_moods={FEI: D(24)})
    check("触发者②**本班未排班**（第 1 班没有她）⇒ 第 1 班班初也触发",
          any(m.t == ZERO and "互换" in m.label for m in entry_marks(traj)) or
          any("互换" in m.label for m in entry_marks(traj)), f"实际 {entry_labels(traj)}")
    # 显式「不在基建」名单
    facs = copy.deepcopy(ENTRY_FACS)
    facs[0]["operators"] = [{"name": MUR, "mood": "3"}]
    sch = Schedule([shift("班1", 12, facs, detached=[FEI])], D(12), ZERO, [FEI])
    traj = simulate_schedule(sch, cycles=1, entry_events=True, entry_swap_with=DRAGON,
                             entry_scope="anywhere", entry_when="immediate", idle_to_dorm=False,
                             initial_moods={FEI: D(24)})
    check("触发者③显式「不在基建」名单 ⇒ 照样触发（有意例外）",
          any("互换" in m.label for m in entry_marks(traj)), f"实际 {entry_labels(traj)}")

    # —— 「满 24」这道门在三种模式下都生效 ——
    for when in ("immediate", "full", "wait"):
        for mood, should in (("24", True), ("20", False), ("23.99", False)):
            tr = _entry_run(fei_mood=mood, when=when)
            labels = entry_labels(tr)
            swapped0 = any(m.t == ZERO and "互换" in m.label for m in entry_marks(tr))
            if when == "wait" and not should:
                check(f"「满 24」门：when={when} 她 {mood} ⇒ **班初不换**，"
                      f"但记一条「等她回满」（之后回满那一刻才换）",
                      (not swapped0) and any("等她回满" in s for s in labels) and
                      any("互换" in s for s in labels), f"实际 {labels}")
            else:
                check(f"「满 24」门：when={when} 她 {mood} ⇒ "
                      f"{'换' if should else '不换（一次都不换）'}",
                      swapped0 == should and (should or not any("互换" in s for s in labels)),
                      f"实际 换={swapped0}（{labels}）")
    tr = _entry_run(fei_mood="20", when="wait")
    check("when=wait：她回满 24 的**那一刻**才换（t≈2.00 换，不是 t=0）",
          any(abs(m.t - D(2)) <= D("0.01") and "互换" in m.label for m in entry_marks(tr)),
          f"实际 {[(str(m.t), m.label[:28]) for m in entry_marks(tr)]}")
    check("wait：她换到的是对方**当时**的心情（龙舌兰已经耗到 0 ⇒ 她接手的不是 2）",
          to_decimal(tr.mood_at(FEI, D("2.05"))) < D(1),
          f"实际 {tr.mood_at(FEI, D('2.05'))}")
    close("wait：对方拿到她当时的 24", tr.mood_at(DRAGON, D("2.05")), 24, "0.05")

    # —— 双方心情相同也照换（不许给 full/wait 补"等值就跳过"） ——
    facs = copy.deepcopy(ENTRY_FACS)
    facs[0]["operators"] = [{"name": FEI, "mood": "24"}, {"name": MUR, "mood": "24"}]
    sch = Schedule([shift("班1", 12, facs)], D(12))
    tr = simulate_schedule(sch, cycles=1, entry_events=True, entry_swap_with=MUR,
                           entry_scope="anywhere", entry_when="full", idle_to_dorm=False)
    labels = entry_labels(tr)
    check("双方心情相同（都 24）也照换、事件照记（用户口径，三种 when 都一样）",
          any("互换" in s for s in labels), f"实际 {labels}")

    # —— scope ——
    tr = _entry_run(swap_with=DRAGON, scope="dorm")        # 龙舌兰在制造站 ⇒ 同宿舍找不到
    check("scope=dorm：指定对象不在**同一间** ⇒ 不换 + 记一条「不在宿舍#1」",
          (not any("互换" in s for s in entry_labels(tr))) and
          any("不在宿舍#1" in s for s in entry_labels(tr)), f"实际 {entry_labels(tr)}")
    tr = _entry_run(swap_with=DRAGON, scope="anywhere")
    check("scope=anywhere：改任意位置就换得到", any("互换" in s for s in entry_labels(tr)))
    tr = _entry_run(swap_with="不存在的人", scope="anywhere")
    check("点名的人不存在 ⇒ 不换 + 记一条「不在基建内」（不静默）",
          (not any("互换" in s for s in entry_labels(tr))) and
          any("不在基建内" in s for s in entry_labels(tr)), f"实际 {entry_labels(tr)}")

    # —— 默认「前一位进驻」 ——
    facs = [{"type": "宿舍", "level": 5, "name": "宿舍#1",
             "operators": [{"name": FEI, "mood": "24"}, {"name": MUR, "mood": "3"}]}]
    sch = Schedule([shift("班1", 12, facs)], D(12))
    tr = simulate_schedule(sch, cycles=1, entry_events=True, entry_scope="dorm",
                           entry_when="immediate", idle_to_dorm=False)
    check("默认口径＝「前一位进驻」（她排第 1 位 ⇒ 没有前一位 ⇒ 记一条说明）",
          any("前一位进驻" in s for s in entry_labels(tr)), f"实际 {entry_labels(tr)}")
    facs = [{"type": "宿舍", "level": 5, "name": "宿舍#1",
             "operators": [{"name": MUR, "mood": "3"}, {"name": FEI, "mood": "24"}]}]
    sch = Schedule([shift("班1", 12, facs)], D(12))
    tr = simulate_schedule(sch, cycles=1, entry_events=True, entry_scope="dorm",
                           entry_when="immediate", idle_to_dorm=False)
    check("默认口径＝「前一位进驻」：她排第 2 位 ⇒ 与第 1 位互换",
          any("前一位进驻" in s and "互换" in s for s in entry_labels(tr)),
          f"实际 {entry_labels(tr)}")

    # —— find_entry_target 直接验（四种 kind） ——
    world = build_base_layout({"facilities": copy.deepcopy(ENTRY_FACS)})
    holder = world.get_operator(FEI)
    fac = world.facility_of(FEI)
    other, note = find_entry_target(world, holder, fac, DRAGON, "anywhere")
    eq("find_entry_target：anywhere + 人名 ⇒ 找得到", other.name if other else None, DRAGON)
    other, note = find_entry_target(world, holder, fac, DRAGON, "dorm")
    eq("find_entry_target：dorm + 人名不同间 ⇒ None + 说明",
       (other, "不在" in note), (None, True))
    other, note = find_entry_target(world, holder, fac, "any", "dorm")
    eq("find_entry_target：`any` ⇒ 自动挑全基建最累的（龙舌兰 2）",
       other.name if other else None, DRAGON)
    # 她排第 2 位（缪尔赛思在前）⇒ 默认口径＝「前一位进驻」
    world2 = build_base_layout({"facilities": [
        {"type": "宿舍", "level": 5, "name": "宿舍#1",
         "operators": [{"name": MUR, "mood": "3"}, {"name": FEI, "mood": "24"}]}]})
    holder2, fac2 = world2.get_operator(FEI), world2.facility_of(FEI)
    other, note = find_entry_target(world2, holder2, fac2, None, "dorm")
    eq("find_entry_target：dorm + 不点名 ⇒ 前一位进驻（缪尔赛思）",
       other.name if other else None, MUR)
    other, note = find_entry_target(world, holder, fac, None, "dorm")
    eq("find_entry_target：她在第 1 位 ⇒ 没有前一位，返回 None + 说明",
       (other, "没有「前一位进驻」" in note), (None, True))
    other, note = find_entry_target(world, holder, None, None, "dorm")
    eq("find_entry_target：facility=None（她本班未排班）⇒ 没有前一位可换",
       (other, "不属于任何房间" in note), (None, True))

    # —— 幂等：同一份快照只结算一次；reset 后可再结算 ——
    world = build_base_layout({"facilities": copy.deepcopy(ENTRY_FACS)})
    first = apply_entry_events(world, enabled=True, swap_with=DRAGON, scope="anywhere",
                               when="immediate")
    second = apply_entry_events(world, enabled=True, swap_with=DRAGON, scope="anywhere",
                                when="immediate")
    eq("apply_entry_events 幂等：同一份快照连调两次，第二次 0 条",
       (len([e for e in first if e.group == "entry_swap"]), len(second)), (1, 0))
    eq("reset_entry_events 归位标记（她刚换过 ⇒ 返回 1）", reset_entry_events(world), 1)
    eq("reset 之后所有干员的 entry_swapped 都归零",
       [o.name for o in world.all_operators() if getattr(o, "entry_swapped", False)], [])

    # —— 名单里的人：新心情写回那张表 ——
    facs = copy.deepcopy(ENTRY_FACS)
    facs[0]["operators"] = [{"name": MUR, "mood": "3"}]
    sch = Schedule([shift("班1", 24, facs, detached=[FEI])], D(24), ZERO, [FEI])
    traj = simulate_schedule(sch, cycles=3, entry_events=True, entry_swap_with=DRAGON,
                             entry_scope="anywhere", entry_when="wait",
                             idle_to_dorm=False, initial_moods={FEI: D(24)})
    swaps = [m.t for m in entry_marks(traj) if "互换" in m.label]
    eq("名单里的她换完**写回**那张表（3 周期只换一次，不是每周期重复）",
       swaps, [ZERO])
    close("换完她的新心情跨执行点/跨周期连续（一路 2）",
          traj.mood_at(FEI, D(23.9)), 2)
    check("她仍不在 facilities 里（不参与计数）",
          traj.rate_at(FEI, 1) == ZERO, f"实际速率 {traj.rate_at(FEI, 1)}")

    # —— when 解析 / 非法值 ——
    eq("normalize_entry_when 别名：now→immediate、等待→wait、满心情→full",
       [normalize_entry_when(x) for x in ("now", "等待", "满心情")],
       ["immediate", "wait", "full"])
    eq("normalize_entry_when 认不出 ⇒ None", normalize_entry_when("随便"), None)
    eq("ENTRY_WHEN_MODES 三档", tuple(ENTRY_WHEN_MODES), ("immediate", "wait", "full"))
    bad = raises(build_entry_event_config, {"when": "乱写"})
    check("build_entry_event_config：非法 when ⇒ ValueError（不静默取默认）",
          bad.startswith("ValueError"), f"实际 {bad!r}")
    bad = raises(build_entry_event_config, {"scope": "乱写"})
    check("build_entry_event_config：非法 scope ⇒ ValueError", bad.startswith("ValueError"),
          f"实际 {bad!r}")
    cfg = build_entry_event_config({"force": True})
    eq("旧字段 force=true ⇒ when=wait（向后兼容）", cfg.when, "wait")
    cfg = build_entry_event_config({"force": False})
    eq("旧字段 force=false ⇒ when=full", cfg.when, "full")
    eq("entry_events 收 `true`/`false`/人名/None 四种宽松写法",
       [build_entry_event_config(x).enabled for x in (True, False, DRAGON, None)],
       [True, False, True, None])

    # —— 逐班覆盖：列表读 key / 字典按序号或班次名 / 只关那一班 ——
    outs = build_entry_shift_overrides([{"key": 2, "enabled": True, "swap_with": "甲"},
                                        {"key": 3, "enabled": False}])
    eq("per_shift 列表写法：写了 key 就按 key（不是按位置）",
       [(o.key, o.enabled) for o in outs], [(2, True), (3, False)])
    outs = build_entry_shift_overrides([{"enabled": True}, {"enabled": False}])
    eq("per_shift 列表写法：没写 key 才按位置编号（向后兼容）",
       [(o.key, o.enabled) for o in outs], [(1, True), (2, False)])
    outs = build_entry_shift_overrides({"1": {"enabled": False},
                                        "Shift 2 · 12h": {"swap_with": WULIAN}})
    eq("per_shift 字典写法：按序号 / 按班次名都能定位",
       [(o.key, o.enabled, o.swap_with) for o in outs],
       [("1", False, None), ("Shift 2 · 12h", None, WULIAN)])
    base_cfg = build_entry_event_config({"enabled": True, "swap_with": PAO})
    got = resolve_entry_config(base_cfg, 0, "A",
                               [build_entry_shift_overrides([{"enabled": False}])[0]])
    eq("resolve_entry_config：逐班覆盖 enabled=false ⇒ 只关这一班（其余继承全局）",
       (got.enabled, got.swap_with), (False, PAO))
    got = resolve_entry_config(base_cfg, 1, "B",
                               [build_entry_shift_overrides([{"enabled": False}])[0]])
    eq("resolve_entry_config：不命中的班次照旧继承全局", (got.enabled, got.swap_with),
       (True, PAO))

    # —— 逐班覆盖真的影响引擎（只关第 2 班） ——
    ov = build_entry_shift_overrides([{"key": 2, "enabled": False}])
    sch = Schedule([shift("班1", 12, copy.deepcopy(ENTRY_FACS)),
                    shift("班2", 12, copy.deepcopy(ENTRY_FACS))], D(24))
    traj = simulate_schedule(sch, cycles=1, entry_events=True, entry_swap_with=DRAGON,
                             entry_scope="anywhere", entry_when="immediate",
                             entry_per_shift=ov, idle_to_dorm=False)
    times = sorted({m.t for m in entry_marks(traj) if "互换" in m.label})
    eq("逐班覆盖 `enabled:false` 只关第 2 班（第 1 班 t=0 换、第 2 班 t=12 不换）",
       times, [ZERO])

    # —— restore_back：顶层被界面口径覆盖，逐班写法却能生效（口径不一致，钉住） ——
    tr = simulate_schedule(Schedule([shift("班1", 12, copy.deepcopy(ENTRY_FACS))], D(12)),
                           cycles=1, entry_events=True, entry_swap_with=DRAGON,
                           entry_scope="anywhere", entry_when="immediate",
                           entry_restore_back=False, idle_to_dorm=False)
    check("引擎层 entry_restore_back=False ⇒ 位置也一起互换（说明里写明）",
          any("位置也对调" in m.label for m in entry_marks(tr)), f"实际 {entry_labels(tr)}")
    d = make_tmpdir("rb")
    p = d / "rb.json"
    write_json(p, {"entry_events": {"enabled": True, "swap_with": DRAGON, "scope": "anywhere",
                                    "when": "immediate", "restore_back": False},
                   "facilities": copy.deepcopy(ENTRY_FACS)})
    for tag, apply_file in (("load_paths", None), ("load_data", False), ("load_data+file", True)):
        s = Session()
        if tag == "load_paths":
            s.load_paths([p])
        else:
            s.load_data(json.loads(p.read_text(encoding="utf-8")), apply_file_settings=apply_file)
        eq(f"文件顶层 restore_back=false 被界面口径覆盖为 True（{tag}）",
           s.entry_restore_back, True)
    p2 = d / "rb2.json"
    write_json(p2, {"entry_events": {"enabled": True, "swap_with": DRAGON, "scope": "anywhere",
                                     "when": "immediate",
                                     "per_shift": [{"key": 1, "restore_back": False}]},
                    "facilities": copy.deepcopy(ENTRY_FACS)})
    s = Session()
    s.load_paths([p2])
    eq("同一个字段走**逐班**写法却能生效（顶层被吞、逐班生效 ⇒ 两条路口径不一致）",
       (s.entry_restore_back, s.schedule.entry_config_for_shift(0, s.entry_per_shift).restore_back),
       (True, False))

    # —— entry_candidates：同宿舍室友排前面 ——
    s = sess_from_data({"facilities": copy.deepcopy(ENTRY_FACS)}, idle_to_dorm=False)
    holders, mates = s.entry_candidates()
    eq("entry_candidates：触发者＝持有 M15a 的人（菲亚梅塔）", holders, [FEI])
    eq("entry_candidates：同宿舍室友排在其他干员前面", mates[0], MUR)
    check("entry_candidates：可换对象含基建其他人（龙舌兰）", DRAGON in mates, f"实际 {mates}")
    eq("entry_event_holders：任意设施都算（她不在宿舍也算）",
       [n for n, _r in entry_event_holders(build_base_layout({"facilities": [
           {"type": "贸易站", "level": 3, "operators": [{"name": FEI, "mood": "24"}]}]}))],
       [FEI])

    # —— 红脸 ⇒ 技能失效（总口径） ——
    tr = _entry_run(fei_mood="0")
    check("她红脸（0）⇒ M15a 不触发（红脸技能失效的总口径）",
          not entry_labels(tr), f"实际 {entry_labels(tr)}")


# ============================================================================
# §5 闲置入宿（开关 / 黑名单 / 已撤的 per_operator / 锁定位置数 / 两相 / 每个执行点）
# ============================================================================
IDLE_BASE = {"facilities": [
    {"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": [{"name": "甲", "mood": "24"}]},
    {"type": "贸易站", "level": 3, "name": "贸易站#1", "operators": [{"name": "戊", "mood": "24"}]}],
    # 候选池：**不在任何设施里**、但在排班名册里（`detached` ⇒ `operator_names()` 含他们）。
    # ⚠️ 心情默认 24 ⇒ 不是候选，所以下面统一把他们的起点心情压到 10。
    "detached": ["乙", "丙", "丁"]}
IDLE_CANDS = ("乙", "丙", "丁")


def _idle_opts(s: Session, cands=IDLE_CANDS, mood=10) -> Session:
    """把候选池的起点心情压到 24 以下（否则他们"不需要恢复"、压根不进候选）。"""
    s.idle_protected_slots = 0
    s.set_initial_moods({n: mood for n in cands})
    s.recompute()
    return s


def _idle_session(**kw):
    return _idle_opts(sess_from_data(copy.deepcopy(IDLE_BASE), **kw))


def _idle_scene(facs, cands=IDLE_CANDS, mood=10) -> Session:
    """给一组设施 + 一个候选池（`detached`）建会话（闲置入宿用例的统一夹具）。"""
    return _idle_opts(sess_from_data({"facilities": copy.deepcopy(facs),
                                      "detached": list(cands)}), cands, mood)


def test_section5_idle():
    print("\n=== §5 闲置入宿（开关 / 黑名单 / 已撤的 per_operator / 锁定位置数 / 两相） ===")

    # —— 开关：默认开、显式 false 才关 ——
    s = sess_from_data(copy.deepcopy(IDLE_BASE))
    eq("文件里没写 idle_to_dorm ⇒ **默认开**（全项目统一口径）", s.idle_to_dorm, True)
    s2 = sess_from_file({**copy.deepcopy(IDLE_BASE), "idle_to_dorm": False}, "idle_false")
    eq("**按文件**导入：文件显式 idle_to_dorm: false ⇒ 关", s2.idle_to_dorm, False)
    s3 = sess_from_file({**copy.deepcopy(IDLE_BASE), "idle_to_dorm": {"enabled": False}},
                        "idle_false_obj")
    eq("**按文件**导入：文件显式 {enabled: false} ⇒ 关", s3.idle_to_dorm, False)
    s4 = sess_from_data(copy.deepcopy(IDLE_BASE), idle_to_dorm=False)
    eq("内联入口显式 idle_to_dorm=False ⇒ 关（参数优先）", s4.idle_to_dorm, False)
    s5 = _idle_session()
    handle(s5, "set_idle_to_dorm", {"enabled": False})
    s5.recompute()
    eq("API 关掉开关 ⇒ 引擎一个 idle 事件都不记", idle_labels(s5), [])
    handle(s5, "set_idle_to_dorm", {"enabled": True})
    s5.recompute()
    check("API 打开开关 ⇒ 引擎记 idle 事件", bool(idle_labels(s5)),
          f"实际 {idle_labels(s5)}")

    # —— 黑名单：不能**通过闲置入宿进宿舍**，但可被换出 ——
    s = _idle_session()
    s.idle_blacklist = ["乙"]
    s.recompute()
    eq("黑名单：不通过闲置入宿进宿舍（乙不在宿舍）",
       "乙" in layout_names(s, 0)["宿舍#1"], False)
    eq("黑名单不连坐：别的候选照旧进（丙进宿舍）",
       "丙" in layout_names(s, 0)["宿舍#1"], True)
    ids = [r[0] for g in s.idle_groups() for r in g[2]]
    eq("黑名单：候选表里也不出现（连行都没有）", "乙" in ids, False)
    eq("候选表里仍然列出别的候选", "丙" in ids, True)
    s = _idle_session()
    eq("不拉黑 ⇒ 乙 就进宿舍（相 1 填空床）",
       "乙" in layout_names(s, 0)["宿舍#1"], True)

    # —— ⚠️ 2026-10 已删一整段：逐人「参不参与」的优先级判据
    #    （`IdleToDormConfig.entry_for` / `IdleToDormEntry.specificity`）——
    #    原来这里钉「周期+班次 > 只写一个 > 全局、同分后写的赢、没写过 ⇒ None」，
    #    对象是 `IdleToDormConfig.entry_for` 与 `IdleToDormEntry`。**两者随功能整条撤销**
    #    （不是判据变了、也不是断言放宽，是**对象消失**：引擎里已经没有"参与"这个概念）。
    #    撤除后的口径改钉在下面「宽松解析」里那两条（旧键读得进来、不落任何字段）。

    # —— 宽松解析 ——
    c = build_idle_to_dorm_config(True)
    eq("宽松写法 `true` ⇒ 开、其余默认", (c.enabled, c.protected_slots, c.blacklist),
       (True, 5, []))
    c = build_idle_to_dorm_config({"enabled": False, "protectedSlots": 2,
                                   "blackList": ["甲", " 乙 "]})
    eq("别名 protectedSlots / blackList 都认，并 strip 空白",
       (c.enabled, c.protected_slots, c.blacklist), (False, 2, ["甲", "乙"]))
    c = build_idle_to_dorm_config({"blacklist": "甲，乙, 丙"})
    eq("黑名单也收逗号/中文逗号分隔的字符串（拆开 strip）", c.blacklist, ["甲", "乙", "丙"])
    # ⚠️ 2026-10 **改断言**：原来是「`per_operator` 字典写法 ⇒ 只记布尔」
    #    （`sorted((e.name, e.enabled) for e in c.per_operator)`）与「旧写法（手动点名/指定位置）
    #    读得进来但**只剩参与**」。**为什么变**：`per_operator` / `IdleToDormEntry` 已整条撤销，
    #    `c.per_operator` 不存在了。新断言钉**撤除口径**：两种老写法都**读得进来、不报错、
    #    不落任何字段**（`hasattr` 为假），且同一份 dict 里的其余字段照旧解析。
    c = build_idle_to_dorm_config({"protected_slots": 4,
                                   "per_operator": {"甲": False, "乙": True}})
    eq("已撤的 `per_operator`（字典写法）：不报错、不落任何字段、其余字段照旧解析",
       (c.protected_slots, hasattr(c, "per_operator"), c.blacklist), (4, False, []))
    c = build_idle_to_dorm_config({"protected_slots": 4,
                                   "per_operator": [{"name": "甲", "swap_with": "乙",
                                                     "dorm": 2, "slot": 1}]})
    eq("已撤的 `per_operator`（数组 + 旧的手动点名/指定位置写法）：同样只被忽略",
       (c.protected_slots, hasattr(c, "per_operator")), (4, False))
    bad = raises(build_idle_to_dorm_config, {"protected_slots": -1})
    check("protected_slots 为负 ⇒ ValueError（配置层就拒绝负数）",
          bad.startswith("ValueError"), f"实际 {bad!r}")
    s = _idle_session()
    handle(s, "set_idle_to_dorm", {"protected_slots": -5})
    eq("但 API 层对负数**夹到 0**（不报错）", s.idle_protected_slots, 0)

    # —— 锁定位置数：默认 5、竖向正序、钳位、空位照样能入住 ——
    eq("默认锁定位置数＝5", DEFAULT_PROTECTED_SLOTS, 5)
    eq("IdleToDormConfig 默认 protected_slots＝5", build_idle_to_dorm_config(None).protected_slots,
       5)

    def two_dorms(protected):
        s = _idle_scene([
            {"type": "宿舍", "level": 5, "name": "宿舍#1", "capacity": 1,
             "operators": [{"name": "甲", "mood": "9"}]},
            {"type": "宿舍", "level": 5, "name": "宿舍#2", "capacity": 1,
             "operators": [{"name": "乙", "mood": "24"}]}],
            cands=("丙",), mood=10)
        s.idle_protected_slots = protected
        s.recompute()
        return s

    s = two_dorms(2)
    eq("锁定区＝竖向正序前 2 个（宿1位1、宿2位1）：全满且都在锁定区 ⇒ 谁都换不动",
       [layout_names(s, 0)["宿舍#1"], layout_names(s, 0)["宿舍#2"]], [["甲"], ["乙"]])
    check("锁定区里的人不被换出 ⇒ 记一条「没有合适的可换对象」",
          any("没有合适的可换对象" in s for s in idle_labels(s)), f"实际 {idle_labels(s)}")
    s = two_dorms(0)
    eq("锁定位置数＝0 ⇒ 换出「心情 ≥ 候选且最大」的住户（乙 24 被换出、甲 9 留下）",
       [layout_names(s, 0)["宿舍#1"], layout_names(s, 0)["宿舍#2"]], [["甲"], ["丙"]])
    s = two_dorms(99)
    eq("锁定位置数超过总位置数 ⇒ 钳到总数（效果与 2 相同）、不报错",
       (s.recompute() is not None, layout_names(s, 0)["宿舍#2"]), (True, ["乙"]))

    # —— 相 1：竖向正序填空床（含中间空洞） ——
    # ⚠️ 「竖向正序」＝**位次优先、宿舍序号其次**：宿1位2 排在 宿2位1 **之后**
    #    （位次 1 < 2）；候选之间按 (心情↑, 名字↑) —— 同心情时按名字的码位（丁 < 丙 < 乙）。
    s = _idle_scene([
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": [{"name": "甲", "mood": "24"}]},
        {"type": "宿舍", "level": 5, "name": "宿舍#2", "operators": []},
        {"type": "贸易站", "level": 3, "name": "贸易站#1", "operators": [{"name": "戊"}]}])
    eq("相 1：竖向正序填空床（位次优先 ⇒ 宿2位1 排在 宿1位2 之前；候选按名字序 丁→丙→乙）",
       [layout_names(s, 0)["宿舍#1"], layout_names(s, 0)["宿舍#2"]],
       [["甲", "丙"], ["丁", "乙"]])
    s = _idle_scene([
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "slots": ["甲", None, "己"]},
        {"type": "贸易站", "level": 3, "operators": [{"name": "戊"}]}])
    eq("相 1：中间空洞优先（`slots` 留的空洞照样能入住，名字序靠前的先进）",
       layout_names(s, 0)["宿舍#1"][1], "丁")

    # —— 相 2：宿舍全满 ⇒ 换出「锁定区外、心情 ≥ 她、且心情最大」 ——
    s = _idle_scene([
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "capacity": 2,
         "operators": [{"name": "甲", "mood": "23"}, {"name": "乙", "mood": "24"}]},
        {"type": "贸易站", "level": 3, "operators": [{"name": "戊"}]}],
        cands=("丙",), mood=10)
    got = layout_names(s, 0)["宿舍#1"]
    check("相 2：全满时换出「心情 ≥ 候选且最大」的住户（乙 24 出去、甲 23 留下）",
          "乙" not in got and "丙" in got and "甲" in got, f"实际 {got}")

    # —— ⚠️ 2026-10 已删一整段：逐人设置（`idle_globals` / `idle_entries`）真的生效 ——
    #    原来这里钉：`idle_globals = {"乙": False}` ⇒ 乙不进宿舍；`idle_entries` 按
    #    `(周期, 班次, 干员)` 生效、只对指定周期生效；以及"相 2 也要查"那三组
    #    （`full_dorm_session` / `_swap_phase_scene`，含 `86a311f 修相2漏查逐人参与设置`
    #    的精确形状）。**对象随功能整条撤销**：`Session.idle_globals` / `.idle_entries`
    #    字段已删、引擎里两道 `entry_for` 判据已删 ⇒ 这些断言的**主语不存在了**，
    #    不是"结论变了"。要挡人改用黑名单（上面「黑名单」那一段仍在，钉的是同一条口径
    #    "不能进宿舍但可被换出"）。撤除口径另钉在：本脚本的 `set_idle_to_dorm` 探针、
    #    `scripts/verify_idle.py` 的 A5 与 `tests/test_settings_blackbox.py`。

    # —— 每个执行点都跑：24h 班两个执行点都在同一个 `(周期, 班次)` 作用域下 ——
    # 造一个"候选永远进不去"的场景（唯一床位在锁定区 ⇒ 乙 一路是候选）：
    # 这样两个执行点都会出现在逐次表里，且 `scope` 都是同一个 `(1, 1)`。
    # ⚠️ 2026-10 措辞改：原来是"共享同一份**逐人设置**"，现在没有逐人设置了 ——
    #    `scope` 仍在（它是"第几周期第几班"的标签）。
    s = _idle_scene([
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "capacity": 1,
         "operators": [{"name": "甲", "mood": "24"}]},
        {"type": "贸易站", "level": 3, "operators": [{"name": "戊"}]}],
        cands=("乙",), mood=10)
    s.idle_protected_slots = 5                     # 锁定唯一床位 ⇒ 乙 进不去、一直是候选
    s.set_timeline(hours=[24])
    s.recompute()
    groups = s.idle_groups()
    eq("24h 班 ⇒ 逐次表按**换班执行点**分组（班初 + 12h 内部换班）", len(groups), 2)
    eq("同班各执行点共用同一个 `scope`（都是 (1,1)）",
       sorted({tuple(g[1]) for g in groups}), [(1, 1)])
    eq("内部换班那一组的标题写明「12h 内部换班」", "内部换班" in groups[1][0], True)
    # ⚠️ 2026-10 改断言：原来是「`idle_count` ＝各行**参与**数之和（第 4 格为真的行）」——
    #    第 4 格（原「参不参与」）现在**恒为 `None`**，所以"按参与数筛"已经筛不出任何人。
    #    新口径＝"每一行候选都参与" ⇒ 等于各行候选数之和（黑名单的人根本不在表里）。
    eq("idle_count＝候选行数之和（第 4 格已废恒 None ⇒ 每一位候选都参与）",
       s.idle_count(), sum(len(g[2]) for g in groups))
    eq("逐次表第 4 格恒为 None（对外形状仍是 6 元组，那一格不再有第二种取值）",
       sorted({r[3] for g in groups for r in g[2]}), [None])

    # —— ⚠️ 2026-10 已删：`idle_entry_list()`（只列改过默认的 False；没改过 ⇒ None）——
    #    方法随 `idle_globals` / `idle_entries` 一起删除。

    # —— 与引擎同一份候选口径（表里给的候选＝引擎真的处理的） ——
    s = _idle_session()
    s.recompute()
    rows = [r[0] for g in s.idle_groups() for r in g[2]]
    owners = [m.label.split(" ")[0].lstrip("（") for m in s.traj.marks
              if m.kind == "idle" and "进" in m.label]
    check("逐次表的候选与引擎实际处理的人一致（超集：表里列出的都至少被评估过）",
          set(owners) <= set(rows) | {""}, f"表 {rows} / 引擎 {owners}")


# ============================================================================
# §6 导入不变量（两个导入入口逐字段比对 / 文件级设置真的生效）
# ============================================================================
def test_section6_import():
    print("\n=== §6 导入不变量（按文件 vs 内联 / 文件级设置真的生效） ===")

    # —— 同一份 JSON 两条入口：逐字段自动比对 ——
    for path in SAMPLES:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        by_file = Session()
        by_file.load_paths([path])
        by_json = Session()
        by_json.load_data(copy.deepcopy(data))
        by_json_file = Session()
        by_json_file.load_data(copy.deepcopy(data), apply_file_settings=True)

        eq(f"{path.name}：按文件载入不报错且班次拼得起来", by_file.schedule is not None, True)

        # ① 布局（含 manual 台账）逐班次逐位次相同
        eq(f"{path.name}：两条入口的**布局快照**逐位相同",
           facilities_json(by_file), facilities_json(by_json))
        # ② 数值（轨迹）逐位相同
        eq(f"{path.name}：两条入口的**心情轨迹**逐位相同",
           {n: [str(v) for v in by_file.traj.moods[n]] for n in by_file.traj.names},
           {n: [str(v) for v in by_json.traj.moods[n]] for n in by_json.traj.names})

        # ③ 设置逐字段比对：默认口径下**只允许 entry_events 这一项**不同（文档口径）
        diff = diff_settings(settings_snapshot(by_file), settings_snapshot(by_json))
        eq(f"{path.name}：默认口径下差异字段恰是 ['entry_events']（＝不继承文件开关）",
           diff, ["entry_events"])
        # ④ 显式按文件设置 ⇒ **一个字段都不许不同**
        diff2 = diff_settings(settings_snapshot(by_file), settings_snapshot(by_json_file))
        eq(f"{path.name}：apply_file_settings=True ⇒ 设置逐字段一致（差异＝[]）", diff2, [])

    # —— 文件级 idle_to_dorm 真的生效（**导入读闲置入宿**） ——
    # ⚠️ 2026-10：夹具里的 `per_operator` 已删（逐人「参不参与」随功能整条撤销）；
    #    下面三处的四元组随之变成三元组 —— **不是放宽**，是那一项不存在了。
    d = make_tmpdir("import")
    p = d / "idle.json"
    write_json(p, {"idle_to_dorm": {"enabled": False, "protected_slots": 2,
                                    "blacklist": ["甲"]},
                   "facilities": [{"type": "宿舍", "level": 5, "name": "宿舍#1",
                                   "operators": ["甲", "乙"]},
                                  {"type": "贸易站", "level": 3, "operators": ["丙"]}]})
    s = Session()
    s.load_paths([p])
    eq("文件顶层 idle_to_dorm 真的被读进会话（enabled/锁定位置/黑名单）",
       (s.idle_to_dorm, s.idle_protected_slots, s.idle_blacklist),
       (False, 2, ["甲"]))
    s = Session()
    s.load_data(json.loads(p.read_text(encoding="utf-8")), apply_file_settings=True)
    # ⚠️ 2026-10 修（A4 工单）：原来是**半继承**（开关被拍回 True、其余三项却继承）。
    #    现在整组继承 ⇒ 与 `load_paths` 和 `op_load_json` 的文档口径一致。
    eq("load_data(apply_file_settings=True) ⇒ 文件级 idle_to_dorm **整组**继承"
       "（enabled 也照文件，不再是半继承）",
       (s.idle_to_dorm, s.idle_protected_slots, s.idle_blacklist),
       (False, 2, ["甲"]))
    s = Session()
    s.load_data(json.loads(p.read_text(encoding="utf-8")), apply_file_settings=True,
                idle_to_dorm=False)
    eq("显式传 idle_to_dorm=False ⇒ 以参数为准（这条口径本身就是「显式优先」）",
       s.idle_to_dorm, False)
    s = Session()
    s.load_data(json.loads(p.read_text(encoding="utf-8")), apply_file_settings=True,
                idle_to_dorm=True)
    eq("显式传 idle_to_dorm=True ⇒ **参数压过文件**里的 false（优先序：参数 > 文件 > 默认开）",
       s.idle_to_dorm, True)
    s = Session()
    s.load_data(json.loads(p.read_text(encoding="utf-8")))          # 默认口径（不继承文件）
    eq("默认（不继承文件）⇒ 开关回到本入口默认**开**，另外两项也回项目默认",
       (s.idle_to_dorm, s.idle_protected_slots, s.idle_blacklist),
       (True, 5, []))

    # —— 三个内联入口的口径差异（自动比对，钉住现状） ——
    layout = {"facilities": [{"type": "宿舍", "level": 5, "name": "宿舍#1",
                              "operators": ["甲"]}],
              "entry_events": {"enabled": True, "swap_with": "丙", "when": "wait"}}
    a = Session()
    a.load_layout(copy.deepcopy(layout))
    b = Session()
    b.load_data(copy.deepcopy(layout))
    diff = diff_settings(settings_snapshot(a), settings_snapshot(b))
    eq("同一份 dict：load_layout **继承**文件设置、load_data 默认不继承（差异＝entry_events）",
       diff, ["entry_events"])
    eq("load_data 默认把入口设置整组打回本入口默认（enabled=False / when=full）",
       (b.entry_events, b.entry_when), (False, "full"))

    # 闲置入宿那一组：`load_layout`（＝本工具场景，与 load_paths 同义）按文件走
    a = Session()
    a.load_layout({"facilities": [{"type": "宿舍", "level": 5, "name": "宿舍#1",
                                   "operators": ["甲"]}],
                   "idle_to_dorm": {"enabled": True, "protected_slots": 3}})
    eq("load_layout 的闲置入宿按文件走（锁定位置 3、开关照文件）",
       (a.idle_to_dorm, a.idle_protected_slots), (True, 3))
    c = Session()
    c.load_layout({"facilities": [{"type": "宿舍", "level": 5, "name": "宿舍#1",
                                   "operators": ["甲"]}],
                   "idle_to_dorm": {"protected_slots": 3}})
    # ⚠️ 2026-10 修（A5 工单）：原来是 `bool(getattr(idle, "enabled", True))` ——
    #    `getattr` 的缺省值救不了"**属性存在但值是 `None`**"（`bool(None) == False`）。
    #    而 `models.build_idle_to_dorm_config` 对"只写了 protected_slots 的对象"留的
    #    就是 `enabled=None`（"未指定"）⇒ 会话判**关**、引擎把 `None` 当**开**，
    #    同一份 world 两套结论。全项目口径＝"没写这个键 ⇒ 结算；显式 false 才关"。
    eq("文件写 `idle_to_dorm` 对象却**不写 `enabled`** ⇒ 开关照全项目口径判 **开**"
       "（与 `IdleToDormConfig.enabled=True`、`apply_idle_to_dorm` 把 None 当开一致）",
       (c.idle_to_dorm, c.idle_protected_slots), (True, 3))
    c2 = Session()
    c2.load_layout({"facilities": [{"type": "宿舍", "level": 5, "name": "宿舍#1",
                                    "operators": ["甲"]}],
                    "idle_to_dorm": {"enabled": False, "protected_slots": 3}})
    eq("显式 `enabled: false` 仍然关（默认开不是「一律开」）",
       (c2.idle_to_dorm, c2.idle_protected_slots), (False, 3))

    # —— 名单优先（导入时"detached 与占位冲突"⇒ 摘人 + 留洞不左移 + 解该位锁） ——
    d2 = make_tmpdir("detach")
    p2 = d2 / "both.json"
    write_json(p2, {"detached": ["乙"],
                    "facilities": [{"type": "宿舍", "level": 5, "name": "宿舍#1",
                                    "operators": ["甲", "乙", "丙"],
                                    "manual": {"slots": [1], "names": ["乙"]}}]})
    s = Session()
    ld = s.load_paths([p2])
    vals = seat_values(fac_of(s))
    eq("导入冲突：**名单优先** ⇒ 从位次上摘掉（留洞不左移）",
       (vals[0], vals[1], vals[2]), ("甲", "", "丙"))
    eq("导入冲突：该位次的锁一并解除（不留「空着却没人住」的锁）",
       manual_of(fac_of(s)), {})
    check("导入冲突：记一条进导入摘要（不静默）",
          any("不在基建名单优先" in n for n in ld.notes), f"实际 {ld.notes}")


# ============================================================================
# §7 导出 → 再导入往返（哪些保持 / 哪些丢 / 已知例外钉住）
# ============================================================================
#: 往返用例的目录序号（每次换一个目录 ⇒ 文件名能**恰好**等于班次名）
_RT_SEQ = [0]


def _roundtrip(s: Session, cycles=None) -> Session:
    """按用户会走的路往返：`export_schedule` → 每班 `scenario` 写文件 → 再按文件载入。

    ⚠️ 导出是**每班一份场景**的信封（顶层 `{shifts, detached, start_clock, cycles}`），
    **这个信封本身还不能被导入**（`sources.detect_format` 不认 `shifts`，症状①未做），所以要走
    "拆成每班一份场景文件"这条路。

    ⚠️ **2026-10 工单 ②③ 之后这里不再手工走装配**：每班的**时长**与**班次名**现在都在
    `shifts[].scenario` 正文里（`hours` / `label`），而 `initial_moods` / `mood_events`
    与 `initial_global` 早就由导入层收进 `ImportResult` ⇒ 老夹具里那些
    "`load_schedule_ex(hours=…)` + 照 `Session._read_scenario_moods` 补读一遍"的绕法
    **全部删掉**，直接走用户真正走的那条 `Session.load_paths`。
    这是**更强**的断言：从信封补时长就等于绕过了"正文自带时长"这件事本身。

    只剩两句是真的"信封级别、还没进正文"的：`start_clock` 与 `cycles`（症状①未做）。
    """
    out = op_export_schedule(s, {})
    _RT_SEQ[0] += 1
    d = make_tmpdir(f"roundtrip{_RT_SEQ[0]}")        # 每次换目录：文件名要**恰好**是班次名
    paths = []
    for i, sh in enumerate(out["shifts"]):
        # 用户会按班次名存盘（scenario 格式的班次名＝文件名，见 sources._import_scenario）
        stem = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in str(sh["label"]))
        p = d / f"{stem or ('shift' + str(i))}.json"
        write_json(p, sh["scenario"])
        paths.append(p)
    s2 = Session()
    s2.load_paths(paths)
    s2.set_start_clock(to_decimal(out["start_clock"]))
    s2.set_cycles(int(out["cycles"]) if cycles is None else int(cycles))
    s2.recompute()
    return s2


def _legacy_scenario_unchanged() -> bool:
    """**向后兼容**：正文里**没有** `label` / `hours` 的老场景文件，行为一字不变。

    班次名仍取**文件名**、时长仍按 `store.schedule._hours_from_hints` 兜底（单班 ⇒ 24h）。
    """
    d = make_tmpdir("legacy_scen")
    p = d / "legacy_scene.json"
    write_json(p, {"facilities": [{"type": "贸易站", "level": 3, "operators": ["龙舌兰"]}]})
    s = Session()
    s.load_paths([p])
    return (s.schedule.shift_labels() == ["legacy_scene"]
            and [str(x.hours) for x in s.schedule.shifts] == ["24"])


def _initial_global_all_entries() -> bool:
    """**工单 ⑲**：`initial_global` 走三条导入入口都要逐位读得进来。

    修前只有 `load_layout` 读得进来 —— `_import_scenario` 不收它、`load_paths` / `load_data`
    都静默丢（`{}`）。
    """
    scen = {"facilities": [{"type": "贸易站", "level": 3, "operators": ["龙舌兰"]}],
            "initial_global": {"木天蓼": 5, "人间烟火": "30.5"}}
    d = make_tmpdir("initial_global")
    p = d / "ig.json"
    write_json(p, scen)
    want = {"木天蓼": "5", "人间烟火": "30.5"}
    for load in (lambda t: t.load_layout(copy.deepcopy(scen)),
                 lambda t: t.load_data(copy.deepcopy(scen)),
                 lambda t: t.load_paths([p])):
        t = Session()
        load(t)
        if {k: str(v) for k, v in t.shifts()[0].initial_global.items()} != want:
            return False
    return True


def test_section7_roundtrip():
    print("\n=== §7 导出 → 再导入往返 ===")

    s = Session()
    s.load_paths([MAA_SAMPLE])
    s.set_cycles(3)
    s.entry_events = True
    s.entry_swap_with = DRAGON
    s.entry_scope = "anywhere"
    s.entry_when = "wait"
    s.idle_to_dorm = True
    s.idle_protected_slots = 3
    s.idle_blacklist = [PAO]
    # ⚠️ 2026-10：原来这里还有 `s.idle_globals = {FEI: False}`（逐人「参不参与」）——
    #    字段随功能整条撤销。这一段的"设置面全覆盖"少一项是**对象消失**，不是漏设。
    s.set_initial_mood(PAO, 11)
    s.set_mood_at(FEI, 5, 6)
    s.set_detached(["路人甲"])
    s.set_start_clock(1)
    dorm0 = next(i for i, f in enumerate(s.facilities_of(0)) if f.get("type") == "宿舍")
    s.place_operator(0, dorm0, 2, PAO)               # 摆一个人 ⇒ 导出里要带 `manual` 台账
    s.recompute()
    before = settings_snapshot(s)
    out = op_export_schedule(s, {})

    eq("导出顶层键恰是四个（兼容性红线）", sorted(out),
       ["cycles", "detached", "shifts", "start_clock"])
    eq("每班条目恰是 label / hours / scenario 三个键",
       sorted(out["shifts"][0]), ["hours", "label", "scenario"])
    check("每班 `scenario` 里带 facilities / detached（顶层 detached 非空时）",
          all("facilities" in sh["scenario"] for sh in out["shifts"])
          and all("detached" in sh["scenario"] for sh in out["shifts"]))
    manual_keys = set()
    for sh in out["shifts"]:
        for f in sh["scenario"]["facilities"]:
            if f.get("manual"):
                manual_keys |= set(f["manual"])
    eq("导出里的 `manual` 子键恰是 {slots, names}", manual_keys, {"slots", "names"})
    blob = json.dumps(out, ensure_ascii=False)
    # ⚠️ 裸 `"restore"` **不能**做子串判据：`entry_events` 是本工具场景本就支持的顶层键，
    #    它的子键就叫 `restore_back`（2026-10 起导出会写它）⇒ 裸子串会误报。
    #    要钉的是"原位 / 恢复默认（session-only 的那两个能力）不进导出"，用真正的标识符。
    check("导出里没有「导入原样 / 恢复默认」的痕迹"
          "（imported / origin / restore_seat / restore_default / imported_seat）",
          not [w for w in ("imported", "origin", "restore_seat", "restore_default",
                           "imported_seat") if w in blob])

    # ⚠ 往返的**已知缺口**：只剩症状①（顶层信封本身不能被导入）
    d = make_tmpdir("roundtrip")
    whole = d / "whole.json"
    write_json(whole, out)
    bad = raises(Session().load_paths, [whole])
    known("⚠ 导出的**顶层信封**不能再被导入（`{shifts, detached, start_clock, cycles}` "
          "不被格式识别）⇒ 必须自己拆成每班一份场景（症状①，**未做**：要动 "
          "`sources.detect_format` 那层格式识别契约，用户明确选了「不动识别层」）",
          bad.startswith("ValueError") and "无法识别的 JSON" in bad,
          f"实际 {bad!r}；报告 §2 第 5 条（症状①）", issue="已知缺口5")
    # ✅ 症状②③（班次时长 / 班次名）**已修**（2026-10）：`scenario` 正文里现在带
    #    `hours` 与 `label`（`api.ops.op_export_schedule` 写、`sources._import_scenario` 读）
    #    ⇒ 拆成每班一份场景之后，**不带 `hours=`、文件名也无关**就能逐班还原。
    check("每班 `scenario` 正文里带**班次时长**（`hours`）与**班次名**（`label`）",
          all("hours" in sh["scenario"] and "label" in sh["scenario"] for sh in out["shifts"]),
          f"实际键={sorted(out['shifts'][0]['scenario'])}")
    d2 = make_tmpdir("roundtrip_nohours")
    one = d2 / "renamed_not_the_label.json"          # 文件名**故意**不等于班次名
    write_json(one, out["shifts"][0]["scenario"])
    s_nohours = Session()
    s_nohours.load_paths([one])
    eq("② 不带 `hours=` 再导入（文件名也无关）⇒ 时长仍＝导出里那份（修前均分成 24h）",
       str(s_nohours.schedule.shifts[0].hours), str(out["shifts"][0]["hours"]))
    eq("③ 班次名来自正文（修前由文件名决定，这里文件名是 renamed_not_the_label）",
       s_nohours.schedule.shift_labels(), [out["shifts"][0]["label"]])
    check("②③ 老场景文件（正文里没有这两个键）行为一字不变：名字取文件名、时长仍均分 24h",
          _legacy_scenario_unchanged())
    # ✅ 症状④（设置全丢）**已修**（2026-10）：这四组现在都在 `shifts[].scenario` 里，
    #    并有专门的回归（`tests/test_export_roundtrip.py`）。
    check("每班 `scenario` 里带全四组设置（entry_events / idle_to_dorm / "
          "initial_moods / mood_events）",
          all({"entry_events", "idle_to_dorm", "initial_moods", "mood_events"}
              <= set(sh["scenario"]) for sh in out["shifts"]))
    # ✅ 症状⑲（变量初始值只有 load_layout 读得进来）**已修**（2026-10）：三条入口同源。
    check("⑲ `initial_global` 三条入口（load_layout / load_data / load_paths）逐位相同",
          _initial_global_all_entries())

    s2 = _roundtrip(s)
    eq("往返：布局（含位次空洞与 manual 台账）逐班次逐位次不变",
       facilities_json(s), facilities_json(s2))
    eq("往返：`detached` 不变", s2.detached, s.detached)
    # ⚠️ 2026-10 工单 ②③ 之后这条**变成了真断言**：不再靠 `set_timeline(hours=…)` 从信封补，
    #    时长与班次名都来自 `shifts[].scenario` 正文（`_roundtrip` 里那几行绕法已删）。
    eq("往返：每班**时长**不变（来自正文 `hours`，不是靠 `set_timeline`）",
       [str(x.hours) for x in s2.schedule.shifts],
       [str(x.hours) for x in s.schedule.shifts])
    eq("往返：每班**班次名**不变（来自正文 `label`，不是靠文件名）",
       s2.schedule.shift_labels(), s.schedule.shift_labels())
    eq("往返：start_clock / cycles 补上后一致",
       (str(s2.schedule.start_clock), s2.cycles), (str(s.schedule.start_clock), s.cycles))
    # 「排班快照」那部分要逐位相同 ⇒ 把两边的**设置**（含心情锚点）都清掉再比
    # （否则换心情/入宿/锚点会按各自的设置改变布局与节点数，比出来的是"设置差异"）。
    s_off, s2_off = copy.deepcopy(s), copy.deepcopy(s2)
    for t in (s_off, s2_off):
        t.entry_events = False
        t.idle_to_dorm = False
        t.restore_imported_moods()                   # 清起点心情 + 清锚点
        t.recompute()
    eq("往返：把设置对齐后，**排班本身**逐位相同（快照未被动过）",
       {n: [str(v) for v in s_off.traj.moods[n]] for n in s_off.traj.names},
       {n: [str(v) for v in s2_off.traj.moods[n]] for n in s2_off.traj.names})

    after = settings_snapshot(s2)
    diff = diff_settings(before, after)
    # ✅ 症状④（设置全丢）**已修**（2026-10）：四组设置现在都写进 `shifts[].scenario`、
    #    并由导入层读回 ⇒ 往返**逐字段相等**。改前这里钉的是
    #    `["entry_events", "idle_to_dorm", "initial_moods", "mood_events"]` 这个"会丢的集合"。
    eq("往返：**全部设置逐字段不变**（`entry_events` / `idle_to_dorm` / `initial_moods` / "
       "`mood_events` 都进了 `shifts[].scenario`）", diff, [])

    # —— 已知且有意：「原位」不进导出 ——
    s3 = Session()
    s3.load_paths([MAA_SAMPLE])
    fi = next(i for i, f in enumerate(s3.facilities_of(0)) if seat_values(f)[:1] == [FEI])
    eq("基线：导入时她本班坐在第 1 位", s3.imported_seat_occupant(0, fi, 0), FEI)
    s3.place_operator(0, fi, 1, FEI)                 # 挪到第 2 位（导出会带上这份布局）
    s4 = _roundtrip(s3, cycles=1)
    known("⚠ 往返后「恢复默认」回到的是**导出那一刻**的布局，不再是原始导入那份"
          "（原位是 session-only、一个字节都不进导出）—— **已知且有意**（用户明确要求）",
          s4.imported_seat_occupant(0, fi, 0) == "" and s4.imported_seat_occupant(0, fi, 1) == FEI,
          f"实际 第 1 位={s4.imported_seat_occupant(0, fi, 0)!r}、"
          f"第 2 位={s4.imported_seat_occupant(0, fi, 1)!r}（原始导入那份是 第 1 位=菲亚梅塔）；"
          f"见 documents/10-现状与校准记录（附版本记录）",
          issue="已知例外-原位")


# ============================================================================
# §8 「改完设置再触发各种动作，设置还在不在」（静默覆盖扫描）
# ============================================================================
def _set_everything(s: Session) -> None:
    """把设置面**每一组**都设成非默认值（sweep 的基准）。"""
    s.cycles = 3
    s.idle_to_dorm = True
    s.idle_protected_slots = 9
    s.idle_blacklist = ["丙"]
    # ⚠️ 2026-10：`idle_globals` / `idle_entries`（逐人「参不参与」）已删 —— 字段不存在，
    #    sweep 的"每一组都设成非默认值"里少这两项是**对象消失**。
    s.entry_events = True
    s.entry_swap_with = FEI
    s.entry_scope = "anywhere"
    s.entry_when = "immediate"
    s.entry_restore_back = False
    s.entry_per_shift = build_entry_shift_overrides([{"key": 2, "enabled": False}])
    s.set_initial_mood("甲", 10)
    s.set_mood_at("乙", 5, 6)
    s.recompute()


def test_section8_survive_actions():
    print("\n=== §8 改完设置再触发各种动作（设置还在不在） ===")

    ACTIONS = {
        "碰「不在基建」名单 set_detached": (lambda t: t.set_detached(["丁"]), {"detached"}),
        "改各班时长 set_timeline(hours)": (lambda t: t.set_timeline(hours=[12]), set()),
        "改房间等级 set_room_level": (lambda t: t.set_room_level(0, 0, 2), set()),
        "改周期数 set_cycles": (lambda t: t.set_cycles(4), {"cycles"}),
        "锁定入宿 place_operator": (lambda t: t.place_operator(0, 0, 2, "戊"), set()),
        "改整段占位 set_slots": (lambda t: t.set_slots(0, 0, ["己", "庚"]), set()),
        "改练度 set_training": (lambda t: t.set_training(elite=1, names=["甲"]), set()),
        "从池中填入 fill_from_pool": (lambda t: t.fill_from_pool(), set()),
        "导出 export_schedule": (lambda t: handle(t, "export_schedule", {}), set()),
        "原地重算 recompute": (lambda t: t.recompute(), set()),
    }

    base = {"facilities": [
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": ["甲", "乙"]},
        {"type": "贸易站", "level": 3, "name": "贸易站#1", "operators": ["丙"]}]}

    for label, (act, allowed) in ACTIONS.items():
        t = sess_from_data(copy.deepcopy(base))
        _set_everything(t)
        want = settings_snapshot(t)
        try:
            act(t)
        except Exception as exc:                     # noqa: BLE001
            check(f"{label} 不抛异常", False, f"{type(exc).__name__}: {exc}")
            continue
        diff = [k for k in diff_settings(want, settings_snapshot(t)) if k not in allowed]
        check(f"{label} 之后设置逐项不变（本次动作本就该改的 {sorted(allowed)} 不算）",
              diff == [], f"意外变化的字段 {diff}")

    # ⚠ 「快照 vs 会话」的分叉：**只有**走 `set_detached`（本来就要重建 Schedule 的那条路）
    #    才会把会话那两组自动化设置落回快照（`_push_automation_settings`）。
    #    其余动作都不落 ⇒ 快照长期是旧值。
    #    ✅ 2026-10 起这条**不再会导出旧值**：`op_export_schedule` 改成从**会话**读那两组设置
    #    （`api/ops.py::_behavior_settings`，会话本来就是引擎的唯一权威），
    #    而不是从快照读 —— 所以下面的"快照仍是旧值"照旧成立，但它只是"快照滞后"、
    #    不再是"导出会写错"。回归：`tests/test_export_roundtrip.py::test_导出读会话而不是读快照`。
    t = sess_from_data(copy.deepcopy(base))
    _set_everything(t)
    t.set_room_level(0, 0, 2)                        # 一条"重建 Schedule 但不 push"的路
    snap = t.schedule.shifts[0].world.entry_events
    known("⚠ 除 `set_detached` 外的动作**不把**会话的两组自动化设置落回快照"
          "（快照仍是文件里的旧值）；导出已改成读**会话**，所以只是快照滞后、不再影响导出",
          (bool(snap.enabled), int(t.schedule.shifts[0].world.idle_to_dorm.protected_slots))
          == (False, 5),
          f"实际 快照 entry.enabled={snap.enabled}、idle.protected_slots="
          f"{t.schedule.shifts[0].world.idle_to_dorm.protected_slots} "
          f"vs 会话 {t.entry_events}/{t.idle_protected_slots}；报告 §2 第 7 条"
          f"（`_push_automation_settings` 只在 `set_detached` 里调）",
          issue="已知缺口7")

    # 源码级：编辑路径不许再调 `_sync_from_schedule`（那是"导入时读一次"的入口）
    code = [ln.strip() for ln in inspect.getsource(Session.set_detached).splitlines()]
    code = [ln for ln in code if ln and not ln.lstrip().startswith("#")]
    check("set_detached 的**代码**里不再调 `_sync_from_schedule()`（碰名单只动名册）",
          not any(ln.startswith("self._sync_from_schedule") for ln in code),
          f"实际命中 {[ln for ln in code if '_sync_from_schedule' in ln]}")
    src = inspect.getsource(Session)
    eq("编辑路径里 `_sync_from_schedule` 只在三条 `load_*` 里各调一次（导入时那一次）",
       src.count("self._sync_from_schedule(from_import=True)"), 3)

    # 落回快照：`_push_automation_settings` 由"本来就要重建 Schedule"的路（set_detached）触发
    t = sess_from_data(copy.deepcopy(base))
    _set_everything(t)
    t.set_detached(["丁"])
    idle_snap = t.schedule.shifts[0].world.idle_to_dorm
    entry_snap = t.schedule.shifts[0].world.entry_events
    # ⚠️ 2026-10 改断言：原来元组里还有第四项
    #    `sorted((e.name, e.enabled) for e in idle_snap.per_operator)`（＝`[("乙", False), ("甲", False)]`）
    #    —— `per_operator` 已随功能整条撤销，快照上不再有这个字段，所以元组变三项。
    eq("碰名单（重建 Schedule）之后：快照里的闲置入宿设置＝会话那份",
       (bool(idle_snap.enabled), int(idle_snap.protected_slots), list(idle_snap.blacklist)),
       (True, 9, ["丙"]))
    eq("碰名单之后：快照上也不再挂已撤的 `per_operator`（字段不存在）",
       hasattr(idle_snap, "per_operator"), False)
    eq("碰名单之后：快照里的换心情设置＝会话那份",
       (bool(entry_snap.enabled), entry_snap.swap_with, entry_snap.scope, entry_snap.when),
       (True, FEI, "anywhere", "immediate"))

    # 反向：设置写进快照之后，四条 Schedule 重建路径都要**原样搬运**这两组全局设置
    for name, sch2 in (("with_hours", t.schedule.with_hours([12])),
                       ("replaced_shift", t.schedule.replaced_shift(
                           0, t.schedule.shifts[0].facilities)),
                       ("with_detached", t.schedule.with_detached(["丁"])),
                       ("with_start_clock", t.schedule.with_start_clock(2))):
        idle = sch2.shifts[0].world.idle_to_dorm
        entry = sch2.shifts[0].world.entry_events
        eq(f"重建路径 {name}：闲置入宿设置被原样搬运",
           (bool(idle.enabled), int(idle.protected_slots), list(idle.blacklist)),
           (True, 9, ["丙"]))
        eq(f"重建路径 {name}：换心情设置被原样搬运",
           (bool(entry.enabled), entry.swap_with, entry.scope, entry.when),
           (True, FEI, "anywhere", "immediate"))


# ============================================================================
# §9 被移动 / 移除 / 换位之后，设置是否仍然生效（菲亚梅塔那一类）
# ============================================================================
def _entry_scene(extra_shifts=(), detached=(), fei_mood="24"):
    """菲亚梅塔(24) 在宿舍 + 龙舌兰(2) 在制造站 的单班场景（可挂额外班次）。"""
    facs = [{"type": "宿舍", "level": 5, "name": "宿舍#1",
             "operators": [{"name": FEI, "mood": fei_mood}, {"name": MUR, "mood": "3"}]},
            {"type": "制造站", "level": 3, "name": "制造站#1",
             "operators": [{"name": DRAGON, "mood": "2"}]}]
    shifts = [shift("班1", 12, facs, detached=list(detached))]
    shifts += [s for s in extra_shifts]
    return sess_from_shifts(shifts, detached=list(detached))


def _entry_on(s: Session, **kw):
    s.entry_events = True
    s.entry_swap_with = kw.pop("swap_with", DRAGON)
    s.entry_scope = kw.pop("scope", "anywhere")
    s.entry_when = kw.pop("when", "immediate")
    for k, v in kw.items():
        setattr(s, k, v)
    s.recompute()
    return s


def test_section9_after_moves():
    print("\n=== §9 被移动 / 移除 / 换位之后，设置是否仍然生效 ===")

    # —— ① 单班：被人顶掉（锁定入宿 → place_operator） ——
    # ⚠️ 2026-10：这一条原来**钉的是缺陷现状**（她被顶掉后从整份排班消失 ⇒ 0 事件）。
    #    修法＝`Schedule.roster`（导入时出现过的干员名册，`operator_names()` 并上它）——
    #    她被人顶掉后**仍在实时心情表里**，于是 `rules._entry_trigger_ops` 那条
    #    "表里有、这一班的 world 里没有 ⇒ 算存在"的判据轮得到她，照旧触发一次互换。
    s = _entry_on(_entry_scene())
    eq("基线：她在宿舍、换心情触发（1 次事件）", len(entry_marks(s)), 1)
    s.place_operator(0, 0, 0, PAO)                 # 泡泡顶掉她那一格
    s.recompute()
    got = (s.mood_at(FEI, 0), entry_labels(s), FEI in s.operator_names(),
           [p["name"] for p in s.layout_at(0)["detached"]])
    check("被顶掉后她**仍在实时心情表里**（「本班未排班也算存在」）⇒ 换心情照旧触发 1 次",
          got[0] is not None and len(got[1]) == 1 and got[2],
          f"实际 mood_at={got[0]}、entry 事件={got[1]}、在 operator_names={got[2]}；"
          f"期望 mood 有值 + 1 次事件 + 她在名册里")
    # ⚠️ 三件"不许发生的副作用"：**不进 `facilities`**（所以不参与任何技能计数）、
    #    不改变布局、面板「不在基建」那一列能看到她（她是 `bench_names()` 的一员）。
    w = s.schedule.shifts[0].world
    check("被顶掉后她**不进 `facilities`**（不参与任何技能计数）",
          w.get_operator(FEI) is None, f"实际 world 里有她={w.get_operator(FEI) is not None}")
    eq("被顶掉后她**只是心情表里的一个名字**（`traj.names` 含她、`world.all_operators()` 不含）",
       (FEI in s.traj.names, FEI in [o.name for o in w.all_operators()]), (True, False))
    check("被顶掉后她仍在面板「不在基建」那一列（`bench_names()` 含她）",
          FEI in s.bench_names(), f"实际 {s.bench_names()}")

    # —— ①b 同一个动作的另一条入口：「干员与心情」位置列（清空她那格） ——
    s = _entry_on(_entry_scene())
    s.set_slots(0, 0, ["", MUR])                   # 位置列把第 1 位清空
    s.recompute()
    check("同一条形状的第二个入口：「干员与心情」位置列清空她那格 ⇒ 她仍在心情表里、照旧触发",
          (s.mood_at(FEI, 0) is not None) and len(entry_marks(s)) == 1
          and FEI in s.operator_names(),
          f"实际 mood_at={s.mood_at(FEI, 0)}、事件={entry_labels(s)}、"
          f"在 operator_names={FEI in s.operator_names()}")

    # —— ② 多班：她在**别的班**有活 ⇒ 现状**能**触发（这是已被修过的那一半） ——
    other = shift("班2", 12, [{"type": "贸易站", "level": 3, "operators": [FEI]}])
    s = _entry_on(_entry_scene(extra_shifts=[other]))
    s.place_operator(0, 0, 0, PAO)                 # 只顶掉第 1 班
    s.recompute()
    check("她在**别的班**有活 ⇒ 被顶掉后仍算存在、第 1 班照旧触发（已修的一半）",
          bool([m for m in entry_marks(s) if m.t == 0]) and s.mood_at(FEI, 0) is not None,
          f"实际 事件={entry_labels(s)}、mood={s.mood_at(FEI, 0)}")

    # —— ③ 显式「不在基建」名单 ——
    s = _entry_on(_entry_scene())
    s.set_detached([FEI])
    s.recompute()
    check("显式名单：她不在设施里但**在名单**里 ⇒ 照旧触发",
          bool(entry_labels(s)) and s.mood_at(FEI, 0) is not None,
          f"实际 事件={entry_labels(s)}")
    # ⚠️ 先关掉闲置入宿再验"平线"：否则她会作为候选被现造的 Operator 塞进宿舍（那是另一码事）
    s.idle_to_dorm = False
    s.recompute()
    check("显式名单：她仍不参与技能计数（速率 0、一条平线）", s.rate_at(FEI, 6) == ZERO,
          f"实际 {s.rate_at(FEI, 6)}")

    # —— ④ 把她自己**锁到别处**（同一班次内换房间） ——
    s = _entry_on(_entry_scene())
    s.place_operator(0, 1, 1, FEI)                 # 从宿舍挪到制造站第 2 位
    s.recompute()
    check("她本人被「锁定入宿」换到**别的设施** ⇒ 仍在 world 里 ⇒ 照旧触发",
          bool(entry_labels(s)) and s.mood_at(FEI, 0) is not None,
          f"实际 事件={entry_labels(s)}")
    eq("她被钉住的位次不被自动入宿碰（停在制造站第 2 位）",
       layout_names(s, 0)["制造站#1"][1], FEI)

    # —— ⑤ 容量收缩挤掉：人**不删**（全占满）时设置照旧生效 ——
    s = sess_from_shifts([shift("班1", 12, [
        {"type": "贸易站", "level": 3, "name": "贸易站#1",
         "operators": [{"name": "甲"}, {"name": "乙"}, {"name": FEI, "mood": "24"}]},
        {"type": "制造站", "level": 3, "operators": [{"name": DRAGON, "mood": "2"}]}])])
    _entry_on(s)
    s.set_room_level(0, 0, 2)                      # 3 → 2 级：越界全是人 ⇒ 保留
    s.recompute()
    check("缩容（越界全是人）后人被保留 ⇒ 她的 M15a 照旧触发",
          any(FEI in o for o in [layout_names(s, 0).get("贸易站#1", [])]) and
          bool(entry_labels(s)), f"实际 布局={layout_names(s, 0)} 事件={entry_labels(s)}")

    # —— ⑥ 培训 / 练度切换：触发集合与引擎行为必须一致（不许静默失效） ——
    for elite in (2, 1, 0):
        s = _entry_on(_entry_scene())
        s.set_training(elite=elite)
        s.recompute()
        holders = [n for n, _r in entry_event_holders(s.schedule.shifts[0].world)]
        fired = bool(entry_labels(s))
        eq(f"练度 E{elite}：触发者集合与引擎行为一致（holders={holders}）",
           fired, bool(holders))

    # —— ⑦ 名单增删：设置一字不动 + 触发照旧 ——
    s = _entry_on(_entry_scene())
    s.idle_protected_slots = 4
    s.idle_blacklist = ["丙"]
    before = settings_snapshot(s)
    s.add_detached("丁")
    s.remove_detached("丁")
    s.recompute()
    eq("名单 add/remove 往返之后设置逐项不变", diff_settings(before, settings_snapshot(s)), [])
    check("名单增删之后换心情照旧触发", bool(entry_labels(s)), f"实际 {entry_labels(s)}")

    # —— ⑧ set_detached 摘人**留洞不左移**（2026-10 修，A3 工单） ——
    #     口径变更：原来这里钉的是"交互层唯一剩下的紧凑化例外"（`AGENTS.md` 坑 27 与
    #     `documents/10-现状与校准记录.md` §3.2 第 2 条记的就是它）。用户裁决与
    #     `models.remove_occupant` / 导入层"名单优先"同口径 ⇒ **留空洞、不左移**，
    #     并把被摘空那一格的锁一并解掉（不留"锁着一个空位"）。
    s = sess_from_data({"facilities": [
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": ["甲", "乙", "丙"]},
        {"type": "贸易站", "level": 3, "operators": ["戊"]}]})
    s.idle_to_dorm = False
    s.place_operator(0, 0, 1, "乙")                # 乙 钉在第 2 位
    s.set_detached(["甲"])                         # 甲 在第 1 位 ⇒ 留洞、后面的人不前移
    vals = seat_values(fac_of(s))
    eq("set_detached 摘人**留空洞、不左移**（[甲,乙,丙] → ['',乙,丙]）",
       vals[:3], ["", "乙", "丙"])
    eq("台账 slots:[1] 仍指向乙的位次（她没漂到丙身上、丙也没被静默上锁）",
       manual_of(fac_of(s)), {"slots": [1], "names": ["乙"]})
    s2 = sess_from_data({"facilities": [
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": ["甲", "乙"]},
        {"type": "贸易站", "level": 3, "operators": ["戊"]}]})
    s2.idle_to_dorm = False
    s2.place_operator(0, 0, 1, "乙")
    s2.set_detached(["甲"])
    dorm = s2.schedule.shifts[0].world.facilities[0]
    eq("紧凑化的第二种症状也修了：位置留洞（乙不前移）",
       seat_values(fac_of(s2))[:2], ["", "乙"])
    check("被摘空那一格不再是 `keep`（自动入宿能填回第 1 位，不留永久空锁）",
          _seat_verdict(dorm, 0, world=s2.schedule.shifts[0].world)[0] != "keep"
          and dorm.next_open_slot() == 0,
          f"实际 verdict[0]={_seat_verdict(dorm, 0, world=s2.schedule.shifts[0].world)[0]}、"
          f"next_open_slot={dorm.next_open_slot()}")

    # —— ⑨ 被闲置入宿换出 ⇒ 下一个执行点重新评估（位置复位、心情连续） ——
    s = _idle_scene([
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "capacity": 1,
         "operators": [{"name": "甲", "mood": "24"}]},
        {"type": "贸易站", "level": 3, "operators": [{"name": "戊"}]}],
        cands=("乙",), mood=10)
    s.set_timeline(hours=[24])
    s.recompute()
    marks0 = [m for m in s.traj.marks if m.kind == "idle" and m.t == 0]
    check("24h 班：班初那一段跑了闲置入宿（乙 换进宿舍）", bool(marks0),
          f"实际 idle 标记 {idle_labels(s)}")
    eq("t=0 那一段的宿舍＝乙（甲被换出）", layout_names(s, 0)["宿舍#1"], ["乙"])
    eq("t=12 那一段从 **pristine** 重建 ⇒ 回到导入时的排布（甲），不是继承上一段的乙",
       layout_names(s, 12)["宿舍#1"], ["甲"])
    check("心情连续继承：乙 进宿舍后确实在回复（t=0 是 10、t=6 更高）",
          to_decimal(s.mood_at("乙", 6)) > D(10), f"实际 {s.mood_at('乙', 6)}")
    eq("被换出的甲心情不变（她不在任何设施里 ⇒ 平线 24）",
       to_decimal(s.mood_at("甲", 6)), D(24))


# ============================================================================
# §10 多周期 / 多班次 / 长班内部换班点
# ============================================================================
def test_section10_multi():
    print("\n=== §10 多周期 / 多班次 / 长班内部换班点 ===")

    eq("execution_offsets：12h→[0]、18h→[0,12]、24h→[0,12]、25h→[0,12,24]、36h→[0,12,24]",
       [[str(x) for x in execution_offsets(h)] for h in (12, 18, 24, 25, 36)],
       [["0"], ["0", "12"], ["0", "12"], ["0", "12", "24"], ["0", "12", "24"]])

    def one_shift(hours, cycles=1, people=(("甲", "10"),)):
        facs = [{"type": "贸易站", "level": 3, "name": "贸易站#1",
                 "operators": [{"name": n, "mood": m} for n, m in people]}]
        return Schedule([shift("班1", hours, facs)], to_decimal(hours))

    pts = execution_points(one_shift(25), 2)
    eq("25h 单班 × 2 周期 ⇒ 6 个执行点（0/12/24/25/37/49）",
       [str(p[0]) for p in pts], ["0", "12", "24", "25", "37", "49"])
    eq("内部换班点**不增加班次数**（班次下标恒为 0）",
       sorted({p[2] for p in pts}), [0])
    eq("周期序号按 1 基（0/12/24 → 周期 1；25/37/49 → 周期 2）",
       [p[3] for p in pts], [1, 1, 1, 2, 2, 2])
    eq("班内偏移按顺序给出", [str(p[4]) for p in pts], ["0", "12", "24", "0", "12", "24"])

    # —— 内部换班标记：即使没事发生也记 ——
    s = sess_from_shifts([shift("班1", 25, [
        {"type": "贸易站", "level": 3, "name": "贸易站#1", "operators": [{"name": "甲"}]}])])
    s.idle_to_dorm = False
    s.set_cycles(1)
    s.recompute()
    internal = [m for m in s.traj.marks if m.kind == "internal"]
    eq("25h 班：内部换班标记 2 条（12h / 24h），即使什么都没发生",
       [str(m.t) for m in internal], ["12", "24"])
    check("内部换班标记的文案写明班次与偏移",
          all("内部换班" in m.label and "第 1 班" in m.label for m in internal),
          f"实际 {[m.label for m in internal]}")

    # —— 心情连续：内部换班点不重置心情 ——
    s = sess_from_data({"facilities": [
        {"type": "制造站", "level": 3, "name": "制造站#1",
         "operators": [{"name": PAO, "mood": "20"}]}]})
    s.idle_to_dorm = False
    s.set_timeline(hours=[25])
    s.recompute()
    a, b = to_decimal(s.mood_at(PAO, D("11.99"))), to_decimal(s.mood_at(PAO, 12))
    check("心情跨内部换班点**连续**（不回到起点值）",
          abs(a - b) < D("0.05") and b < D(20), f"实际 11.99h={a} vs 12h={b}")

    # —— 每个执行点从 pristine 重建位置（换心情 restore_back=False 也只在当段生效） ——
    facs = [{"type": "宿舍", "level": 5, "name": "宿舍#1",
             "operators": [{"name": FEI, "mood": "24"}, {"name": MUR, "mood": "3"}]},
            {"type": "制造站", "level": 3, "name": "制造站#1",
             "operators": [{"name": DRAGON, "mood": "2"}]}]
    s = sess_from_shifts([shift("班1", 25, facs)])
    s.entry_events = True
    s.entry_swap_with = DRAGON
    s.entry_scope = "anywhere"
    s.entry_when = "immediate"
    s.entry_restore_back = False                   # 位置也一起互换
    s.recompute()
    one, two = layout_names(s, 0), layout_names(s, 12)
    eq("位置每段从 pristine 重建：t=0 换过位置（宿舍#1 变成龙舌兰在住）",
       one["宿舍#1"], [DRAGON, MUR])
    eq("位置每段从 pristine 重建：t=12 那一段回到 pristine（她没满 24 ⇒ 这一次不换）",
       two["宿舍#1"], [FEI, MUR])
    eq("位置复位不是「叠加两次互换」（t=12 的制造站回到 pristine）",
       two["制造站#1"], [DRAGON])
    check("但心情**连续继承**（t=12 那一刻她不是满 24，也不是起点值）",
          to_decimal(s.mood_at(FEI, 12)) != D(24), f"实际 {s.mood_at(FEI, 12)}")

    # —— 心情锚点落在内部换班点时刻 ⇒ 生效 ——
    s = sess_from_shifts([shift("班1", 25, [
        {"type": "制造站", "level": 3, "name": "制造站#1",
         "operators": [{"name": PAO, "mood": "20"}]}])])
    s.idle_to_dorm = False
    s.set_mood_at(PAO, 6, 12)                      # 第 1 周期 t=12（正好是内部换班点）
    s.recompute()
    eq("心情锚点落在内部换班点上 ⇒ 同刻生效", to_decimal(s.mood_at(PAO, 12)), D(6))

    # —— 周期数变多/变少：增量重算与全量一致（设置面不因增量而错） ——
    s = Session()
    s.load_paths([MAA_SAMPLE])
    s.set_cycles(7)
    s.recompute()
    full = {n: [str(v) for v in s.traj.moods[n]] for n in s.traj.names}
    s.set_cycles(3)
    s.recompute()                                  # 走"只截断"
    s.set_cycles(7)
    s.recompute()                                  # 走"续算"
    eq("周期 7→3→7：增量重算与原全量逐位一致",
       {n: [str(v) for v in s.traj.moods[n]] for n in s.traj.names}, full)


# ============================================================================
# §11 导入设置 vs 界面改设置（同一批落地面）的一致性
# ============================================================================
def test_section11_import_vs_ui():
    print("\n=== §11 导入设置 vs 界面改设置的一致性 ===")

    facs = [{"type": "宿舍", "level": 5, "name": "宿舍#1",
             "operators": [{"name": FEI, "mood": "20"}, {"name": MUR, "mood": "3"}]},
            {"type": "贸易站", "level": 3, "name": "贸易站#1",
             "operators": [{"name": DRAGON, "mood": "2"}]},
            {"type": "贸易站", "level": 3, "name": "贸易站#2",
             "operators": [{"name": PAO, "mood": "10"}]}]

    # ① 换心情：文件带设置 vs 导入后按 API 设同一批值
    data = {"entry_events": {"enabled": True, "swap_with": DRAGON, "scope": "anywhere",
                             "when": "wait"},
            "facilities": copy.deepcopy(facs)}
    a = sess_from_data(copy.deepcopy(data), apply_file_settings=True)   # 文件设置那一路
    b = sess_from_data({"facilities": copy.deepcopy(facs)})
    handle(b, "set_entry_events", {"enabled": True, "swap_with": DRAGON,
                                   "scope": "anywhere", "when": "wait"})
    eq("换心情：导入带的设置 vs 界面/API 逐项设置的**设置快照**一致",
       settings_snapshot(a)["entry_events"], settings_snapshot(b)["entry_events"])
    eq("换心情：两条路的**轨迹**逐位一致",
       {n: [str(v) for v in a.traj.moods[n]] for n in a.traj.names},
       {n: [str(v) for v in b.traj.moods[n]] for n in b.traj.names})

    # ② 逐班覆盖：文件写 per_shift vs API 写同一份
    data2 = {"entry_events": {"enabled": True, "swap_with": DRAGON, "scope": "anywhere",
                              "per_shift": [{"key": 1, "enabled": False}]},
             "facilities": copy.deepcopy(facs)}
    a = sess_from_data(copy.deepcopy(data2), apply_file_settings=True)
    b = sess_from_data({"facilities": copy.deepcopy(facs)})
    handle(b, "set_entry_events", {"enabled": True, "swap_with": DRAGON, "scope": "anywhere",
                                   "per_shift": [{"key": 1, "enabled": False}]})
    eq("逐班覆盖：两条路的 per_shift 逐项一致",
       [(o.key, o.enabled, o.swap_with) for o in a.entry_per_shift],
       [(o.key, o.enabled, o.swap_with) for o in b.entry_per_shift])

    # ③ 闲置入宿：文件带设置 vs API 设同一批值
    #    ⚠️ 2026-10 改断言：原来这里还比"两条路的 `per_operator` **表示**允许不同
    #    （文件里可以写 True 项，API 只留 False）但**有效行为**必须一致"，比较元组里带了
    #    `idle_globals`。**为什么变**：逐人「参不参与」随功能整条撤销，`idle_globals` 字段
    #    已删 ⇒ 元组变三项。**新钉**：两条路**都带着**已撤的 `per_operator` 时，
    #    文件那条回一条导入 note、API 那条回一条 `notes`，两边**行为仍然完全一致**、
    #    且会话上都不落任何逐人状态（`hasattr` 为假）—— 这就把"撤除后仍不静默"钉住了。
    data3 = {"idle_to_dorm": {"enabled": True, "protected_slots": 2,
                              "blacklist": [PAO],
                              "per_operator": [{"name": "乙", "enabled": False}]},
             "facilities": copy.deepcopy(facs)}
    a = sess_from_data(copy.deepcopy(data3), apply_file_settings=True)
    b = sess_from_data({"facilities": copy.deepcopy(facs)})
    out_b = handle(b, "set_idle_to_dorm", {"enabled": True, "protected_slots": 2,
                                           "blacklist": [PAO],
                                           "per_operator": [{"name": "乙", "enabled": False}]})
    eq("闲置入宿：两条路的 effective 设置一致（enabled/锁定位置/黑名单）",
       (a.idle_to_dorm, a.idle_protected_slots, a.idle_blacklist),
       (b.idle_to_dorm, b.idle_protected_slots, b.idle_blacklist))
    eq("闲置入宿：两条路的**引擎宿舍排布**逐位一致",
       layout_names(a, 0), layout_names(b, 0))
    eq("闲置入宿：两条路带着已撤的 `per_operator` 时都**不落任何逐人状态**（字段不存在）",
       [hasattr(a, "idle_globals"), hasattr(a, "idle_entries"),
        hasattr(b, "idle_globals"), hasattr(b, "idle_entries")], [False] * 4)
    eq("闲置入宿：API 那条路回一条「该设置已撤、已忽略」",
       out_b.get("notes"), ["per_operator（逐人「参不参与」）该设置已撤、已忽略"])
    check("闲置入宿：文件那条路把它记进导入 note（同一个措辞）",
          any("该设置已撤、已忽略" in n for n in a.loaded.reports[0].notes),
          f"实际 {a.loaded.reports[0].notes}")

    # ④ 摆位：界面两条入口（逐位矩阵 vs 「干员与心情」位置列）同一动作 ⇒ 同一布局与台账
    def mv(which):
        s = sess_from_data({"facilities": [
            {"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": ["甲", "乙"]},
            {"type": "贸易站", "level": 3, "name": "贸易站#1", "operators": ["丙"]}]})
        s.idle_to_dorm = False
        if which == "matrix":                       # 锁定入宿矩阵 → place_operator
            s.place_operator(0, 0, 2, "丙")         # 把丙钉到宿舍第 3 位（先摘本班别处）
        else:                                       # 干员与心情位置列 → apply_manual_shifts
            changes = s.facilities_of(0)
            changes[0]["operators"] = ["甲", "乙", "丙"]
            changes[1]["operators"] = []
            s.apply_manual_shifts({0: changes})
        return s

    a, b = mv("matrix"), mv("列")
    eq("摆位：矩阵（place_operator）与位置列（apply_manual_shifts）落出**同一份布局**",
       facilities_json(a), facilities_json(b))
    eq("摆位：两条路的台账也一致（位次 + 人名）",
       [manual_of(f) for f in a.facilities_of(0)],
       [manual_of(f) for f in b.facilities_of(0)])

    # ⑤ 起点心情 / 锚点：文件 vs API
    data5 = {"initial_global": {}, "facilities": copy.deepcopy(facs)}
    a = sess_from_data(copy.deepcopy(data5))
    a.set_initial_mood(FEI, 13)
    a.set_mood_at(PAO, 7, 6)
    a.recompute()
    b = sess_from_data(copy.deepcopy(data5))
    handle(b, "set_initial_moods", {"moods": {FEI: 13}})
    handle(b, "set_mood_at", {"name": PAO, "mood": 7, "at": 6})
    eq("起点心情 / 锚点：Session 方法 vs API 的落点一致",
       settings_snapshot(a)["initial_moods"], settings_snapshot(b)["initial_moods"])
    eq("起点心情 / 锚点：两条路的锚点一致",
       settings_snapshot(a)["mood_events"], settings_snapshot(b)["mood_events"])
    eq("起点心情 / 锚点：两条路的轨迹逐位一致",
       {n: [str(v) for v in a.traj.moods[n]] for n in a.traj.names},
       {n: [str(v) for v in b.traj.moods[n]] for n in b.traj.names})

    # ⑥ 房间等级：Session.set_room_level vs API op（含"改等级别把练度拍回默认"）
    a = sess_from_data({"facilities": [{"type": "贸易站", "level": 3, "name": "贸易站#1",
                                        "operators": [{"name": PAO, "elite": 1}]}]})
    a.idle_to_dorm = False
    a.set_room_level(0, 0, 2)
    b = sess_from_data({"facilities": [{"type": "贸易站", "level": 3, "name": "贸易站#1",
                                        "operators": [{"name": PAO, "elite": 1}]}]})
    b.idle_to_dorm = False
    handle(b, "set_room_level", {"shift_index": 1, "facility_index": 1, "level": 2})
    eq("房间等级：Session 与 API 结果一致（等级 + 占位）",
       (a.facilities_of(0)[0]["level"], seat_specs(a.facilities_of(0)[0])),
       (b.facilities_of(0)[0]["level"], seat_specs(b.facilities_of(0)[0])))
    eq("房间等级：改等级不会把练度对象写法拍回纯名字（对象原样保留）",
       seat_specs(a.facilities_of(0)[0]), [{"name": PAO, "elite": 1}])

    # ⑦ 「干员与心情」整批落地（apply_batch 的 Session 侧）也写手动台账
    s = sess_from_data({"facilities": [
        {"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": ["甲", "乙"]}]})
    s.idle_to_dorm = False
    changes = s.facilities_of(0)
    changes[0]["operators"] = ["甲", "丙"]
    n = s.apply_manual_shifts({0: changes}, recompute=False)
    eq("apply_manual_shifts：只动过的那一间算改动", n, 1)
    eq("apply_manual_shifts：只锁真的改了的那一位（乙→丙）",
       manual_of(fac_of(s)), {"slots": [1], "names": ["丙"]})
    s.recompute()
    eq("apply_manual_shifts(recompute=False) 之后手动补一次 recompute 即生效",
       layout_names(s, 0)["宿舍#1"], ["甲", "丙"])


# ============================================================================
# main
# ============================================================================
#: 分节清单（**顺序即运行顺序**；新增一节记得加进来 —— `main` 会核对没有漏网的 `test_*`）
SECTIONS = (
    ("§0 设置面清单（分页 / 上限常量 / 每个字段都有写入口）", test_section0_surface),
    ("§1 时间轴与全局（周期数边界 / 起始时钟 / 时长 / 容量 / 氛围）", test_section1_timeline),
    ("§1b 容量收缩 `_trim_to_capacity`", test_section1_shrink),
    ("§2 干员与心情（练度 / 起点心情 / 心情锚点 / 房间等级）", test_section2_batch),
    ("§3 锁定入宿（摆位即上锁 / 粒度 / 后者覆盖 / 拒绝）", test_section3_lock),
    ("§4 换心情（触发者 / 满 24 门 / 三种 when / scope / 逐班）", test_section4_entry),
    ("§5 闲置入宿（开关 / 黑名单 / 已撤的 per_operator / 锁定位置 / 两相）", test_section5_idle),
    ("§6 导入不变量（两个导入入口逐字段比对 / 文件级设置）", test_section6_import),
    ("§7 导出 → 再导入往返", test_section7_roundtrip),
    ("§8 改完设置再触发各种动作（保真扫描）", test_section8_survive_actions),
    ("§9 被移动 / 移除 / 换位之后设置是否仍生效", test_section9_after_moves),
    ("§10 多周期 / 多班次 / 长班内部换班点", test_section10_multi),
    ("§11 导入设置 vs 界面改设置的一致性", test_section11_import_vs_ui),
)


def main() -> int:
    print("设置面自检（scripts/verify_settings.py）"
          " —— 分节、每条 check、失败打印实际值 vs 期望值")
    listed = [fn for _title, fn in SECTIONS]
    missing = sorted(n for n, f in globals().items()
                     if n.startswith("test_") and callable(f) and f not in listed)
    if missing:                       # 新写了 test_ 却忘了挂进 SECTIONS
        print(f"  ⚠ 有 {len(missing)} 个 test_* 没挂进 SECTIONS：{missing}")
    rows = []
    for title, func in SECTIONS:
        p0, f0, k0 = PASS, FAIL, KNOWN
        func()
        rows.append((title, PASS - p0, FAIL - f0, KNOWN - k0))
        print(f"  —— 本节 {PASS - p0} 条 / {FAIL - f0} fail")
    print("\n" + "=" * 72)
    print("分节清单（条数 / fail / 其中已知项）：")
    for title, n, f, k in rows:
        print(f"  {n:>4} 条  {f} fail  （已知 {k}）  {title}")
    print("-" * 72)
    print(f"设置面自检：{PASS} 条 / {FAIL} fail"
          f"　（{len(rows)} 节；其中 {KNOWN} 条是已知缺陷/已知例外的现状钉住）")
    if KNOWN:
        print(f"  ⚠ 0 fail ≠ 没有缺陷：下列 {KNOWN} 条钉的是**现状**，真缺陷清单见交付报告 §2：")
        seen = set()
        for issue, name in KNOWN_ISSUES:
            if issue in seen:
                continue
            seen.add(issue)
            print(f"    · [{issue}] {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

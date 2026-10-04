"""scripts/verify_idle.py —— 「闲置入宿」自检（三层解耦版：手动编辑 > 自动入宿 > 导入布局）。

用法：
    .venv/Scripts/python.exe scripts/verify_idle.py          # 全绿 → 退出码 0，有红 → 1
    .venv/Scripts/python.exe scripts/verify_idle.py -v       # 顺带打印每一条通过项

口径（2026-10 重写，见 `documents/04-特殊机制.md` 第 30 条）：

  ① **手动编辑**（`models.ManualLedger`，跟着班次布局走）—— **摆位即上锁、清空即解锁**
     （2026-10 口径反转）：摆了人的**位次**与手动放进去的**人**绝对不碰（不占、不换）；
     **把该位置空＝那一位交还自动入宿**（不再"保持空着"）；导入不打标。
     ⚠️ **粒度＝只锁你碰过的那些位次（累积）**（2026-10 第三次收敛）：界面两条路交来
     "碰过哪些位次"（`touched`），台账按"旧 ∩ 现在还在的 ∪ 这次碰过的"算 ⇒ **同房间
     导入进来的人不受影响、仍可被自动入宿换出**，以前手动放过的格子继续保持锁，
     清空一格只解一格。不传 `touched` 的老写法＝整段有名字的全算（`set_slots` 的 API 契约）。
     锁只在**内部**（自动入宿判定位置）用 —— 界面上没有 `☑ 锁` / 「全部解锁」，
     但 `Session.set_seat_lock` / `clear_seat_locks` 仍保留给 API（能锁一个**空位**）。
  ② **全局配置** —— 总开关 / 锁定位置数（竖向正序前 N 个**逻辑位次**，默认 5）/
     黑名单（不能通过闲置入宿进宿舍）/ 逐人"不参与"。
  ③ **自动入宿** —— 两相：竖向正序填空床 → 全满则取**心情最低**的候选替换
     "锁定区外、心情 ≥ 她、且心情最大"的住户（并列取竖向正序靠前）；换人接替原位次；
     被换出者不在本执行点再入队，留到下一个执行点重新评估；
     定点＝**宿舍里（锁定区外）最低的那位也 ≥ 外面剩下的候选**。
  ④ **导入布局**＝基线不是护身符：住进宿舍的人照样可以被换出去。

⚠️ 已作废、别再加回来：手动指定位置 / 手动点名（归**手动编辑逻辑**，走布局快照）、
   竖向反序取"首个严格大于"、被换出者追加队尾、连续排列（留空洞即跳过）、
   以及更早的四级优先级 / 挂件门 / 阵营门 / 菲亚梅塔例外。
"""
from __future__ import annotations

import inspect
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:                                  # Windows 控制台默认 GBK，打不出「⇒」这类字符
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                     # noqa: BLE001 —— 老解释器/被重定向时忽略
    pass

from mood_soc import rules as rules_mod                      # noqa: E402
from mood_soc.battery import to_decimal                      # noqa: E402
from mood_soc.config import MOOD_MAX, FacilityType           # noqa: E402
from mood_soc.models import (ManualLedger, mark_manual,      # noqa: E402
                             read_manual, set_seat)
from mood_soc.models import build_idle_to_dorm_config        # noqa: E402
from mood_soc.rules import (DEFAULT_PROTECTED_SLOTS, SEAT_AUTO,  # noqa: E402
                            SEAT_KEEP, SEAT_LOCKED, SEAT_SWAPPABLE,
                            _best_swap_victim, _dorm_numbered, _next_free_slots,
                            _protected_positions, _seat_key, _seat_verdict,
                            apply_idle_to_dorm, dorm_state)
from store.layout import build_base_layout                   # noqa: E402
from store.schedule import (MoodSetEvent, Schedule, Shift,   # noqa: E402
                            execution_offsets, execution_points, simulate_schedule)

PASS = 0
FAIL = 0
VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv


def check(name: str, ok: bool, detail: str = "") -> None:
    """一条断言：通过就 +1（`-v` 时打印），失败打印原因并计入失败数。"""
    global PASS, FAIL
    if ok:
        PASS += 1
        if VERBOSE:
            print(f"  ok   {name}")
        return
    FAIL += 1
    print(f"  FAIL {name}" + (f"　—— {detail}" if detail else ""))


# ============================================================================
# 合成布局（只用名字 + 心情；闲置入宿不读技能，所以合成干员足够且完全确定）
# ============================================================================
def make_layout(dorms, *, capacity=5, dorm_count=None, extra=(), protected_slots=5,
                blacklist=(), enabled=True, per_operator=None, manual=None, labels=False):
    """造一份布局：`dorms` = `[[(名字, 心情), ...], ...]`（按宿舍序号 1 基）。

    `extra`：额外设施（dict 原样塞进去，如 `{"type": "制造站", ...}`）；
    `per_operator`：逐人"参不参与"设置（`IdleToDormEntry` 的 JSON 写法）；
    `manual`：手动台账 `{宿舍下标(0 基): {"slots": [...], "names": [...]}}`；
    `labels=True`：给宿舍写上 `宿舍#N`（**多间不带名字的宿舍在引擎里是同一个键**，
    要按设施取用时必须给名字）。
    """
    facilities = []
    total_dorms = len(dorms) if dorm_count is None else int(dorm_count)
    for i in range(total_dorms):
        people = dorms[i] if i < len(dorms) else []
        fac = {"type": "宿舍", "level": 1, "capacity": int(capacity),
               "operators": [{"name": str(n), "mood": str(m)} for n, m in people]}
        if labels:
            fac["name"] = f"宿舍#{i + 1}"
        if manual and i in manual:
            fac["manual"] = manual[i]
        facilities.append(fac)
    facilities.extend(extra)
    idle = {"enabled": bool(enabled), "protected_slots": int(protected_slots),
            "blacklist": [str(n) for n in blacklist]}
    if per_operator is not None:
        idle["per_operator"] = per_operator
    return build_base_layout({"facilities": facilities, "idle_to_dorm": idle})


def dorm_at(world, no: int):
    """第 `no`（1 基）间**可用宿舍**的设施对象。"""
    return _dorm_numbered(world)[no - 1][1]


def dorm_names(world):
    """`[[名字或 None, ...], ...]`：各**可用宿舍**按**位次**列出（去掉尾部没到过的空槽）。"""
    out = []
    for f in world.facilities:
        if f.ftype is not FacilityType.DORMITORY or not f.enabled:
            continue
        mapping = f.slot_map()
        reach = (max(mapping) + 1) if mapping else 0
        out.append([mapping[i].name if i in mapping else None for i in range(reach)])
    return out


def slots_text(world):
    """各宿舍按**容量**铺开的位次文本（`None` → `一`），便于看清空洞。"""
    out = []
    for f in world.facilities:
        if f.ftype is not FacilityType.DORMITORY or not f.enabled:
            continue
        mapping = f.slot_map()
        out.append([mapping[i].name if i in mapping else None for i in range(int(f.capacity))])
    return out


def home_moods(world):
    """各宿舍**在位**住户的心情（跳过空槽与手动钉住的位次）。"""
    out = []
    for f in world.facilities:
        if f.ftype is not FacilityType.DORMITORY or not f.enabled:
            continue
        led = read_manual(f)
        out.append([op.mood for i, op in sorted(f.slot_map().items())
                    if not led.pins_slot(i) and not led.pins_name(op.name)])
    return out


def mood_of(world, name):
    op = world.get_operator(name)
    return None if op is None else op.mood


def run(world, idle, *, trace=None, scope=None, only=None):
    return apply_idle_to_dorm(world, enabled=True, idle=dict(idle),
                              trace=trace, scope=scope, only=only)


def groups_of(events):
    return [ev.group for ev in events]


def joined(events):
    return " / ".join(ev.detail for ev in events)


# ============================================================================
# ① 适用条件与执行点（§2/§3：无班次门槛；长班班内 12h 整数倍也算执行点）
# ============================================================================
def test_no_gate_and_offsets():
    print("§2 适用条件：不设班次数量门槛 + 内部换班偏移")
    check("签名里没有班次数参数（门槛已取消）",
          "shift_count" not in inspect.signature(apply_idle_to_dorm).parameters)
    check("执行点偏移：12h→[0]、18h→[0,12]、24h→[0,12]、25h→[0,12,24]",
          [execution_offsets(h) for h in ("12", "18", "24", "25")]
          == [[Decimal(0)], [Decimal(0), Decimal(12)], [Decimal(0), Decimal(12)],
              [Decimal(0), Decimal(12), Decimal(24)]],
          str([execution_offsets(h) for h in ("12", "18", "24", "25")]))
    world = make_layout([[("甲", 5)]], capacity=2, dorm_count=1, protected_slots=0)
    events = run(world, {"乙": 10})
    check("单班排班照旧执行（有空位就住进去）",
          dorm_names(world) == [["甲", "乙"]]
          and "idle_to_dorm" in groups_of(events), str(dorm_names(world)))


def test_execution_points():
    print("§3 执行点切分（班初 + 长班内部换班）")

    def _schedule_one_shift(hours):
        facs = [{"type": "宿舍", "level": 1, "capacity": 5,
                 "operators": [{"name": "甲", "mood": "5"}]}]
        return Schedule(shifts=[Shift(label="班1", hours=hours, facilities=facs)])

    pts = execution_points(_schedule_one_shift("24"), 2)
    check("24h 单班 × 2 周期 → 4 个执行点（0/12/24/36）",
          [p[0] for p in pts] == [Decimal(0), Decimal(12), Decimal(24), Decimal(36)],
          str([p[0] for p in pts]))
    check("内部换班点**不增加班次数**（班次下标仍是 0）",
          all(p[2] == 0 for p in pts), str([p[2] for p in pts]))
    check("周期序号按 1 基（0/12 → 周期 1，24/36 → 周期 2）",
          [p[3] for p in pts] == [1, 1, 2, 2], str([p[3] for p in pts]))


# ============================================================================
# ② 相 1：竖向正序填空床
# ============================================================================
def test_vertical_order():
    print("相 1：竖向正序填空床（位次优先、宿舍序号其次）")
    world = make_layout([[("甲", 5), ("乙", 5), ("丙", 5), ("丁", 5)], []],
                        capacity=4, dorm_count=2, protected_slots=0)
    run(world, {"戊": 6, "己": 7})
    check("空床按 宿1位5 → 宿2位1 依次入住",
          slots_text(world) == [["甲", "乙", "丙", "丁", "戊"], ["己", None, None, None, None]]
          or slots_text(world) == [["甲", "乙", "丙", "丁"], ["戊", "己", None, None]],
          str(slots_text(world)))

    # 空洞位次照样可入住
    world = make_layout([[("甲", 5)]], capacity=3, dorm_count=1, protected_slots=0)
    set_seat(world.facilities[0], 1, None) if 1 in world.facilities[0].slot_map() else None
    check("空洞位次照样是可入住位（位次不左移）",
          [s for _k, _f, s in _next_free_slots(world)] == [1, 2],
          str([s for _k, _f, s in _next_free_slots(world)]))


# ============================================================================
# ③ 相 2：低心情优先换人 + 定点终态
# ============================================================================
def test_swap_lowest_mood_first():
    print("相 2：取心情最低的候选，换出锁定区外**心情最大的合格住户**")
    world = make_layout([[("甲", 24), ("乙", 22), ("丙", 21), ("丁", 20)]],
                        capacity=4, dorm_count=1, protected_slots=0)
    run(world, {"戊": 5, "己": 10})
    home = sorted(home_moods(world)[0])
    check("宿舍装进心情最低的两位（5、10）⇒ [5, 10, 20, 21]",
          home == [Decimal(x) for x in ("5", "10", "20", "21")], str(home))
    check("定点：宿舍里最低的那位（5）≤ 外面剩下的候选（22/24）",
          min(home) == Decimal("5") and Decimal("5") < Decimal("22"), str(home))

    world = make_layout([[("甲", 24), ("乙", 22), ("丙", 21), ("丁", 20)]],
                        capacity=4, dorm_count=1, protected_slots=0)
    run(world, {"戊": 5})
    check("只换一位：换出**合格住户里心情最大**的 24（留出余量给后到的候选）",
          sorted(home_moods(world)[0]) == [Decimal(x) for x in ("5", "20", "21", "22")],
          str(sorted(home_moods(world)[0])))

    world = make_layout([[("甲", 24), ("乙", 10)]], capacity=2, dorm_count=1, protected_slots=0)
    run(world, {"丙": 10})
    check("住户 10 与候选 10：≥ 成立 ⇒ 换（把门槛抬到 10，后到的更高候选才换得进）",
          sorted(home_moods(world)[0]) == [Decimal("10"), Decimal("10")]
          and world.get_operator("甲") is None,
          str(sorted(home_moods(world)[0])))
    check("不合格（住户更低）时记一条「未执行」",
          "idle_to_dorm_skipped" in groups_of(
              run(make_layout([[("甲", 5)]], capacity=1, dorm_count=1, protected_slots=0),
                  {"丁": 10})), "")


def test_swap_victim_tiebreak():
    print("相 2：并列时取**竖向正序最靠前**的住户（旧口径是反序取最靠后）")
    world = make_layout([[("甲", 24), ("乙", 24)], [("丙", 24), ("丁", 24)]],
                        capacity=2, dorm_count=2, protected_slots=0)
    fac, slot, victim = _best_swap_victim(world, Decimal("10"),
                                          protected=_protected_positions(world, 0))
    check("竖向正序最靠前 = 宿1位1（甲）", victim is not None and victim.name == "甲",
          str(victim))


def test_evicted_return_to_queue():
    print("相 2：被换出者**不在本执行点再入队**，她的重新评估发生在下一个执行点")
    world = make_layout([[("甲", 24), ("乙", 20)]], capacity=2, dorm_count=1, protected_slots=0)
    run(world, {"丙": 5, "丁": 19})
    check("两个候选都进得去（丙顶掉 20，丁再顶掉 24）",
          {o.name for f in world.facilities for o in f.operators} == {"丙", "丁"},
          str(slots_text(world)))
    check("换人接替**被换出者的原位次**、不产生空洞（5 顶掉 24 占位 1，19 顶掉 20 占位 2）",
          world.facilities[0].slot_of("丙") == 0 and world.facilities[0].slot_of("丁") == 1,
          str({i: o.name for i, o in world.facilities[0].slot_map().items()}))
    # 被换出者留到下一个执行点再判：这里她换不掉心情更低的甲 ⇒ 正确结果是留在外面
    world = make_layout([[("甲", 10)], [("乙", 24)]], capacity=1, dorm_count=2,
                        protected_slots=0, labels=True)
    events = run(world, {"丙": 20})
    check("丙 顶掉乙（心情 24）；乙 留在外面（她换不掉心情更低的甲）",
          dorm_at(world, 1).slot_of("甲") == 0 and dorm_at(world, 2).slot_of("丙") == 0
          and world.get_operator("乙") is None,
          str(slots_text(world)))
    check("本执行点只发生一次换人（被换出者不再入队）",
          len([e for e in events if e.group == "idle_to_dorm"]) == 1,
          str([e.detail[:50] for e in events]))


def test_terminal_state():
    print("定点：队列里剩下的人都比宿舍里的人更满")
    world = make_layout([[("甲", 24), ("乙", 24), ("丙", 24), ("丁", 24), ("戊", 24)]],
                        capacity=5, dorm_count=1, protected_slots=0)
    run(world, {"己": 5, "庚": 6, "辛": 7, "壬": 8, "癸": 9, "子": 10})
    home = home_moods(world)[0]
    check("宿舍装进心情最低的 5 位（5,6,7,8,9）",
          sorted(home) == [Decimal(x) for x in ("5", "6", "7", "8", "9")], str(sorted(home)))
    check("剩下的候选（10）不低于宿舍内最低值 ⇒ 这一位不动（记未执行）",
          "子" not in [o.name for o in world.facilities[0].operators]
          and Decimal("10") >= min(home), str(sorted(home)))


# ============================================================================
# ④ 手动编辑逻辑（最高优先级；导入不打标）
# ============================================================================
def test_manual_locks_slot():
    print("手动编辑：钉住的位次不被自动入宿占用")
    world = make_layout([[("甲", 5)]], capacity=3, dorm_count=1, protected_slots=0,
                        manual={0: {"slots": [1], "names": []}})
    run(world, {"乙": 6})
    check("台账里钉住的位次保持空着（＝API 锁一个空位那种状态）⇒ 乙 去第 3 位",
          slots_text(world) == [["甲", None, "乙"]], str(slots_text(world)))
    check("该位次的裁决是 keep", _seat_verdict(world.facilities[0], 1)[0] == SEAT_KEEP,
          _seat_verdict(world.facilities[0], 1)[0])


def test_manual_locks_person():
    print("手动编辑：手动放进去的人不被换出（哪怕心情更低）")
    world = make_layout([[("甲", 24)]], capacity=1, dorm_count=1, protected_slots=0,
                        manual={0: {"slots": [0], "names": ["甲"]}})
    events = run(world, {"乙": 5})
    check("乙 心情 5 也换不掉被钉住的甲",
          dorm_names(world) == [["甲"]], str(dorm_names(world)))
    check("并记一条「未执行」",
          "idle_to_dorm_skipped" in groups_of(events), str(groups_of(events)))
    check("裁决是 keep（人）", _seat_verdict(world.facilities[0], 0)[0] == SEAT_KEEP, "")


def test_manual_beats_lockzone():
    print("手动编辑优先于锁定区：手动可以放/换到锁定区里")
    world = make_layout([[("甲", 24)]], capacity=1, dorm_count=1, protected_slots=0,
                        manual={0: {"slots": [0], "names": ["乙"]}})
    check("手动钉住的人不被换出（心情 5 的丙进不来）",
          run(world, {"丙": 5}) and world.facilities[0].slot_of("甲") == 0 and
          world.facilities[0].slot_of("丙") is None, str(slots_text(world)))
    world2 = make_layout([[("甲", 24)]], capacity=1, dorm_count=1, protected_slots=1,
                         labels=True)
    check("没有手动标记时锁定区是 locked",
          _seat_verdict(dorm_at(world2, 1), 0, world=world2,
                        protected=_protected_positions(world2, 1))[0] == SEAT_LOCKED, "")
    world3 = make_layout([[("甲", 24)]], capacity=1, dorm_count=1, protected_slots=1,
                         manual={0: {"slots": [0], "names": []}})
    check("同一位置上手动标记胜出（keep 而不是 locked）",
          _seat_verdict(dorm_at(world3, 1), 0, world=world3,
                        protected=_protected_positions(world3, 1))[0] == SEAT_KEEP, "")


def test_import_has_no_marks():
    print("导入布局不打标：宿舍住户可被换出（导入 = 基线，不是护身符）")
    world = make_layout([[("甲", 24)]], capacity=1, dorm_count=1, protected_slots=0)
    check("导入住户的裁决是 swappable",
          _seat_verdict(world.facilities[0], 0)[0] == SEAT_SWAPPABLE, "")
    run(world, {"乙": 5})
    check("心情 5 的闲置干员真的把她换出去了",
          dorm_names(world) == [["乙"]], str(dorm_names(world)))


def test_ledger_primitives():
    print("手动台账的原语（mark_manual / read_manual / clear_manual）")
    world = make_layout([[("甲", 24), ("乙", 24)]], capacity=3, dorm_count=1, protected_slots=0)
    fac = world.facilities[0]
    check("导入时台账是空的", read_manual(fac).is_empty(), str(read_manual(fac).to_dict()))
    mark_manual(fac, 1, "")
    check("手动清空某位 ⇒ 该位次进台账（人被移出 names）",
          read_manual(fac).pins_slot(1) and not read_manual(fac).pins_name("乙"),
          read_manual(fac).to_dict())
    mark_manual(fac, 0, "甲")
    check("手动写名 ⇒ 位次 + 人都进台账",
          read_manual(fac).pins_slot(0) and read_manual(fac).pins_name("甲"),
          read_manual(fac).to_dict())
    fac._manual = ManualLedger()
    check("clear 之后回到空台账", read_manual(fac).is_empty(), "")


# ============================================================================
# ⑤ 全局配置逻辑（总开关 / 锁定位置数 / 黑名单 / 逐人不参与）
# ============================================================================
def test_blacklist():
    print("全局配置：黑名单")
    world = make_layout([[]], capacity=1, dorm_count=1, protected_slots=0, blacklist=["甲"])
    run(world, {"甲": 5, "乙": 5})
    check("黑名单不进候选（乙 进、甲 不进）",
          dorm_names(world) == [["乙"]], str(dorm_names(world)))
    world = make_layout([[("甲", 24)]], capacity=1, dorm_count=1, protected_slots=0)
    world.idle_to_dorm.blacklist = ["乙"]
    run(world, {"乙": 5})
    check("黑名单只是「不能自己进宿舍」——不能换出别人",
          dorm_names(world) == [["甲"]], str(dorm_names(world)))


def test_enabled_switch():
    print("全局配置：总开关")
    world = make_layout([[]], capacity=1, dorm_count=1, protected_slots=0)
    events = apply_idle_to_dorm(world, enabled=False, idle={"甲": 5})
    check("显式关 ⇒ 不动布局、无事件",
          dorm_names(world) == [[]] and events == [], str(dorm_names(world)))
    world = make_layout([[]], capacity=1, dorm_count=1, protected_slots=0, enabled=False)
    check("JSON 里 enabled=false ⇒ 默认不结算",
          apply_idle_to_dorm(world, idle={"甲": 5}) == [], "")
    world = make_layout([[]], capacity=1, dorm_count=1, protected_slots=0)
    check("没配置 enabled 也按开（默认开是全项目口径）",
          len(apply_idle_to_dorm(world, idle={"甲": 5})) == 1, "")


def test_protected_slots():
    print("全局配置：锁定位置数（竖向正序前 N 个**逻辑位次**）")
    check("默认值 = 5", DEFAULT_PROTECTED_SLOTS == 5, str(DEFAULT_PROTECTED_SLOTS))
    check("配置缺省时是 5", build_idle_to_dorm_config({}).protected_slots == 5, "")
    check("小驼峰别名也认",
          build_idle_to_dorm_config({"protectedSlots": 2}).protected_slots == 2, "")
    world = make_layout([[("甲", 24), ("乙", 24)], [("丙", 24), ("丁", 24)]],
                        capacity=2, dorm_count=2, protected_slots=5, labels=True)
    prot = _protected_positions(world, 5)
    check("竖向正序前 5 个（2 间 × 2 位 ⇒ 只有 4 个真实位置，全锁）",
          len(prot) == 4, str(len(prot)))
    check("锁的是前两间宿舍的全部位次",
          all((_seat_key(world, dorm_at(world, i)), j) in prot
              for i in (1, 2) for j in range(2)), str(sorted(prot)))
    world = make_layout([[("甲", 24)], [("乙", 24)]], capacity=1, dorm_count=2,
                        protected_slots=1, labels=True)
    run(world, {"丙": 5})
    check("只锁 宿1位1 ⇒ 换出宿2位1（乙）",
          dorm_at(world, 1).slot_of("甲") == 0 and dorm_at(world, 2).slot_of("丙") == 0,
          str(slots_text(world)))
    world = make_layout([[]], capacity=2, dorm_count=1, protected_slots=2)
    run(world, {"丙": 5, "丁": 6})
    check("锁定区的**空位**照样能入住（两个都在锁定区）",
          dorm_names(world) == [["丙", "丁"]], str(dorm_names(world)))
    world = make_layout([[("甲", 24)]], capacity=1, dorm_count=1, protected_slots=5,
                        labels=True)
    run(world, {"乙": 20})
    check("锁定数超出总位置数 ⇒ 按总数生效（这一位也锁住、乙 进不来）",
          dorm_at(world, 1).slot_of("甲") == 0 and dorm_at(world, 1).slot_of("乙") is None,
          str(slots_text(world)))


def test_per_operator_disabled():
    print("全局配置：逐人「这一位不参与」")
    world = make_layout([[("甲", 24)]], capacity=1, dorm_count=1, protected_slots=0,
                        per_operator=[{"name": "乙", "enabled": False}])
    run(world, {"乙": 5})
    check("勾掉参与的人不被安排",
          dorm_names(world) == [["甲"]], str(dorm_names(world)))
    world = make_layout([[("甲", 24)]], capacity=1, dorm_count=1, protected_slots=0,
                        per_operator=[{"name": "乙", "enabled": True}])
    run(world, {"乙": 5})
    check("参与的人照常安排", dorm_names(world) == [["乙"]], str(dorm_names(world)))


def test_dorm_state():
    print("宿舍态（面板 / trace 的唯一来源）")
    world = make_layout([[("甲", 5)], [("乙", 5)]], capacity=3, dorm_count=2, protected_slots=0)
    state = dorm_state(world)
    check("按位次给名字、尾部空槽不占数组长度",
          state["dorms"] == {1: ["甲"], 2: ["乙"]}, str(state["dorms"]))
    check("next = 可入住的下一个位次（1 基）", state["next"] == {1: 2, 2: 2}, str(state["next"]))
    check("holes = 还空着的位次（1 基）",
          state["holes"] == {1: [2, 3], 2: [2, 3]}, str(state["holes"]))
    check("capacity 照抄设施容量", state["capacity"] == {1: 3, 2: 3}, str(state["capacity"]))
    check("free = 还有空位的宿舍序号", state["free"] == [1, 2], str(state["free"]))
    world = make_layout([[("甲", 5)]], capacity=3, dorm_count=1, protected_slots=0,
                        manual={0: {"slots": [1], "names": []}})
    state = dorm_state(world)
    check("手动钉住的空位不出现在 next 的候选里（next 给的是 3）",
          state["next"][1] == 3, str(state["next"]))
    check("但它仍算在 holes 里（面板要显示「这一格空着」）",
          state["holes"][1] == [2, 3], str(state["holes"]))

# ============================================================================
# ⑥ 班次层（引擎副本 / 候选口径 / 长班内部换班 / 心情先后）
# ============================================================================
def _schedule(cycles_dorms, hours="12", detached=(), entry=None):
    facs = [{"type": "宿舍", "level": 1, "capacity": 5,
             "operators": [{"name": n, "mood": str(m)} for n, m in cycles_dorms[0]]}]
    return Schedule(shifts=[Shift(label=f"班{i + 1}", hours=hours, facilities=facs,
                                  detached=list(detached))
                            for i in range(cycles_dorms[1])])


def test_schedule_layer():
    print("班次层：位置每个执行点重建 + 改的是模拟副本而非排班快照")
    sch = Schedule(shifts=[Shift(label="班1", hours="24", facilities=[
        {"type": "宿舍", "level": 1, "capacity": 5,
         "operators": [{"name": "甲", "mood": "24"}]}], detached=["乙"])])
    traj = simulate_schedule(sch, cycles=2, initial_moods={"乙": "10"}, idle_to_dorm=True,
                             idle_protected_slots=0)
    check("每班重建：第 2 周期的 12h 内部换班点仍按当刻心情重新判定",
          traj.world_at(Decimal("0")) is not None
          and traj.world_at(Decimal("12")) is not None, "")
    snap = sch.shifts[0].world
    check("排班快照没被改（模拟副本才变）",
          snap.facility_of("甲") is not None and snap.facility_of("乙") is None, "")
    check("引擎副本里乙进了宿舍",
          traj.world_at(Decimal("0")).facility_of("乙") is not None, "")


def test_candidate_uses_pristine():
    print("候选口径：看「这一班导入时的布局」，不看跑过进驻事件之后的世界")
    facs = [{"type": "宿舍", "level": 1, "capacity": 2,
             "operators": [{"name": "甲", "mood": "24"}]},
            {"type": "制造站", "level": 1, "capacity": 1, "operators": []}]
    sch = Schedule(shifts=[Shift(label="班1", hours="24", facilities=facs,
                                 detached=["乙"])])
    traj = simulate_schedule(sch, cycles=1, initial_moods={"乙": "10"}, idle_to_dorm=True,
                             idle_protected_slots=0)
    check("本班没排班的人是候选（→ 进宿舍的空床）",
          traj.world_at(Decimal("0")).facility_of("乙") is not None, "")
    sch2 = Schedule(shifts=[Shift(label="班1", hours="24", facilities=[
        {"type": "宿舍", "level": 1, "capacity": 2, "operators": ["甲"]}])])
    check("排班快照里没有她（候选只存在于引擎副本）",
          sch2.shifts[0].world.facility_of("乙") is None, "")


def test_internal_swap_points():
    print("长班内部换班执行点：每个点都重新判定")
    # 满员宿舍：甲/乙室友 心情 10、候选丙 心情 20 —— 班初换不掉（住户 10 < 候选 20），
    # 但他们一路回满 24 ⇒ 12h 那个内部换班点上就该换成丙。
    facs = [{"type": "宿舍", "level": 1, "capacity": 2, "name": "宿舍#1",
             "operators": [{"name": "甲", "mood": "10"}, {"name": "乙", "mood": "10"}]}]
    sch = Schedule(shifts=[Shift(label="班1", hours="24", facilities=facs,
                                 detached=["丙"])])
    traj = simulate_schedule(sch, cycles=1, initial_moods={"丙": "20"}, idle_to_dorm=True,
                             idle_protected_slots=0)
    marks = [(m.t, m.kind) for m in traj.marks]
    check("内部换班标记即使没事发生也要记",
          (Decimal(12), "internal") in marks, str(marks[:6]))
    check("班初换不掉、12h 点上才换成丙",
          traj.world_at(Decimal(0)).facility_of("丙") is None
          and traj.world_at(Decimal(12)).facility_of("丙") is not None,
          str([(i, o.name) for i, o in
               traj.world_at(Decimal(12)).facilities[0].slot_map().items()]))


def test_anchor_after_idle():
    print("同刻先后：心情锚点在闲置入宿之后生效（§12 的口径）")
    facs = [{"type": "宿舍", "level": 1, "capacity": 5,
             "operators": [{"name": "甲", "mood": "24"}]}]
    sch = Schedule(shifts=[Shift(label="班1", hours="24", facilities=facs,
                                 detached=["乙"])])
    traj = simulate_schedule(sch, cycles=1, initial_moods={"乙": "10"}, idle_to_dorm=True,
                             idle_protected_slots=0,
                             mood_events=[MoodSetEvent(name="乙", t="1", mood="3", cycle=1)])
    check("锚点把她的心情改回 3",
          traj.mood_at("乙", Decimal("1")) == Decimal("3"),
          str(traj.mood_at("乙", Decimal("1"))))
    check("锚点生效前她已在宿舍（闲置入宿先跑）",
          traj.world_at(Decimal("0")).facility_of("乙") is not None, "")


def test_groups_per_point():
    print("逐次表按**换班执行点**分组（同班各执行点共用一份设置）")
    facs = [{"type": "宿舍", "level": 1, "capacity": 5,
             "operators": [{"name": "甲", "mood": "24"}]}]
    sch = Schedule(shifts=[Shift(label="班1", hours="24", facilities=facs,
                                 detached=["乙"])])
    traj = simulate_schedule(sch, cycles=1, initial_moods={"乙": "10"}, idle_to_dorm=True,
                             idle_protected_slots=0)
    check("trace 里记了「轮到她的那一刻」的宿舍态",
          Decimal(0) in traj.idle_states and "乙" in traj.idle_states[Decimal(0)],
          str({str(k): sorted(v) for k, v in traj.idle_states.items()}))
    check("idle_state_at 取得到，且给的是她那一刻的宿舍态",
          (traj.idle_state_at(Decimal(0), "乙") or {}).get("capacity") == {1: 5},
          str(traj.idle_state_at(Decimal(0), "乙")))


def test_config():
    print("配置解析（宽松写法 + 别名）")
    cfg = build_idle_to_dorm_config({"enabled": True, "protected_slots": 3,
                                     "blacklist": ["甲"], "per_operator": [{"name": "乙",
                                                                             "enabled": False}]})
    check("顶层字段被解析",
          cfg.protected_slots == 3 and cfg.blacklist == ["甲"]
          and cfg.entry_for("乙").enabled is False, str(cfg))
    check("true / false 简写",
          build_idle_to_dorm_config(True).enabled is True
          and build_idle_to_dorm_config(False).enabled is False, "")
    check("下划线/小驼峰别名",
          build_idle_to_dorm_config({"black_list": ["乙"]}).blacklist == ["乙"]
          and build_idle_to_dorm_config({"blackList": ["丙"]}).blacklist == ["丙"], "")
    check("负的锁定位置数报错",
          _raises(lambda: build_idle_to_dorm_config({"protected_slots": -1})), "")


def _raises(func) -> bool:
    try:
        func()
    except Exception:                     # noqa: BLE001
        return True
    return False


# ============================================================================
# ⑦ 手动编辑走**布局写入**这条路（Session.set_slots / set_facility_slots）
# ============================================================================
def test_session_write_paths():
    print("手动编辑的写入路径（位次留洞 + 台账 + 容量收缩）")
    from store.session import Session

    data = {"facilities": [
        {"type": "宿舍", "level": 1, "capacity": 5,
         "operators": [{"name": "甲", "mood": "24"}, {"name": "乙", "mood": "24"},
                       {"name": "丙", "mood": "24"}]}]}
    s = Session()
    s.load_data(data)
    s.idle_to_dorm = False
    s.recompute()
    s.set_slots(0, 0, ["甲", "", "丙"])
    fac = s.schedule.shifts[0].facilities[0]
    check("清空第 2 位 ⇒ 写 `slots` 且第 2 格是 null",
          fac.get("slots") == ["甲", None, "丙"], str(fac))
    check("手动台账只记**摆了人的位次**与人（清空的那一位不进 slots）",
          fac.get("manual") == {"slots": [0, 2], "names": ["丙", "甲"]}, str(fac.get("manual")))
    w = s.schedule.shifts[0].world.facilities[0]
    check("引擎侧的位次映射保留空洞",
          {i: o.name for i, o in w.slot_map().items()} == {0: "甲", 2: "丙"},
          str({i: o.name for i, o in w.slot_map().items()}))
    # ⚠️ **2026-10 口径反转**（用户原话：「放上去之后自动上锁。**不需要手动上锁**」
    #    「解锁时只需要**将该位置空**就可以了」「锁功能只作为内部自动入宿进行位置判定时使用，
    #    而**不对外输出暴露**」）：上一轮这里是「清空即上锁」（`manual.slots == [0, 1, 2]`），
    #    现在是**「摆位即上锁、清空即解锁」** —— 被清空的第 2 位**不进** `slots`，
    #    于是引擎把它**交还自动入宿**（`next_open_slot()` 立刻回到第 2 位＝0 基 1）。
    check("清空的那一位被交还自动入宿（下一个空位是第 2 位）",
          w.next_open_slot() == 1, f"next_open_slot={w.next_open_slot()}")

    s.set_facility_slots(0, 0, [None, "丁"])
    fac = s.schedule.shifts[0].facilities[0]
    check("set_facility_slots 逐位写（第 1 位清空、第 2 位写丁）",
          fac.get("slots") == [None, "丁", "丙"], str(fac))
    check("台账跟着更新（甲 被写掉、丁 被标为手动）",
          "甲" not in fac["manual"]["names"] and "丁" in fac["manual"]["names"],
          str(fac["manual"]))
    check("清空的那一位同样交还（第 1 位不再是手动钉住的位次）",
          fac["manual"]["slots"] == [1], str(fac["manual"]))

    # ⚠️ **尾位清空**也要真的"解锁"：`_write_seats` 会裁掉尾部空槽，台账的位次上界若跟着
    #    "占位数组长度"算就会漏掉越界下标 —— 上界必须是**容量**（修过的 bug 的另一半）。
    s2 = Session()
    s2.load_data({"facilities": [{"type": "宿舍", "level": 1, "capacity": 5,
                                  "operators": ["甲", "乙", "丙"]}]})
    s2.idle_to_dorm = False
    s2.recompute()
    s2.set_slots(0, 0, ["甲", "乙", ""])
    fac2 = s2.schedule.shifts[0].facilities[0]
    check("清空**末位** ⇒ 那一位不进台账（上界＝容量，越界下标不会被写进去）",
          fac2.get("manual") == {"slots": [0, 1], "names": ["乙", "甲"]},
          str(fac2.get("manual")))
    check("而且它的裁决被交还（自动入宿可以填第 3 位）",
          s2.schedule.shifts[0].world.facilities[0].next_open_slot() == 2,
          f"next_open_slot={s2.schedule.shifts[0].world.facilities[0].next_open_slot()}")


def test_seat_lock_primitives():
    print("显式上锁 / 解锁（逐位 + 全部解锁）")
    from store.session import Session

    s = Session()
    s.load_data({"facilities": [{"type": "宿舍", "level": 1, "capacity": 5,
                                 "operators": ["甲", "乙"]}]})
    s.idle_to_dorm = False
    s.recompute()

    def world():
        return s.schedule.shifts[0].world.facilities[0]

    def raw():
        return s.schedule.shifts[0].facilities[0]

    check("起始：下一个空位是第 3 位", world().next_open_slot() == 2,
          str(world().next_open_slot()))

    s.set_seat_lock(0, 0, 2)                       # 锁一个**空位**
    check("锁一个空位 ⇒ 位次进台账、不写人名",
          raw().get("manual") == {"slots": [2], "names": []}, str(raw().get("manual")))
    check("锁上之后它不再算可入住（下一个空位跳到第 4 位）",
          world().next_open_slot() == 3, str(world().next_open_slot()))

    s.set_seat_lock(0, 0, 2, locked=False)
    check("解锁那个空位 ⇒ 空台账不留痕（`manual` 键整份消失）",
          "manual" not in raw(), str(raw()))
    check("又回到第 3 位可入住", world().next_open_slot() == 2,
          str(world().next_open_slot()))

    s.set_slots(0, 0, ["甲", "乙", "丙"])          # 摆位即上锁 ⇒ 摆了人的 0/1/2 进锁
    s.set_seat_lock(0, 0, 1, locked=False)         # 再解锁第 2 位（"乙" 那一格）
    check("解锁**有人的**位次 ⇒ 位次与那个人名一起摘掉（只摘位次会被 pins_name 抵消）",
          raw().get("manual") == {"slots": [0, 2], "names": ["丙", "甲"]},
          str(raw().get("manual")))
    check("被解锁的人不再受保护（裁决回到 swappable）",
          _seat_verdict(world(), 1)[0] == SEAT_SWAPPABLE,
          str(_seat_verdict(world(), 1)))

    check("查某人被锁在哪：丙 在 第 1 班/第 1 间/第 3 位（设施名是**补名**后的 `宿舍#1`）",
          s.locked_seats_of("丙") == [(0, 0, "宿舍#1", 2)], str(s.locked_seats_of("丙")))
    check("乙 已被解锁 ⇒ 查不到", s.locked_seats_of("乙") == [],
          str(s.locked_seats_of("乙")))

    n = s.clear_seat_locks()
    check("全部解锁 ⇒ 动过 1 个班次、台账清空",
          n == 1 and "manual" not in raw(), f"n={n} fac={raw()}")

    # `manual=False`：只改布局、**不打手动标**（API 的口径；"改布局"与"上锁"的解耦口）
    s7 = Session()
    s7.load_data({"facilities": [{"type": "宿舍", "level": 1, "capacity": 5,
                                  "operators": ["甲", "乙"]}]})
    s7.idle_to_dorm = False
    s7.recompute()
    s7.set_slots(0, 0, ["甲", "", "丙"], manual=False)
    fac7 = s7.schedule.shifts[0].facilities[0]
    check("`manual=False`：布局照改（留洞）但**台账一个字都不写**",
          fac7.get("slots") == ["甲", None, "丙"] and "manual" not in fac7, str(fac7))

    # ⚠️ **对称的拒绝**：'既在名单、又坐在位上、还被锁住' 是自相矛盾的状态，
    #    交互层两个方向都不许把它造出来（可达路径：`remove_from_slots=False` 之后再锁）。
    s8 = Session()
    s8.load_layout({"facilities": [{"type": "宿舍", "level": 1, "capacity": 5,
                                    "operators": ["甲", "乙"]}]}, hours=1)
    s8.set_detached(["甲"], remove_from_slots=False)   # 只进名单、不摘位置
    err8 = ""
    try:
        s8.set_seat_lock(0, 0, 0)                      # 锁"甲"所在的那一位
    except ValueError as exc:
        err8 = str(exc)
    check("上锁那一位的人已在名单里 ⇒ **也拒绝**（与 set_detached 的拒绝对称）",
          "已在「不在基建」名单里" in err8, err8 or "（没有抛异常）")
    check("拒绝之后台账没被写坏",
          "manual" not in s8.schedule.shifts[0].facilities[0],
          str(s8.schedule.shifts[0].facilities[0]))


def test_manual_dorm_editor_state():
    print("「手动入宿」编辑器的只读数据（宿舍 × 位次：谁在、锁没锁）")
    from store.session import Session

    s = Session()
    s.load_layout({"facilities": [
        {"type": "宿舍", "level": 1, "capacity": 5, "operators": ["甲", "乙"]},
        {"type": "制造站", "level": 3, "operators": ["丙"]}]}, hours=1)
    s.set_seat_lock(0, 0, 2)                     # API 锁第 3 位（空位）⇒ 应当"保持空着"
    st = s.manual_dorm_editor_state(0)
    check("只列宿舍（制造站不进编辑器）",
          [d["name"] for d in st["dorms"]] == ["宿舍#1"],
          str([d["name"] for d in st["dorms"]]))
    seats = st["dorms"][0]["seats"]
    check("逐格给出人名（空槽是空串）",
          [x["name"] for x in seats] == ["甲", "乙", "", "", ""], str(seats))
    check("锁标记与裁决点同口径（手动锁住的**空位**也标 locked）",
          [x["locked"] for x in seats] == [False, False, True, False, False],
          str([x["locked"] for x in seats]))
    check("同时给出班次清单（编辑器要按班次切换）",
          [k for k, _label in st["shifts"]] == [0], str(st["shifts"]))


def test_apply_manual_shifts():
    print("界面整批摆位也写手动台账（Q15=(a)：`Session.apply_manual_shifts`）")
    from store.session import Session

    s = Session()
    s.load_layout({"facilities": [
        {"type": "宿舍", "level": 1, "capacity": 5, "operators": ["甲", "乙"]},
        {"type": "制造站", "level": 3, "operators": ["丙"]}]}, hours=1)
    facs = s.facilities_of(0)
    # 面板风格：设施描述里 `operators` 是"按位次对齐的名字列表"（空串＝空槽）
    facs[0] = dict(facs[0], operators=["甲", "", "丁"])
    n = s.apply_manual_shifts({0: facs})
    raw = s.schedule.shifts[0].facilities[0]
    # ⚠️ **粒度＝"这次真的改了的那几位"（2026-10 第三次收敛）**：面板交来的是一整班布局，
    #    但逐位比对之后只有第 2 位（乙→空）与第 3 位（空→丁）算"碰过"；第 1 位的 甲
    #    是**导入进来的人**、用户没碰 ⇒ 不进 `names`（旧口径把她一起锁上＝连坐）。
    #    清空的那一位（第 2 位）仍然"摆位即上锁 / 清空即解锁"⇒ 交还自动入宿。
    check("改过的设施被写回，台账只记**这次真的改了的位次里还有人的那些**"
          "（导入的甲不连坐、清空的第 2 位交还自动入宿）",
          raw.get("slots") == ["甲", None, "丁"]
          and raw.get("manual") == {"slots": [2], "names": ["丁"]},
          str(raw))
    check("制造站没被改 ⇒ **一个标都不打**（不能因为「顺带过一遍」就把它锁上）",
          "manual" not in s.schedule.shifts[0].facilities[1],
          str(s.schedule.shifts[0].facilities[1]))
    check("返回值＝写过的设施数（状态栏「几间房」用它）", n == 2, str(n))
    # 累积：同一条路上再动一格 ⇒ 上一次那个标**留着**（不是每次从头算）
    # ⚠️ 像面板那样 `pop("slots")` + 写 `operators`：两种写法并存时 `slots` 优先，
    #    只改 `operators` 会静默失效（`ui/batch.py` 修过的 bug，这里照它的形状构造）。
    facs = s.facilities_of(0)
    facs[0] = dict(facs[0], operators=["甲", "", "丁", "戊"])
    facs[0].pop("slots", None)
    s.apply_manual_shifts({0: facs})
    raw = s.schedule.shifts[0].facilities[0]
    check("第二条整批摆位：新碰的第 4 位进台账，上一次的第 3 位**留着**（累积）",
          raw.get("manual") == {"slots": [2, 3], "names": ["丁", "戊"]},
          str(raw.get("manual")))


def test_manual_lock_scope():
    """**手动台账的粒度**（2026-10 第三次收敛：只锁碰过的那一格、累积）。

    病根（工单 §0 实测）：界面把"按位次对齐的**整段**"交给 `set_facility_slots`，而旧
    `_write_manual` 是"传进来的整段里有名字的位次全算人写的" ⇒ **只放 1 个人**也会把
    同一间宿舍里**导入进来的人**一起锁上（`slots` 从 `[]` 变 `[0, 1, 4]`），他们从此
    再不能被自动入宿换出。修法：界面两条路交来"**碰过哪些位次**"（`touched`），
    `_write_manual` 按**累积**算：
      `slots = (旧 slots ∩ 现在仍有人的位次) ∪ (T ∩ 现在仍有人的位次)`、
      `names = (旧 names ∩ 现在仍在本设施里的人) ∪ (T 位上现在坐着的人)`。
    ⚠️ **不传 `T` 的老写法保持原样**（＝"整段里有名字的全算"）：那是 `set_slots` 的
      既有 API 契约（`api/` 与 `documents/11-程序接口.md`），最后一条钉住它。
    """
    from store.session import Session, seat_values

    def fac(session) -> dict:
        return session.schedule.shifts[0].facilities[0]

    def ledger(session):
        return fac(session).get("manual") or {}

    def fresh() -> dict:
        """一间**本来就有 2 个人（导入进来）**的宿舍，容量 5（每次给一份新 dict）。"""
        return {"facilities": [{"type": "宿舍", "level": 1, "capacity": 5,
                                "operators": ["甲", "乙"]}]}

    s = Session()
    s.load_layout(fresh(), hours=1)
    # 起手：两个人是**导入**来的 ⇒ 台账空（导入不打标）
    check("起手（2 人是导入的）台账是空的", "manual" not in fac(s), str(fac(s)))
    # ①＋②：**按界面那样**交来"整段回填 + 碰过第 5 位"（`touched=[4]`）——
    #    整段回填是必须的（那个入口 `slots[0]` 就是第 1 位），但打标只认 `touched`。
    s.set_facility_slots(0, 0, ["甲", "乙", None, None, "戊"], touched=[4])
    check("① 只锁碰过的那一格：`slots == [4]`（不是连坐的 `[0, 1, 4]`）",
          ledger(s).get("slots") == [4], str(ledger(s)))
    check("② 导入的人不受影响：`names` 里**没有**第 1、2 位的 甲/乙，"
          "且布局里他们**还在原位上**（不左移、不摘人）",
          ledger(s).get("names") == ["戊"]
          and seat_values(fac(s)) == ["甲", "乙", "", "", "戊"],
          f"{ledger(s)} / {seat_values(fac(s))}")
    # ②b：他们仍然**不是**"被钉住的人"（裁决点第 1 层只看台账）
    w = s.schedule.shifts[0].world.facilities[0]
    check("②b 导入的人仍可被自动入宿换出（裁决不是 `locked`）",
          _seat_verdict(w, 0)[0] != SEAT_LOCKED and _seat_verdict(w, 1)[0] != SEAT_LOCKED,
          f"{_seat_verdict(w, 0)} / {_seat_verdict(w, 1)}")
    # ③ 累积：再在空格放第 2 个人 ⇒ 上一次的标**留着**
    s.set_facility_slots(0, 0, ["甲", "乙", None, "己", "戊"], touched=[3])
    check("③ 累积：`slots == [3, 4]`（含上一次的），两个人都在 `names` 里",
          ledger(s).get("slots") == [3, 4]
          and set(ledger(s).get("names") or []) == {"戊", "己"}, str(ledger(s)))
    # ④ 清一格只解一格（把第 5 位留空，别的格子原样传回）
    s.set_facility_slots(0, 0, ["甲", "乙", None, "己", None], touched=[4])
    check("④ 清一格只解一格：`slots == [3]`，第 4 位的 己 仍在台账里（戊 一起摘掉）",
          ledger(s).get("slots") == [3]
          and ledger(s).get("names") == ["己"], str(ledger(s)))
    # ⑤ 不传 `touched` 的**旧契约**（`set_slots` / API）不变
    s2 = Session()
    s2.load_layout(fresh(), hours=1)
    s2.set_slots(0, 0, ["甲", "乙", "丙"])
    check("⑤ 不传 `touched` 的旧契约不变：`set_slots(整段)` 仍把**有名字的位次全记**进台账",
          (s2.schedule.shifts[0].facilities[0].get("manual") or {})
          == {"slots": [0, 1, 2], "names": ["丙", "乙", "甲"]},
          str(s2.schedule.shifts[0].facilities[0].get("manual")))
    s3 = Session()
    s3.load_layout({"facilities": [{"type": "宿舍", "level": 1, "capacity": 5,
                                    "operators": ["甲", "乙", "丙"]}]}, hours=1)
    s3.set_facility_slots(0, 0, [None, "丁"])
    check("⑤b 同一份兼容口在 `set_facility_slots` 上也在（不传 `touched` ＝ 传进来那一段里"
          "**有名字的全算**；上界仍是那一段的长度 ⇒ 第 3 位的 丙 不进 `slots`、只进 `names`）",
          (s3.schedule.shifts[0].facilities[0].get("manual") or {})
          == {"slots": [1], "names": ["丁", "丙"]},
          str(s3.schedule.shifts[0].facilities[0].get("manual")))


def test_manual_lock_holds_every_point():
    print("手动锁在**每个周期 / 每个换班执行点**都生效（世界级回归）")
    from mood_soc.battery import to_decimal
    from store.session import Session

    s = Session()
    s.load_layout({"facilities": [
        {"type": "制造站", "level": 3, "operators": ["甲"]},
        {"type": "宿舍", "level": 1, "capacity": 5}]}, hours=24)
    s.set_detached(["乙"])                        # 名单里的人也是自动入宿的候选
    s.initial_moods["乙"] = to_decimal("10")      # 心情 < 24 才会被安排
    s.set_cycles(2)
    s.recompute()

    def dorm_seats(t):
        w = s.traj.world_at(t)
        dorm = next(f for f in w.facilities if f.ftype == FacilityType.DORMITORY)
        m = dorm.slot_map()
        return [m[i].name if i in m else "" for i in range(int(dorm.capacity))]

    starts = [seg[0] for seg in s.traj.segments]
    check("前提：这份排班确实有多个执行点（2 周期 × 24h ⇒ 班初 + 班内 12h）",
          len(starts) >= 4, f"{len(starts)} 段：{starts}")
    check("没锁时：**班初那一段**她被安排到第 1 位",
          dorm_seats(starts[0])[0] == "乙",
          str([dorm_seats(t) for t in starts]))
    # ⚠️ 后面几段她**不在宿舍**是**文档化行为**、不是 bug：候选条件是"实时心情 < 24"，
    #    而她在宿舍里回满 24 之后就不再是候选；位置又每个执行点从 `pristine` 重建
    #    （她不在原始布局里）⇒ 那几段她是"不在基建"的平线。心情是满的，数值无害。

    s.set_seat_lock(0, 1, 0)                      # 锁住"宿舍"第 1 位（空位）
    starts2 = [seg[0] for seg in s.traj.segments]
    check("锁住第 1 位后：**每个**执行点的第 1 位都是空的（锁跨周期、跨执行点都成立）",
          all(dorm_seats(t)[0] == "" for t in starts2),
          str([dorm_seats(t) for t in starts2]))
    check("班初那一段她被改安排到第 2 位（锁只是把她挪开，不是把她挡在门外）",
          dorm_seats(starts2[0])[1] == "乙",
          str([dorm_seats(t) for t in starts2]))
    # ★ 这条才是勘察点名的空白：**台账随深拷贝走到每一段、每个周期**
    from mood_soc.models import read_manual
    leds = []
    for t in starts2:
        w = s.traj.world_at(t)
        dorm = next(f for f in w.facilities if f.ftype == FacilityType.DORMITORY)
        leds.append(sorted(read_manual(dorm).slots))
    check("每一段的世界里那份手动锁都在（`read_manual(...).slots == [0]` × 4 段）",
          all(x == [0] for x in leds), str(leds))

    # —— 手动锁优先于**进驻事件**（用户裁决 Q-A=(a)：只换心情、不换位置）——
    #    入驻事件在 `restore_back=False` 时会顺手对调两人的位置，而它过去**绕过台账**
    #    ⇒ 手动钉住的人/位次会被它挪走。"手动锁＝第 1 层、永不被动"要没有例外。
    from mood_soc.rules import _position_of, _swap_positions
    s3 = Session()
    s3.load_data({"facilities": [{"type": "宿舍", "level": 1, "capacity": 5,
                                  "operators": [{"name": "甲", "mood": "20"},
                                                {"name": "乙", "mood": "10"}]}]})
    s3.idle_to_dorm = False
    s3.recompute()
    s3.set_seat_lock(0, 0, 1)                      # 锁住"乙"所在的第 2 位
    w3 = s3.schedule.shifts[0].world
    op_a, op_b = w3.get_operator("甲"), w3.get_operator("乙")
    note = _swap_positions(w3, op_a, op_b)
    check("手动锁住的位次：进驻事件只换心情、**不换位置**", "不对调" in note, note)
    check("位置确实没动（甲 仍在第 1 位、乙 仍在第 2 位）",
          _position_of(w3, op_a)[1] == 0 and _position_of(w3, op_b)[1] == 1,
          f"{_position_of(w3, op_a)} / {_position_of(w3, op_b)}")

    s4 = Session()
    s4.load_data({"facilities": [{"type": "宿舍", "level": 1, "capacity": 5,
                                  "operators": [{"name": "甲", "mood": "20"},
                                                {"name": "乙", "mood": "10"}]}]})
    s4.idle_to_dorm = False
    s4.recompute()
    w4 = s4.schedule.shifts[0].world
    op4a, op4b = w4.get_operator("甲"), w4.get_operator("乙")
    note4 = _swap_positions(w4, op4a, op4b)
    check("没有手动锁时照旧对调（这条规则只保护锁住的位次）",
          "位置也对调" in note4 and _position_of(w4, op4a)[1] == 1,
          note4)


def test_detached_vs_lock():
    print("「不在基建」名单与手动锁的冲突（交互拒绝 / 导入名单优先）")
    from store.session import Session, _seat_values

    # ① 交互层：她已被手动锁住 ⇒ **拒绝**加入名单（不让"锁着 ↔ 在名单里"这个状态被造出来）
    s = Session()
    s.load_layout({"facilities": [{"type": "宿舍", "level": 1, "capacity": 5,
                                   "operators": ["甲", "乙"]}]}, hours=1)
    s.set_detached(["丙"])
    check("没被锁的人照常可以进名单", s.detached == ["丙"], str(s.detached))
    s.set_seat_lock(0, 0, 1)                        # 锁住"乙"所在的第 2 位
    err = ""
    try:
        s.set_detached(["乙"])
    except ValueError as exc:
        err = str(exc)
    check("被锁的人加不进去（抛 ValueError，消息写明她在第几班哪一间第几位）",
          "已被手动锁在" in err and "第 2 位" in err, err or "（没有抛异常）")
    check("拒绝之后名单没被改坏", s.detached == ["丙"], str(s.detached))

    # ② 导入层：文件里同时写 `detached` 与"她占着位置" ⇒ **名单优先**（摘人 + 解该位锁 + 记一条）
    s2 = Session()
    s2.load_layout({"detached": ["甲"], "facilities": [
        {"type": "宿舍", "level": 1, "capacity": 5, "operators": ["甲", "乙"],
         "manual": {"slots": [0], "names": ["甲"]}}]}, hours=1)
    fac = s2.schedule.shifts[0].facilities[0]
    check("导入：名单里的人被从位置上摘掉，且**留洞、不左移**",
          _seat_values(fac) == ["", "乙"], str(fac))
    check("导入：她那位次的锁也一并解除（不留一把锁着空格的锁）",
          "manual" not in fac, str(fac))
    check("导入：名单本身照旧", s2.detached == ["甲"], str(s2.detached))
    note = (getattr(s2.loaded, "notes", None) or [""])[0]
    check("导入：记了一条（用户看得见，不是静默让步）", "名单优先" in note, note)
    check("导入：它也会出现在 `summary()` 里（状态栏与接口都拿得到）",
          "名单优先" in (s2.loaded.summary() if s2.loaded else ""),
          s2.loaded.summary() if s2.loaded else "")

    # ③ 同一族的第三条口径：**手动安排**（`set_slots` / `set_facility_slots`，`manual=True`）
    #    也不许把名单里的人放到在用位次上 —— 三处一起守，"名单 ∧ 在位 ∧ 被锁"才真没入口。
    s9 = Session()
    s9.load_layout({"facilities": [{"type": "宿舍", "level": 1, "capacity": 5,
                                    "operators": ["乙"]}]}, hours=1)
    s9.set_detached(["甲"], remove_from_slots=False)
    err9 = ""
    try:
        s9.set_slots(0, 0, ["甲", "乙"])
    except ValueError as exc:
        err9 = str(exc)
    check("手动安排名单里的人 ⇒ **拒绝**（否则又造出「名单 ∧ 在位 ∧ 被锁」）",
          "已在「不在基建」名单里" in err9, err9 or "（没有抛异常）")
    s9.set_slots(0, 0, ["甲", "乙"], manual=False)
    check("`manual=False` 时不拦（那份「名单 ∧ 在位」是 remove_from_slots=False 明确允许的）",
          "manual" not in s9.schedule.shifts[0].facilities[0],
          str(s9.schedule.shifts[0].facilities[0]))


def test_facility_auto_name():
    print("设施**补名**（未命名设施 → `{类型标签}#{同类型序号}`；锁的稳定键靠它）")
    from store.session import Session

    s = Session()
    s.load_layout({"facilities": [
        {"type": "宿舍", "level": 1, "capacity": 5, "operators": ["甲"]},
        {"type": "宿舍", "level": 1, "capacity": 5, "operators": ["乙"]},
        {"type": "制造站", "level": 3, "operators": ["丙"]},
        {"type": "宿舍", "level": 1, "capacity": 5, "name": "宿舍#2", "operators": ["丁"]},
    ]}, hours=1)
    w = s.schedule.shifts[0].world
    check("未命名设施一律补名（**单间也带序号**），且跳过用户显式起的名字",
          [f.display_name for f in w.facilities]
          == ["宿舍#1", "宿舍#3", "制造站#1", "宿舍#2"],
          str([f.display_name for f in w.facilities]))
    # 补名的**目的**：`_seat_key` 永远走稳定键 `(类型, 实例名)`，不再退到"世界下标"
    # —— 后者在布局增删设施时会让**锁定区与手动锁保护到别的房间**。
    keys = [_seat_key(w, f) for f in w.facilities]
    check("同类型多间各自拿到唯一稳定键（锁定区/手动锁不会互相顶掉）",
          keys[0] == (FacilityType.DORMITORY, "宿舍#1")
          and keys[1] == (FacilityType.DORMITORY, "宿舍#3")
          and len(set(keys)) == len(keys), str(keys))
    # 补名的**代价边界**：只写模型对象 ⇒ 导出（原样吐 `s.facilities`）与往返对比一字不变
    raw_facs = s.schedule.shifts[0].facilities
    check("补名**不写回布局 dict**（导出与「导出→再导入」的往返对比不受影响）",
          all("name" not in f for f in raw_facs[:3])
          and raw_facs[3].get("name") == "宿舍#2",
          str(raw_facs))


def test_seat_io_roundtrip():
    print("布局位次的**公开**读写（`seat_specs` / `seat_values` / `write_seats`）")
    from store.session import seat_specs, seat_values, write_seats

    holey = {"type": "宿舍", "level": 1, "capacity": 5, "slots": ["甲", None, "乙"]}
    compact = {"type": "宿舍", "level": 1, "capacity": 5, "operators": ["甲", "乙"]}

    check("读：`slots` 优先 —— 位次空洞按位次给出（空槽是空串）",
          seat_values(holey) == ["甲", "", "乙"], str(seat_values(holey)))
    check("读：紧凑写法按「下标＝位次」给出",
          seat_values(compact) == ["甲", "乙"], str(seat_values(compact)))
    check("读：spec 保留对象写法（练度不被拍成名字）",
          seat_specs({"operators": [{"name": "甲", "elite": 1}]})
          == [{"name": "甲", "elite": 1}],
          str(seat_specs({"operators": [{"name": "甲", "elite": 1}]})))
    # ⚠️ 数字型 `slots` 是**容量覆盖**（历史写法，v4 蓝图的 `dorm_beds` 走它），不是占位数组 ——
    #    旧实现只判 `is None`，一旦有人对"数字 slots"的设施调它就 `TypeError`（潜伏 bug，
    #    面板遍历**所有**设施时暴露出来）。
    numeric = {"type": "宿舍", "level": 1, "slots": 5, "operators": ["甲"]}
    check("读：数字型 `slots` 当**容量覆盖**、不当占位数组（当占位迭代会 TypeError）",
          seat_values(numeric) == ["甲"], str(seat_values(numeric)))

    out = dict(holey)
    write_seats(out, ["甲", "", "乙", "丙"])
    check("写：有空洞 ⇒ 写 `slots`（空槽 null），且**不左移**",
          out.get("slots") == ["甲", None, "乙", "丙"] and "operators" not in out,
          str(out))
    out2 = dict(holey)
    write_seats(out2, ["甲", "乙"])
    check("写：没有空洞 ⇒ 回到紧凑 `operators`，且**旧的 `slots` 被摘掉**（两种写法互斥）",
          out2.get("operators") == ["甲", "乙"] and "slots" not in out2, str(out2))
    # ⚠️ 这两条就是 `ui/batch.py` 那个"显示为空 + 写回静默失效"的根因：
    #    面板过去只读 `operators`（`slots` 型读成空），写回又用 `dict(f, operators=…)`
    #    把旧 `slots` 留着（`slots` 优先 ⇒ 改动被吞）。现在两边都走这一套公开函数。


def test_capacity_shrink_keeps_people():
    print("容量变小时：只丢越界的**空位**与标记，住着人的格子保留（交给自检报超容量）")
    from store.session import Session

    data = {"facilities": [{"type": "宿舍", "level": 5,
                            "operators": ["甲", "乙", "丙"]}]}
    s = Session()
    s.load_data(data)
    s.idle_to_dorm = False
    s.set_room_level(0, 0, 5)                   # 5 级 = 5 位，装得下 ⇒ 不动
    fac = s.schedule.shifts[0].facilities[0]
    check("容量没变小时不动数据", fac.get("operators") == ["甲", "乙", "丙"]
          or fac.get("slots") == ["甲", "乙", "丙"], str(fac))
    # 把第 4、5 位占上（空位），再让容量变小 ⇒ 越界的空位与标记一起丢
    s.set_facility_slots(0, 0, [None, None, "丙"])
    fac = s.schedule.shifts[0].facilities[0]
    check("先摆成 [空, 空, 丙]", fac.get("slots") == [None, None, "丙"], str(fac))


def main() -> int:
    for _name, func in sorted(globals().items()):
        if _name.startswith("test_") and callable(func):
            func()
    print(f"\n闲置入宿自检：通过 {PASS} 条，失败 {FAIL} 条")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

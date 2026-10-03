"""scripts/verify_idle.py —— 「闲置入宿」自检（三层解耦版：手动编辑 > 自动入宿 > 导入布局）。

用法：
    .venv/Scripts/python.exe scripts/verify_idle.py          # 全绿 → 退出码 0，有红 → 1
    .venv/Scripts/python.exe scripts/verify_idle.py -v       # 顺带打印每一条通过项

口径（2026-10 重写，见 `documents/04-特殊机制.md` 第 30 条）：

  ① **手动编辑**（`models.ManualLedger`，跟着班次布局走）—— 钉住的**位次**与手动放进去的
     **人**绝对不碰（不占、不换）；手动清空的那一位**保持空着**；导入不打标。
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
    check("手动清空的位次保持空着 ⇒ 乙 去第 3 位",
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
    check("手动台账记下**整段**位次与人（含被清空的那一位 ⇒ 保持空着）",
          fac.get("manual") == {"slots": [0, 1, 2], "names": ["丙", "甲"]}, str(fac.get("manual")))
    w = s.schedule.shifts[0].world.facilities[0]
    check("引擎侧的位次映射保留空洞",
          {i: o.name for i, o in w.slot_map().items()} == {0: "甲", 2: "丙"},
          str({i: o.name for i, o in w.slot_map().items()}))
    # ⚠️ 2026-10 改口径：`set_slots` 与 `set_facility_slots` 统一成"**清空即上锁**"。
    #    这条断言就是那个口径的**回归网**：旧实现下被清空的第 2 位不进台账，
    #    自动入宿会立刻把它填上（而文档写的是"手动清空的位次保持空着"）。
    #    `next_open_slot()` 会跳过"已被手动钉住的位次"，所以下一个空位应当是第 4 位（0 基 3）。
    check("清空的那一位不再算可入住（下一个空位是第 4 位）",
          w.next_open_slot() == 3, f"next_open_slot={w.next_open_slot()}")

    s.set_facility_slots(0, 0, [None, "丁"])
    fac = s.schedule.shifts[0].facilities[0]
    check("set_facility_slots 逐位写（第 1 位清空、第 2 位写丁）",
          fac.get("slots") == [None, "丁", "丙"], str(fac))
    check("台账跟着更新（甲 被写掉、丁 被标为手动）",
          "甲" not in fac["manual"]["names"] and "丁" in fac["manual"]["names"],
          str(fac["manual"]))

    # ⚠️ 清空**最后一位**也要上锁：`_write_seats` 会裁掉尾部空槽，若台账的位次上界跟着
    #    "占位数组长度"算，这一格会连"位次"一起消失，"清空即上锁"落不到它身上（修过的 bug）。
    #    位次上界必须是**容量**。
    s2 = Session()
    s2.load_data({"facilities": [{"type": "宿舍", "level": 1, "capacity": 5,
                                  "operators": ["甲", "乙", "丙"]}]})
    s2.idle_to_dorm = False
    s2.recompute()
    s2.set_slots(0, 0, ["甲", "乙", ""])
    fac2 = s2.schedule.shifts[0].facilities[0]
    check("清空**末位** ⇒ 那一格仍留在台账里（位次上界＝容量，不是占位数组长度）",
          fac2.get("manual") == {"slots": [0, 1, 2], "names": ["乙", "甲"]},
          str(fac2.get("manual")))
    check("而且它的裁决是 keep（自动入宿不许填第 3 位）",
          s2.schedule.shifts[0].world.facilities[0].next_open_slot() == 3,
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

    s.set_slots(0, 0, ["甲", "乙", "丙"])           # 手动放人 ⇒ 位次 0/1/2 全进锁
    s.set_seat_lock(0, 0, 1, locked=False)         # 再解锁第 2 位（"乙" 那一格）
    check("解锁**有人的**位次 ⇒ 位次与那个人名一起摘掉（只摘位次会被 pins_name 抵消）",
          raw().get("manual") == {"slots": [0, 2], "names": ["丙", "甲"]},
          str(raw().get("manual")))
    check("被解锁的人不再受保护（裁决回到 swappable）",
          _seat_verdict(world(), 1)[0] == SEAT_SWAPPABLE,
          str(_seat_verdict(world(), 1)))

    check("查某人被锁在哪：丙 在 第 1 班/第 1 间/第 3 位",
          s.locked_seats_of("丙") == [(0, 0, "宿舍", 2)], str(s.locked_seats_of("丙")))
    check("乙 已被解锁 ⇒ 查不到", s.locked_seats_of("乙") == [],
          str(s.locked_seats_of("乙")))

    n = s.clear_seat_locks()
    check("全部解锁 ⇒ 动过 1 个班次、台账清空",
          n == 1 and "manual" not in raw(), f"n={n} fac={raw()}")


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

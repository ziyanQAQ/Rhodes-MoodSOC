"""scripts/verify_idle.py —— 「闲置入宿」自检（口径＝用户文档《闲置入宿完整逻辑》）。

用法：
    .venv/Scripts/python.exe scripts/verify_idle.py          # 全绿 → 退出码 0，有红 → 1
    .venv/Scripts/python.exe scripts/verify_idle.py -v       # 顺带打印每一条通过项

覆盖：
  · **文档 §16 的 7 个典型示例**（默认锁定区 / 空位优先 / 自动交换 / 相等不换 /
    同分取竖向最靠后 / 黑名单 / 重复入队）；
  · **文档 §17 的 14 条不变式**（每条都有对应用例）；
  · 生效门槛（§2）、连续排列（§4）、竖向正序·反序（§4.1/§4.2）、手动位置与点名（§10/§11）、
    配置格式（§14）、以及**班次层**的三条集成口径（§2/§3：位置每班重建、锚点排在入宿之后）。

⚠️ 为什么不用 `tests/`：本仓库的 `tests/*.py` 已被删除（只剩 `__pycache__`），
   所以这份文档口径的全部断言收在这个脚本里，`--check` 式一次性跑完。
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:                                  # Windows 控制台默认 GBK，打不出「⇒」这类字符
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                     # noqa: BLE001 —— 老解释器/被重定向时忽略
    pass

from mood_soc.config import MOOD_MAX, FacilityType          # noqa: E402
from mood_soc.models import build_idle_to_dorm_config       # noqa: E402
from mood_soc.rules import (DEFAULT_PROTECTED_SLOTS, MIN_SHIFTS_FOR_IDLE,  # noqa: E402
                            apply_idle_to_dorm, dorm_state)
from store.layout import build_base_layout                  # noqa: E402
from store.schedule import MoodSetEvent, Schedule, Shift, simulate_schedule  # noqa: E402

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
                blacklist=(), enabled=True, per_operator=None):
    """造一份布局：`dorms` = `[[(名字, 心情), ...], ...]`（按宿舍序号 1 基）。

    `extra`：额外设施（dict 原样塞进去，如 `{"type": "制造站", ...}`）；
    `per_operator`：逐人手动设置（`IdleToDormEntry` 的 JSON 写法）。
    """
    facilities = []
    total_dorms = len(dorms) if dorm_count is None else int(dorm_count)
    for i in range(total_dorms):
        people = dorms[i] if i < len(dorms) else []
        facilities.append({"type": "宿舍", "level": 1, "slots": int(capacity),
                           "operators": [{"name": str(n), "mood": str(m)} for n, m in people]})
    facilities.extend(extra)
    idle = {"enabled": bool(enabled), "protected_slots": int(protected_slots),
            "blacklist": [str(n) for n in blacklist]}
    if per_operator is not None:
        idle["per_operator"] = per_operator
    return build_base_layout({"facilities": facilities, "idle_to_dorm": idle})


def dorm_names(world):
    """`[[名字, ...], ...]`：各**可用宿舍**当前的人（按宿舍序号）。"""
    return [[o.name for o in f.operators] for f in world.facilities
            if f.ftype == FacilityType.DORMITORY and f.enabled]


def mood_of(world, name):
    op = world.get_operator(name)
    return None if op is None else op.mood


def run(world, idle, *, shift_count=3, trace=None, scope=None, swap_with=None):
    return apply_idle_to_dorm(world, shift_count, enabled=True, idle=dict(idle),
                              trace=trace, scope=scope, swap_with=swap_with)


def groups_of(events):
    return [ev.group for ev in events]


# ============================================================================
# §2 生效门槛
# ============================================================================
def test_gate():
    print("§2 生效门槛（不同班次数 >= 3 才执行）")
    for count in (0, 1, 2):
        world = make_layout([[("甲", 5)]], capacity=2, dorm_count=1)
        events = run(world, {"乙": 5}, shift_count=count)
        check(f"shift_count={count} ⇒ 完全不执行（布局一个字节都不改）",
              events == [] and dorm_names(world) == [["甲"]],
              f"events={len(events)} dorms={dorm_names(world)}")

    world = make_layout([[("甲", 5)]], capacity=2, dorm_count=1)
    events = run(world, {"乙": 5}, shift_count=MIN_SHIFTS_FOR_IDLE)
    check(f"shift_count={MIN_SHIFTS_FOR_IDLE} ⇒ 执行（乙住进空位）",
          dorm_names(world) == [["甲", "乙"]] and any(g == "idle_to_dorm" for g in groups_of(events)),
          f"dorms={dorm_names(world)}")

    # 班次数只看"排班里有几个班"，与周期数无关：1 班 × 3 周期 = shift_count 1 ⇒ 不执行
    world = make_layout([[("甲", 5)]], capacity=2, dorm_count=1)
    check("1 个班次 × 3 个周期 = 不执行（门槛只看班次数）",
          run(world, {"乙": 5}, shift_count=1) == [] and dorm_names(world) == [["甲"]])


# ============================================================================
# §4 / §4.1 / §4.2 连续排列 + 竖向正序
# ============================================================================
def test_vertical_order():
    print("§4/§4.1 连续排列与竖向正序")
    # 竖向正序 = 位次优先、宿舍序号其次：宿舍1位5 (5,1) 比 宿舍2位1 (1,2) 靠后 ⇒ 选宿舍2位1
    world = make_layout([[("甲", 5), ("乙", 5), ("丙", 5), ("丁", 5)], []],
                        capacity=5, dorm_count=2, protected_slots=0)
    run(world, {"戊": 4})
    check("空位按竖向正序取最靠前（宿舍2位1 在 宿舍1位5 之前）",
          dorm_names(world) == [["甲", "乙", "丙", "丁"], ["戊"]],
          str(dorm_names(world)))

    # 同一位次时比宿舍序号：宿舍1位1 在 宿舍2位1 之前
    world = make_layout([[], []], capacity=5, dorm_count=2, protected_slots=0)
    run(world, {"甲": 5, "乙": 6})
    check("同一位次比宿舍序号（甲→宿舍1位1、乙→宿舍2位1）",
          dorm_names(world) == [["甲"], ["乙"]], str(dorm_names(world)))

    # 入住总是接到末尾 ⇒ 位次连续，不留洞
    world = make_layout([[("甲", 5)], []], capacity=5, dorm_count=2, protected_slots=0)
    run(world, {"乙": 5, "丙": 5})
    check("入住接到末尾（位次连续、不留中间空位）",
          dorm_names(world) == [["甲", "乙"], ["丙"]], str(dorm_names(world)))


def test_vertical_reverse_tiebreak():
    print("§4.2/§16.5 同心情按竖向反序")
    dorms = [[(f"宿{n}位{i}", 10) for i in range(1, 6)] for n in range(1, 5)]
    dorms[0][4] = ("甲五", 22)          # 宿舍1位5
    dorms[3][4] = ("丁五", 22)          # 宿舍4位5
    world = make_layout(dorms, capacity=5, dorm_count=4, protected_slots=0)
    run(world, {"候选": 6})
    after = dorm_names(world)
    check("22 与 22 同分 ⇒ 优先换竖向最靠后的（宿舍4位5）",
          "候选" in after[3] and "甲五" in after[0],
          str(after))


# ============================================================================
# §5 锁定位置
# ============================================================================
def test_protected_slots():
    print("§5 锁定位置（默认 5，按竖向正序）")
    check("默认锁定位置数 = 5", DEFAULT_PROTECTED_SLOTS == 5)
    check("锁定位置数默认写进配置", build_idle_to_dorm_config({}).protected_slots == 5)
    check("配置里的 protected_slots / blacklist 被解析（含小驼峰别名）",
          build_idle_to_dorm_config({"protectedSlots": 2, "blackList": ["甲"]}).protected_slots == 2
          and build_idle_to_dorm_config({"black_list": ["乙"]}).blacklist == ["乙"])

    # 默认 5 ⇒ 4 间宿舍 × 5 位时锁：宿1位1、宿2位1、宿3位1、宿4位1、宿1位2
    dorms = [[("x", 0) for _ in range(5)] for _ in range(4)]
    world = make_layout(dorms, capacity=5, dorm_count=4, protected_slots=5)
    state = dorm_state(world)
    check("锁定区恰好是竖向正序前 5 个位置（宿1位1/宿2位1/宿3位1/宿4位1/宿1位2）",
          len(state["dorms"]) == 4 and state["next"] == {1: 6, 2: 6, 3: 6, 4: 6})

    # 锁定区里的人自动不换：宿1位1 是 24，全满，候选只能换锁定区外最高那位
    world = make_layout([[("锁甲", 24)], [("乙", 9)]], capacity=1, dorm_count=2,
                        protected_slots=1)
    run(world, {"候选": 5})
    check("锁定位置上的干员不会被自动换出（换的是锁定区外的乙）",
          dorm_names(world) == [["锁甲"], ["候选"]], str(dorm_names(world)))

    # 锁定区的空位照样能入住
    world = make_layout([[], [("乙", 9)]], capacity=1, dorm_count=2, protected_slots=1)
    run(world, {"候选": 5})
    check("锁定区的空位可以被候选入住（宿1位1）",
          dorm_names(world) == [["候选"], ["乙"]], str(dorm_names(world)))

    # protected_slots=0 ⇒ 全都可以换：换掉心情最高的（宿1位1 的 9）
    world = make_layout([[("甲", 9)], [("乙", 8)]], capacity=1, dorm_count=2, protected_slots=0)
    run(world, {"候选": 5})
    check("protected_slots=0 ⇒ 锁定区外＝全部，换心情最高的（甲 9）",
          dorm_names(world) == [["候选"], ["乙"]], str(dorm_names(world)))

    # 超出总位置数 ⇒ 钳到总数（谁都不能换）
    world = make_layout([[("甲", 9)], [("乙", 8)]], capacity=1, dorm_count=2, protected_slots=999)
    events = run(world, {"候选": 5})
    check("protected_slots 超出总位置数 ⇒ 按总数生效（谁都不能换、不报错）",
          dorm_names(world) == [["甲"], ["乙"]] and "idle_to_dorm_skipped" in groups_of(events),
          str(dorm_names(world)))

    # 候选自己住进锁定空位后，那个位置继续锁定 ⇒ 后来者换不走她
    world = make_layout([[], [("乙", 9)], [("丙", 8)]], capacity=1, dorm_count=3,
                        protected_slots=1)
    run(world, {"甲": 5, "丁": 6})
    check("候选住进锁定位置后该位置继续锁定（甲在宿1位1，被换走的是乙 9）",
          dorm_names(world) == [["甲"], ["丁"], ["丙"]], str(dorm_names(world)))


# ============================================================================
# §6 黑名单
# ============================================================================
def test_blacklist():
    print("§6 黑名单（禁止通过闲置入宿进宿舍，不是保护位次）")
    world = make_layout([[]], capacity=2, dorm_count=1, blacklist=["甲"])
    run(world, {"甲": 5, "乙": 6})
    check("黑名单干员不进初始候选",
          dorm_names(world) == [["乙"]], str(dorm_names(world)))

    world = make_layout([[]], capacity=2, dorm_count=1, blacklist=["甲"],
                        per_operator=[{"name": "甲", "dorm": 1}])
    events = run(world, {"甲": 5})
    check("黑名单干员不能用「手动指定位置」进宿舍（优先级高于逐人设置）",
          dorm_names(world) == [[]] and events == [], str(dorm_names(world)))

    world = make_layout([[]], capacity=2, dorm_count=1, blacklist=["甲"],
                        per_operator=[{"name": "甲", "swap_with": "乙"}])
    events = run(world, {"甲": 5})
    check("黑名单干员也不能用「点名」进宿舍",
          dorm_names(world) == [[]] and events == [], str(dorm_names(world)))

    # 排班自带的黑名单干员：照旧可被换出，但换出后不进队尾
    world = make_layout([[("甲", 12)]], capacity=1, dorm_count=1, protected_slots=0,
                        blacklist=["甲"])
    trace = {}
    run(world, {"乙": 5}, trace=trace)
    check("排班放在宿舍里的黑名单干员可以被换出（她不占保护）",
          dorm_names(world) == [["乙"]], str(dorm_names(world)))
    check("黑名单干员被换出后**不**追加到队尾", "甲" not in trace, str(list(trace)))

    # 别人可以点名把宿舍里的黑名单干员换出
    world = make_layout([[("甲", 12)]], capacity=1, dorm_count=1, blacklist=["甲"],
                        per_operator=[{"name": "乙", "swap_with": "甲"}])
    run(world, {"乙": 5})
    check("其他候选可以点名把宿舍里的黑名单干员换出",
          dorm_names(world) == [["乙"]], str(dorm_names(world)))


# ============================================================================
# §7 初始候选
# ============================================================================
def test_candidates():
    print("§7 初始候选（该班完全没出现在任何设施 + 心情 < 24 + 非黑名单）")
    world = make_layout([[("甲", 5)]], capacity=3, dorm_count=1,
                        extra=[{"type": "制造站", "level": 1,
                                "operators": [{"name": "工人", "mood": "5"}]},
                               {"type": "加工站", "level": 1,
                                "operators": [{"name": "挂件", "mood": "5"}]},
                               {"type": "训练室", "level": 1,
                                "operators": [{"name": "教练", "mood": "5"}]}])
    run(world, {"新人": 6, "工人": 5, "挂件": 5, "教练": 5, "甲": 5})
    check("只有「完全不在任何设施里的」人进宿舍（工人/挂件/教练/已在宿舍的甲都不动）",
          dorm_names(world) == [["甲", "新人"]], str(dorm_names(world)))

    world = make_layout([[]], capacity=2, dorm_count=1)
    events = run(world, {"甲": 24, "乙": 23.5})
    check("心情 >= 24 的闲置干员不进候选",
          dorm_names(world) == [["乙"]] and all(e.owner != "甲" for e in events),
          str(dorm_names(world)))

    world = make_layout([[]], capacity=2, dorm_count=1)
    events = run(world, {"甲": 5.5, "乙": 6.5, "丙": 24})
    check("初始队列按 (心情↑, 名字↑) 依次处理 ⇒ 先到的先拿空位",
          dorm_names(world) == [["甲", "乙"]], str(dorm_names(world)))

    # 副手也算"出现在布局里"
    world = make_layout([[]], capacity=2, dorm_count=1,
                        extra=[{"type": "控制中枢", "level": 1, "operators": [],
                                "deputies": [{"name": "副手", "mood": "5"}]}])
    run(world, {"副手": 5, "新人": 6})
    check("副手不是候选（她已出现在该班布局里）",
          dorm_names(world) == [["新人"]], str(dorm_names(world)))


# ============================================================================
# §8 动态候选队列（FIFO + 队尾追加）
# ============================================================================
def test_queue():
    print("§8 队列（先进先出、换出者可再入队、满 24/黑名单不入队）")
    world = make_layout([[("丙", 10)]], capacity=1, dorm_count=1, protected_slots=0)
    trace = {}
    events = run(world, {"甲": 5}, trace=trace)
    check("被换出者追加到队尾、会被再处理一次（丙出现在逐位宿舍态里）",
          "丙" in trace, str(list(trace)))
    check("再处理的丙因为找不到更高的目标而不再换人（这一班不动）",
          dorm_names(world) == [["甲"]] and "idle_to_dorm_skipped" in groups_of(events),
          str(dorm_names(world)))

    world = make_layout([[("丙", 24)]], capacity=1, dorm_count=1, protected_slots=0)
    trace = {}
    run(world, {"甲": 5}, trace=trace)
    check("满 24 的被换出者不进队尾", "丙" not in trace, str(list(trace)))

    world = make_layout([[("丙", 10)]], capacity=1, dorm_count=1, protected_slots=0)
    trace = {}
    run(world, {"甲": 20}, trace=trace)
    check("处理失败（目标不比自己更满）的候选不被重新入队，被换出者也不存在",
          "丙" not in trace and dorm_names(world) == [["丙"]], str(list(trace)))

    # 终止性：多人多宿舍的合成场景，必须返回并且宿舍内心情总和严格下降（每次交换都换低）
    dorms = [[(f"宿{n}位{i}", 20 - i) for i in range(1, 6)] for n in range(1, 5)]
    world = make_layout(dorms, capacity=5, dorm_count=4, protected_slots=5)
    before = sum(o.mood for f in world.facilities if f.ftype == FacilityType.DORMITORY
                 for o in f.operators)
    run(world, {f"闲{i}": 3 for i in range(12)})
    after = sum(o.mood for f in world.facilities if f.ftype == FacilityType.DORMITORY
                for o in f.operators)
    check("队列处理一定终止，且宿舍内心情总和严格下降",
          after < before, f"{before} → {after}")
    check("满 20 人（4 间 × 5 位）一个不多一个不少",
          sum(len(d) for d in dorm_names(world)) == 20, str(dorm_names(world)))


# ============================================================================
# §10 手动指定位置
# ============================================================================
def test_manual_position():
    print("§10 手动指定位置（宿舍 + 位次，连续排列）")
    # 正好是下一个连续位 ⇒ 直接入住
    world = make_layout([[("甲", 9), ("乙", 9)]], capacity=5, dorm_count=1,
                        per_operator=[{"name": "丙", "dorm": 1, "slot": 3}])
    run(world, {"丙": 5})
    check("指定的位次正好是下一个连续位 ⇒ 直接入住",
          dorm_names(world) == [["甲", "乙", "丙"]], str(dorm_names(world)))

    # 在下一个连续位之后 ⇒ 留洞 ⇒ 跳过，且不回退自动
    world = make_layout([[("甲", 9), ("乙", 9)]], capacity=5, dorm_count=1,
                        per_operator=[{"name": "丙", "dorm": 1, "slot": 4}])
    events = run(world, {"丙": 5})
    check("指定的位次在下一个连续位之后（会留洞）⇒ 跳过这一位、不回退自动",
          dorm_names(world) == [["甲", "乙"]]
          and "idle_to_dorm_skipped" in groups_of(events), str(dorm_names(world)))

    # 指定位置有人 + 心情闸。⚠️ 被换出的乙（20 < 24）会追加队尾，而她一出来就发现
    # 这间宿舍还有空位（第 3 位）⇒ 按 §12 又住了回去 —— 这是 §8 + §12 的合成结果。
    world = make_layout([[("甲", 9), ("乙", 20)]], capacity=5, dorm_count=1,
                        per_operator=[{"name": "丙", "dorm": 1, "slot": 2}])
    run(world, {"丙": 5})
    check("指定位置已有人且她心情更高 ⇒ 互换（丙接替第 2 位）",
          dorm_names(world) == [["甲", "丙", "乙"]], str(dorm_names(world)))

    world = make_layout([[("甲", 9), ("乙", 4)]], capacity=5, dorm_count=1,
                        per_operator=[{"name": "丙", "dorm": 1, "slot": 2}])
    run(world, {"丙": 5})
    check("指定位置的人不比自己更满 ⇒ 这一班不换",
          dorm_names(world) == [["甲", "乙"]], str(dorm_names(world)))

    # 手动可以进锁定区
    world = make_layout([[]], capacity=5, dorm_count=1, protected_slots=5,
                        per_operator=[{"name": "丙", "dorm": 1, "slot": 1}])
    run(world, {"丙": 5})
    check("手动指定位置可以进锁定区（第 1 位就是锁定位置）",
          dorm_names(world) == [["丙"]], str(dorm_names(world)))

    # 只指定宿舍：用"现有人数 + 1"；满了 ⇒ 跳过、不回退自动
    world = make_layout([[("甲", 9)], []], capacity=2, dorm_count=2,
                        per_operator=[{"name": "丙", "dorm": 1}])
    run(world, {"丙": 5})
    check("只指定宿舍 ⇒ 放进它的下一个连续位",
          dorm_names(world) == [["甲", "丙"], []], str(dorm_names(world)))

    world = make_layout([[("甲", 9)], []], capacity=1, dorm_count=2,
                        per_operator=[{"name": "丙", "dorm": 1}])
    events = run(world, {"丙": 5})
    check("指定的那间满了 ⇒ 跳过（不回退去别的宿舍、也不换人）",
          dorm_names(world) == [["甲"], []] and "idle_to_dorm_skipped" in groups_of(events),
          str(dorm_names(world)))

    # §10.4 无效位置
    world = make_layout([[]], capacity=2, dorm_count=1,
                        per_operator=[{"name": "丙", "dorm": 3}])
    events = run(world, {"丙": 5})
    check("指定了不存在的宿舍 ⇒ 跳过并记原因",
          dorm_names(world) == [[]] and "idle_to_dorm_skipped" in groups_of(events))

    world = make_layout([[]], capacity=2, dorm_count=1,
                        per_operator=[{"name": "丙", "dorm": 1, "slot": 9}])
    events = run(world, {"丙": 5})
    check("位次超出容量 ⇒ 跳过并记原因",
          dorm_names(world) == [[]] and "idle_to_dorm_skipped" in groups_of(events))

    world = make_layout([[]], capacity=2, dorm_count=1,
                        per_operator=[{"name": "丙", "dorm": 1, "slot": -1}])
    events = run(world, {"丙": 5})
    check("位次非法（负数）⇒ 跳过并记原因",
          dorm_names(world) == [[]] and "idle_to_dorm_skipped" in groups_of(events))


# ============================================================================
# §11 手动点名
# ============================================================================
def test_named_swap():
    print("§11 手动点名（主动换：可锁定区 / 可黑名单 / 不看满心情）")
    world = make_layout([[("甲", 12)]], capacity=1, dorm_count=1,
                        per_operator=[{"name": "乙", "swap_with": "甲"}])
    run(world, {"乙": 5})
    check("点名对象心情没满也照换（12 > 5）",
          dorm_names(world) == [["乙"]], str(dorm_names(world)))

    world = make_layout([[("甲", 12)]], capacity=1, dorm_count=1, protected_slots=5,
                        per_operator=[{"name": "乙", "swap_with": "甲"}])
    run(world, {"乙": 5})
    check("点名对象在锁定区也照换",
          dorm_names(world) == [["乙"]], str(dorm_names(world)))

    world = make_layout([[("甲", 3)]], capacity=1, dorm_count=1,
                        per_operator=[{"name": "乙", "swap_with": "甲"}])
    events = run(world, {"乙": 5})
    check("点名对象不比候选更满 ⇒ 这一班不换",
          dorm_names(world) == [["甲"]] and "idle_to_dorm_skipped" in groups_of(events),
          str(dorm_names(world)))

    world = make_layout([[("甲", 12)], []], capacity=1, dorm_count=2,
                        per_operator=[{"name": "乙", "swap_with": "丙"}])
    events = run(world, {"乙": 5})
    check("点名对象在那一刻不在任何可用宿舍 ⇒ 这一班不处理她",
          dorm_names(world) == [["甲"], []] and "idle_to_dorm_skipped" in groups_of(events),
          str(dorm_names(world)))

    # 手动高于自动：另一间宿舍还空着，也要先按点名换（不是去住空位）
    world = make_layout([[("甲", 12)], []], capacity=1, dorm_count=2,
                        per_operator=[{"name": "乙", "swap_with": "甲"}])
    run(world, {"乙": 5})
    check("手动设置高于自动（有空位也不去住，先执行点名）",
          dorm_names(world)[0] == ["乙"], str(dorm_names(world)))


# ============================================================================
# §12 / §13 自动填空位 + 自动交换
# ============================================================================
def test_auto():
    print("§12/§13 自动填空位与自动交换")
    # §16.2 空位优先：有空位就不换人
    world = make_layout([[], [("乙", 24)]], capacity=1, dorm_count=2, protected_slots=0)
    events = run(world, {"甲": 5})
    check("§16.2 空位优先：甲进入空位，一个满心情的人都没被换出",
          dorm_names(world) == [["甲"], ["乙"]]
          and all(e.target != "乙" for e in events), str(dorm_names(world)))

    # §16.3 自动交换：换出锁定区外心情最高的那位，她被追加队尾
    world = make_layout([[("乙", 20)]], capacity=1, dorm_count=1, protected_slots=0)
    trace = {}
    run(world, {"甲": 8}, trace=trace)
    check("§16.3 全满时换出心情最高者（20 > 8）",
          dorm_names(world) == [["甲"]], str(dorm_names(world)))
    check("§16.3 被换出的乙（20 < 24）追加到队尾", "乙" in trace, str(list(trace)))

    # §16.4 相等不交换、也不重新排队
    world = make_layout([[("乙", 20)]], capacity=1, dorm_count=1, protected_slots=0)
    trace = {}
    run(world, {"甲": 20}, trace=trace)
    check("§16.4 心情相等 ⇒ 不交换、候选本班不入宿",
          dorm_names(world) == [["乙"]] and "乙" not in trace, str(dorm_names(world)))

    # §13.1 只按心情挑人：不检查满心情（16 < 24 也换）、不检查名单
    world = make_layout([[("乙", 16)]], capacity=1, dorm_count=1, protected_slots=0)
    run(world, {"甲": 5})
    check("§13.1 目标不必满 24（16 > 5 就换）",
          dorm_names(world) == [["甲"]], str(dorm_names(world)))

    world = make_layout([[], []], capacity=1, dorm_count=2, protected_slots=0)
    events = run(world, {"甲": 5})
    check("§13.1 没有可交换目标时这一班不动（宿舍还空着就必须先填空位）",
          dorm_names(world) == [["甲"], []] and "idle_to_dorm_skipped" not in groups_of(events))

    # §13.1 源码级：旧判据（挂件 / 阵营门 / 特殊名单）必须已经删净
    from mood_soc import rules
    removed = ("_is_pendant", "_is_pendant_uncached", "_pendant_probe_names", "_all_rates",
               "_dependent_holders", "_world_without", "_faction_protected", "_factionless",
               "FACTION_WORK_TYPES", "TIER3_EXTRA_NAMES", "DORM_PREFERRED_RANGE",
               "DORM_PREFERRED_SLOTS", "_swap_mate", "_fallback_mate", "_named_mate",
               "_dorm_order", "_dorm_with_free_slot", "_idle_candidates", "_factionless")
    left = [n for n in removed if hasattr(rules, n)]
    check("§13.1 旧口径（挂件门 / 阵营门 / 四级优先级 / 特殊名单）已全部删除",
          not left, f"还在：{left}")


# ============================================================================
# §14 配置格式与优先级
# ============================================================================
def test_config():
    print("§14 配置格式（具体程度优先、同分后写赢、黑名单最高）")
    cfg = build_idle_to_dorm_config({
        "enabled": True, "protected_slots": 3, "blacklist": ["甲"],
        "per_operator": [
            {"name": "乙", "enabled": True},
            {"name": "乙", "cycle": 1, "shift": 1, "enabled": False},
            {"name": "丙", "enabled": True},
            {"name": "丙", "enabled": False},
        ]})
    check("周期+班次都写的设置比全局更具体", cfg.entry_for("乙", 1, 1).enabled is False)
    check("别的班次仍用全局那条", cfg.entry_for("乙", 1, 2).enabled is True)
    check("具体程度相同时后写的生效", cfg.entry_for("丙", 1, 1).enabled is False)
    check("顶层字段被解析", cfg.protected_slots == 3 and cfg.blacklist == ["甲"])

    # 引擎侧：作用域生效
    world = make_layout([[]], capacity=2, dorm_count=1,
                        per_operator=[{"name": "甲", "enabled": False}])
    run(world, {"甲": 5}, scope=(1, 1))
    check("逐人设置 enabled=False ⇒ 这一位完全不参与",
          dorm_names(world) == [[]], str(dorm_names(world)))

    world = make_layout([[]], capacity=2, dorm_count=1,
                        per_operator=[{"name": "甲", "cycle": 2, "shift": 1, "enabled": False}])
    run(world, {"甲": 5}, scope=(1, 1))
    check("带作用域的设置只在对应 (周期, 班次) 生效",
          dorm_names(world) == [["甲"]], str(dorm_names(world)))

    world = make_layout([[]], capacity=2, dorm_count=1,
                        per_operator=[{"name": "甲", "cycle": 2, "shift": 1, "enabled": False}])
    run(world, {"甲": 5}, scope=(2, 1))
    check("到了对应的 (周期, 班次) 就生效",
          dorm_names(world) == [[]], str(dorm_names(world)))


# ============================================================================
# §2/§3 班次层（多班排班跑起来）
# ============================================================================
def _fac(dorm_people, capacity=5, extra=()):
    facs = [{"type": "宿舍", "level": 1, "slots": capacity,
             "operators": [{"name": n, "mood": str(m)} for n, m in dorm_people]}]
    facs.extend(extra)
    return facs


#: 班次层用的排班：**班 1 只有宿舍（丙在里面）**，班 2/3 让甲去上班 ——
#: 于是"班 1 里的甲"正好是"这一班完全没出现在任何设施里"的候选（文档 §7 第 4 条）。
_WORK = [{"type": "制造站", "level": 1, "operators": [{"name": "甲", "mood": "5"}]}]


def _schedule(n_shifts, hours=8, dorm_people=(("丙", 24),), capacity=1):
    """`n_shifts` 个班次的排班：第 1 班是宿舍（+候选），其余班次是制造站。"""
    shifts = []
    for i in range(n_shifts):
        facs = _fac(list(dorm_people), capacity) if i == 0 else _fac([], capacity, _WORK)
        shifts.append(Shift(label=f"班{i + 1}", hours=hours, facilities=facs))
    return Schedule(shifts=shifts, cycle_hours=hours * n_shifts)


def _dorm_at(traj, t):
    world = traj.world_at(t)
    if world is None:
        return None
    return [o.name for f in world.facilities
            if f.ftype == FacilityType.DORMITORY and f.enabled for o in f.operators]


def test_schedule_layer():
    print("§2/§3 班次层：门槛、位置每班重建、锚点排在入宿之后")
    # 2 班 ⇒ 不生效
    traj2 = simulate_schedule(_schedule(2), cycles=1, initial_moods={"甲": 5, "丙": 24},
                              idle_to_dorm=True, idle_protected_slots=0)
    check("2 个班次的排班：闲置入宿完全不生效（甲没进宿舍）",
          _dorm_at(traj2, 0) == ["丙"], str(_dorm_at(traj2, 0)))

    # 3 班 ⇒ 生效（甲换出满 24 的丙）
    traj3 = simulate_schedule(_schedule(3), cycles=1, initial_moods={"甲": 5, "丙": 24},
                              idle_to_dorm=True, idle_protected_slots=0)
    check("3 个班次的排班：闲置入宿生效（甲换出丙、住进宿舍）",
          _dorm_at(traj3, 0) == ["甲"], str(_dorm_at(traj3, 0)))

    # 每个周期、每个班次都**重新判定**（位置每班从原始排班重建）
    traj_cycle = simulate_schedule(_schedule(3), cycles=2, initial_moods={"甲": 5, "丙": 24},
                                   idle_to_dorm=True, idle_protected_slots=0)
    ok = all("甲" in traj_cycle.idle_states.get(Decimal(t), {}) for t in (0, 24))
    check("位置每段（含第 2 周期）都从原始排班重建 ⇒ 每个班次都重新判定闲置入宿",
          ok, f"第 1/2 周期的班 1 逐位宿舍态：{traj_cycle.idle_states.get(Decimal(0))} / "
              f"{traj_cycle.idle_states.get(Decimal(24))}")
    check("第 2 周期第 1 班又是「换过人的那份」（甲），不是第 1 周期的残留",
          _dorm_at(traj_cycle, 24) == ["甲"], str(_dorm_at(traj_cycle, 24)))

    # §3：班次起点的手动心情指定发生在闲置入宿**之后** ⇒ 不参与那一次的候选排序
    traj_anchor = simulate_schedule(
        _schedule(3), cycles=1, initial_moods={"甲": 5, "丙": 24}, idle_to_dorm=True,
        idle_protected_slots=0,
        mood_events=[MoodSetEvent(name="甲", t=Decimal(0), mood=Decimal(24), cycle=1)])
    check("班次起点的心情锚点排在闲置入宿之后（甲先按 5 被安排进宿舍，再被置成 24）",
          _dorm_at(traj_anchor, 0) == ["甲"]
          and traj_anchor.mood_at("甲", Decimal(0)) == MOOD_MAX,
          f"dorms={_dorm_at(traj_anchor, 0)} mood={traj_anchor.mood_at('甲', Decimal(0))}")


# ============================================================================
# 入口
# ============================================================================
def main() -> int:
    print(f"闲置入宿自检 —— 口径＝《闲置入宿完整逻辑》"
          f"（门槛 {MIN_SHIFTS_FOR_IDLE} 班、默认锁定 {DEFAULT_PROTECTED_SLOTS} 位）\n")
    for test in (test_gate, test_vertical_order, test_vertical_reverse_tiebreak,
                 test_protected_slots, test_blacklist, test_candidates, test_queue,
                 test_manual_position, test_named_swap, test_auto, test_config,
                 test_schedule_layer):
        test()
    print(f"\n通过 {PASS} 条，失败 {FAIL} 条。")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

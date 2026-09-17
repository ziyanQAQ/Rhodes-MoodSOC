"""tests/test_equivalence.py —— **搬运等价性**金标准：重构前后数值必须一字不差。

背景：P0~P4 那一轮把"数据/计算/状态/界面/接口"重新分层（见 `documents/07-设计史.md` P6）。
搬家最怕的不是崩溃（崩溃会红），而是**悄悄算错**——所以这里把一批**端到端数值快照**
钉死：同一份布局、同一套设置，界面/程序接口/引擎三条路都必须给出下面这些数。

这些数是重构**之前**（原 `mood_soc`+`ui/schedule.py` 实现）跑出来的实测值，
覆盖了：消耗类技能叠加、宿舍回复取最高、变量账本、闲置入宿的空位/互换、
进驻事件的"等她回满"、同刻跳变、多周期连续。**任何一条对不上就说明数值口径被动过。**

⚠️ 这不是"重新算一遍看相等"（那种测试恒真）；是**写死的期望值**。
若某次改动确实要有意改数值 → 必须同时改这里，并在提交信息里说清为什么。

运行：.venv/Scripts/python.exe -m unittest tests.test_equivalence -v
"""
from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mood_soc import build_base_layout, evaluate, evaluate_base            # noqa: E402
from mood_soc.ledger import Bucket                                        # noqa: E402
from mood_soc.rules import mood_ledger                                    # noqa: E402
from store.schedule import load_schedule, simulate_schedule               # noqa: E402
from store.session import Session                                         # noqa: E402

D = Decimal
Q = D("0.000001")          # 线协议/输出的舍入粒度（6 位小数）


def q(value) -> Decimal:
    return D(value).quantize(Q)


#: 演示布局（与 `main.py --demo` 同源：中枢满员 + 两制造 + 一贸易 + 办公室 + 满级宿舍）
DEMO = {"facilities": [
    {"type": "控制中枢", "level": 1,
     "operators": ["玛恩纳", "维什戴尔", "魔王", "令", "路人中枢"]},
    {"type": "制造站", "level": 3, "operators": ["泡泡", "黍", "路人甲"]},
    {"type": "制造站", "level": 3, "operators": ["槐琥", "阿罗玛", "火神"]},
    {"type": "贸易站", "level": 3, "operators": ["火哨", "巫恋", "路人乙"]},
    {"type": "办公室", "level": 3, "operators": ["遥"]},
    {"type": "宿舍", "level": 5, "operators": ["菲亚梅塔"]},
]}

#: 干净布局（无任何心情技能干扰，纯基础消耗/减免）——用来钉住"基础算式"
PLAIN = {"facilities": [
    {"type": "控制中枢", "level": 1, "operators": ["路人1", "路人2", "路人3", "路人4", "路人5"]},
    {"type": "制造站", "level": 3, "operators": ["普通甲", "普通乙", "普通丙"]},
    {"type": "宿舍", "level": 5, "operators": ["休息者"]},
]}


class Test基础算式快照(unittest.TestCase):
    """纯基础：1.0 − 0.25（中枢满员）− 0.1（L3 三人设施）= 0.65；宿舍 Lv5 = 4.0。"""

    def test_工作干员净速率与剩余心情(self):
        world = build_base_layout(PLAIN)
        r = evaluate(world, "普通甲", D("8"))
        self.assertEqual(r.net_rate, D("0.65"))
        self.assertEqual(r.remaining_mood, D("18.8"))        # 24 − 0.65×8

    def test_宿舍回复速率(self):
        world = build_base_layout(PLAIN)
        r = evaluate(world, "休息者", D("3"))
        # 宿舍 Lv5 满氛围 = 1.5 + 0.5 + 2.0 = 4.0/h；起点 24 已满 → 停在 24
        self.assertEqual(r.net_rate, D("-4"))
        self.assertEqual(r.remaining_mood, D("24"))

    def test_base模式可持续时长与瓶颈(self):
        world = build_base_layout(PLAIN)
        b = evaluate_base(world)
        # 中枢 5 人：1 − 0.25 = 0.75/h → 24 / 0.75 = 32h；瓶颈是中枢里最先红脸的那位
        self.assertEqual(b.layout_sustain_hours, D("32"))
        self.assertEqual(b.bottleneck, "路人1")
        by = {o.name: o for o in b.operators}
        self.assertEqual(by["普通甲"].sustain_hours, D("32"))          # 布局口径：全员一致
        self.assertEqual(q(by["普通甲"].mood_at_end), D("3.2"))        # 24 − 0.65×32


class Test演示布局快照(unittest.TestCase):
    """`main.py --demo` 的口径（含多条技能叠加、变量、扩散、消除）。"""

    def setUp(self):
        self.world = build_base_layout(DEMO)

    def test_泡泡(self):
        # 中枢减免 0.25 + 设施 0.1 + 黍/令/魔王/维什戴尔那一片叠加后剩 0.05
        r = evaluate(self.world, "泡泡", D("8"))
        self.assertEqual(q(r.net_rate), D("0.05"))
        self.assertEqual(q(r.remaining_mood), D("23.6"))

    def test_流量账构成不变(self):
        lg = mood_ledger(self.world, "泡泡")
        self.assertEqual(q(lg.total(Bucket.CONSUME)), D("0.3"))
        self.assertEqual(q(lg.total(Bucket.RECOVER)), D("0.25"))
        self.assertEqual(q(lg.net_rate()), D("0.05"))
        # 自身技能「囤积者」（M07a）仍在账上
        self.assertTrue(any(c.skill_name == "囤积者" for c in lg.of(Bucket.CONSUME)))

    def test_巫恋与同设施者(self):
        # 贸易站三人 + 中枢满员 + 「低语」（含自身）+0.25 + 「裁缝α」自身 −0.25 ……
        # 这四个数是**实测值**（口径细节见 documents/04-特殊机制.md / 05-技能分类大纲.md）；
        # 这里只保证"搬家之后还是这四个数"。
        self.assertEqual(q(evaluate(self.world, "巫恋", D("0")).net_rate), D("0.4"))
        self.assertEqual(q(evaluate(self.world, "火哨", D("0")).net_rate), D("0.55"))
        self.assertEqual(q(evaluate(self.world, "路人乙", D("0")).net_rate), D("0.65"))

    def test_宿舍独占回复(self):
        # 菲亚梅塔「自律」独占：宿舍基础回复被清空，只剩 2.0
        lg = mood_ledger(self.world, "菲亚梅塔")
        self.assertEqual(q(lg.total(Bucket.RECOVER)), D("2"))
        self.assertTrue(any(c.exclusive for c in lg.of(Bucket.RECOVER)))


class Test整周期轨迹快照(unittest.TestCase):
    """多班 + 闲置入宿 + 心情锚点 + 进驻事件：轨迹口径必须与搬运前一致。"""

    def test_单班轨迹与解析解一致(self):
        """单班恒定布局：轨迹上取任意时刻 = 解析解 evaluate 的结果。"""
        from store.layout import build_base_layout as build
        from store.schedule import Schedule, Shift
        shift = Shift(label="班次 1", hours=D("24"), facilities=PLAIN["facilities"])
        traj = simulate_schedule(Schedule([shift], D("24")))
        for hours in (D("0"), D("6"), D("12"), D("24")):
            self.assertEqual(q(traj.mood_at("普通甲", hours)),
                             q(evaluate(build(PLAIN), "普通甲", hours).remaining_mood),
                             f"@{hours}h")

    def test_红脸后钳位到0不再下降(self):
        """心情触 0 后停在 0（且速率归零的表现：轨迹值不再变化）。"""
        facs = [{"type": "制造站", "level": 1, "operators": [{"name": "透支者", "mood": "2"}]}]
        from store.schedule import Schedule, Shift
        traj = simulate_schedule(Schedule([Shift("班次 1", D("24"), facs)], D("24")))
        self.assertEqual(q(traj.mood_at("透支者", 0)), D("2"))
        self.assertEqual(q(traj.mood_at("透支者", D("2"))), D("0"))
        self.assertEqual(q(traj.mood_at("透支者", D("6"))), D("0"))

    def test_两周期连续(self):
        """`cycles=2`：心情接着上一周期末尾继续（不是每周期重置）。"""
        from store.schedule import Schedule, Shift
        facs = [{"type": "制造站", "level": 3, "operators": ["干活者"]}]
        traj = simulate_schedule(Schedule([Shift("班次 1", D("24"), facs)], D("24")), cycles=2)
        first = traj.mood_at("干活者", D("0"))
        second = traj.mood_at("干活者", D("24"))
        self.assertEqual(q(first), D("24"))
        # 制造站 1 人：1 − 0（无中枢）− 0（1 人不减）= 1.0/h → 24h 后 0
        self.assertEqual(q(second), D("0"))
        self.assertEqual(q(traj.mood_at("干活者", D("48"))), D("0"))

    def test_心情锚点同刻跳变(self):
        """`set_mood_at` 在指定时刻把心情直接置值（跳变后取值）。"""
        from store.schedule import Schedule, Shift
        facs = [{"type": "制造站", "level": 3, "operators": ["干活者"]}]
        s = Session()
        s.schedule = Schedule([Shift("班次 1", D("24"), facs)], D("24"))
        s.recompute()
        s.set_mood_at("干活者", 5, D("12"))
        s.recompute()
        self.assertEqual(q(s.mood_at("干活者", D("11.9"))), D("12.1"))   # 跳变前：24 − 11.9
        self.assertEqual(q(s.mood_at("干活者", D("12"))), D("5"))        # 跳变后
        self.assertEqual(q(s.mood_at("干活者", D("13"))), D("4"))        # 之后按 1/h 演化

    def test_闲置入宿把闲置未满者放进空位(self):
        """两班轮换 + 起点不满：第 2 班里"没上班又没满"的人被安排进宿舍空位。"""
        from store.schedule import Schedule, Shift
        shifts = [
            Shift("A", D("12"), [{"type": "制造站", "level": 3,
                                  "operators": ["泡泡", "普通甲", "普通乙"]},
                                 {"type": "宿舍", "level": 5, "operators": []}]),
            Shift("B", D("12"), [{"type": "制造站", "level": 3,
                                  "operators": ["普通甲", "普通乙", "普通丙"]},
                                 {"type": "宿舍", "level": 5, "operators": []}]),
        ]
        s = Session()
        s.schedule = Schedule(shifts, D("24"))
        s.set_initial_moods({"泡泡": 12, "普通甲": 24, "普通乙": 24, "普通丙": 24})
        s.idle_to_dorm = True
        s.recompute()
        groups = s.idle_groups()
        self.assertTrue(groups, "第 2 班应该出现'泡泡没上班又没满'的候选")
        rows = [r for _t, _s, rs in groups for r in rs if r[0] == "泡泡"]
        self.assertTrue(rows)
        # 入宿后泡泡的心情走的是宿舍回复（4.0/h），而不是不动的 0
        self.assertGreater(q(s.mood_at("泡泡", D("24"))), q(s.mood_at("泡泡", D("12"))))


class Test三条路同数(unittest.TestCase):
    """引擎直调 / Session / api op —— 三条路的数值必须逐位相同。"""

    def test_session与api一致(self):
        from api.ops import handle
        s1, s2 = Session(), Session()
        s1.load_layout(DEMO)
        s2.load_layout(DEMO)
        handle(s2, "moods", {"at": [0, 8, 24]})
        for t in (D("0"), D("8"), D("24")):
            for name in s1.traj.names:
                self.assertEqual(s1.mood_at(name, t), s2.mood_at(name, t), f"{name}@{t}")

    def test_与解析解在起点一致(self):
        """t=0 时轨迹取值 = 各人的起点心情（两条独立的路径：轨迹 vs 解析）。"""
        s = Session()
        s.load_layout(DEMO)
        world = build_base_layout(DEMO)
        for name in s.traj.names:
            self.assertEqual(q(s.mood_at(name, D("0"))),
                             q(evaluate(world, name, D("0")).initial_mood), name)


if __name__ == "__main__":
    unittest.main()

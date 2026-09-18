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


class Test闲置入宿优先级(unittest.TestCase):
    """闲置入宿的**四级优先级**（用户口径）：

    | 级 | 条件 | 动作 |
    |---|---|---|
    | ① | 任意宿舍还有未占满的位次 | 直接住进去（有空位就不换人），顺序"优先 4 最后 1" |
    | ② | 宿舍全满 | 换「宿舍 #4→#3→#2 的第 2~5 位」里**实时满心情**的那位 |
    | ③ | ②找不到 | 退到「宿舍里其余任何位置」的实时满心情者 |
    | ④ | 都不满足 | 这一班不动 |

    两个容易写错、也最容易回归的点：**宿舍序号必须取自"未排序"的布局顺序**（否则"优先 #4"
    会挑中布局里的第 1 间）；**同优先级内 位次 2~5 先于第 1 位、宿舍 #4 先于 #1**。
    """

    @staticmethod
    def _布局(dorms):
        """`dorms` = [(宿舍名, [成员])]，成员默认满心情 24。"""
        facs = [{"type": "制造站", "level": 3, "operators": ["普通甲", "普通乙"]}]
        for name, ops in dorms:
            facs.append({"type": "宿舍", "name": name, "level": 5, "operators": list(ops)})
        return facs

    def _跑(self, dorms, moods=None, who="板凳甲", mood=10):
        s = Session()
        s.load_layout({"facilities": self._布局(dorms)}, hours=24)
        s.set_initial_moods({"板凳甲": mood, **(moods or {})})
        s.set_detached([who], recompute=False)
        s.idle_to_dorm = True
        s.recompute()
        detail = " / ".join(m.label for m in s.traj.marks if m.kind == "idle")
        return s, detail

    def test_有空位就直接住不换人(self):
        """**优先级①**：宿舍#4 还有空位 → 住进去，谁也不换（哪怕别的宿舍有人满心情）。"""
        s, detail = self._跑([("宿舍#1", ["A1", "A2", "A3", "A4", "A5"]),
                              ("宿舍#2", ["B1", "B2", "B3", "B4", "B5"]),
                              ("宿舍#3", ["C1", "C2", "C3", "C4", "C5"]),
                              ("宿舍#4", ["D1", "D2"])])
        self.assertIn("优先级①", detail)
        self.assertIn("宿舍#4", detail)
        self.assertNotIn("互换", detail)
        # 她从 10 开始按宿舍回复（Lv5 满氛围 4/h）→ 1h 后 14
        self.assertEqual(q(s.mood_at("板凳甲", D(1))), D("14"))

    def test_全满时换宿舍4的第2位(self):
        """**优先级②**：全满 → 换 宿舍#4 的**第 2 位**（不是第 1 位、也不是宿舍#1）。"""
        s, detail = self._跑([("宿舍#1", ["A1", "A2", "A3", "A4", "A5"]),
                              ("宿舍#2", ["B1", "B2", "B3", "B4", "B5"]),
                              ("宿舍#3", ["C1", "C2", "C3", "C4", "C5"]),
                              ("宿舍#4", ["D1", "D2", "D3", "D4", "D5"])])
        self.assertIn("优先级②", detail)
        self.assertIn("宿舍#4 第 2 位", detail)
        self.assertIn("D2", detail)
        self.assertEqual(q(s.mood_at("板凳甲", D(1))), D("14"))

    def test_只有三间宿舍时优先第三间(self):
        """宿舍不足 4 间 → "优先 4 最后 1"自动前移（3 间 → #3 → #2 → #1）。"""
        _s, detail = self._跑([("宿舍#1", ["A1", "A2", "A3", "A4", "A5"]),
                               ("宿舍#2", ["B1", "B2", "B3", "B4", "B5"]),
                               ("宿舍#3", ["C1", "C2", "C3", "C4", "C5"])])
        self.assertIn("宿舍#3 第 2 位", detail)

    def test_位次1只能靠兜底(self):
        """**位次 1 不在②的点名范围**：宿舍#4 只有第 1 位满心情时，走③兜底换第 1 位。"""
        _s, detail = self._跑([("宿舍#1", ["A1", "A2", "A3", "A4", "A5"]),
                               ("宿舍#2", ["B1", "B2", "B3", "B4", "B5"]),
                               ("宿舍#3", ["C1", "C2", "C3", "C4", "C5"]),
                               ("宿舍#4", ["D1", "D2", "D3", "D4", "D5"])],
                              moods={"D1": 24, "D2": 5, "D3": 5, "D4": 5, "D5": 5,
                                     "B1": 5, "B2": 5, "B3": 5, "B4": 5, "B5": 5,
                                     "A1": 5, "A2": 5, "A3": 5, "A4": 5, "A5": 5,
                                     "C1": 5, "C2": 5, "C3": 5, "C4": 5, "C5": 5})
        self.assertIn("优先级③", detail)
        self.assertIn("D1", detail)

    def test_都不满足就这一班不动(self):
        """**优先级④**：宿舍全满且没有满心情的人 → 不动，心情平线（+ 记一条说明）。"""
        s, detail = self._跑([("宿舍#1", ["A1", "A2", "A3", "A4", "A5"]),
                              ("宿舍#2", ["B1", "B2", "B3", "B4", "B5"])],
                             moods={**{f"A{i}": 5 for i in range(1, 6)},
                                    **{f"B{i}": 5 for i in range(1, 6)}})
        self.assertIn("优先级④", detail)
        self.assertEqual(q(s.mood_at("板凳甲", D(1))), D("10"))     # 没进宿舍 → 平线

    def test_被换出者变成不在基建(self):
        """被换出的那位：**既不工作也不在宿舍** ⇒ 她的心情也是一条平线（不参与任何计数）。"""
        s, _detail = self._跑([("宿舍#1", ["A1", "A2", "A3", "A4", "A5"]),
                               ("宿舍#2", ["B1", "B2", "B3", "B4", "B5"]),
                               ("宿舍#3", ["C1", "C2", "C3", "C4", "C5"]),
                               ("宿舍#4", ["D1", "D2", "D3", "D4", "D5"])])
        # D2 被换出来（她本来满心情 24 → 闲置不掉心情，整段平线）
        self.assertEqual(q(s.mood_at("D2", D(1))), D("24"))
        self.assertEqual(q(s.mood_at("D2", D(12))), D("24"))
        # 而进来的人（板凳甲）在宿舍里按 4/h 恢复
        self.assertEqual(q(s.mood_at("板凳甲", D(1))), D("14"))

    def test_兜底只换白板_有阵营的不动(self):
        """**优先级③只肯换白板**：② 命不中、宿舍里满心情的又全"有阵营" → **不换**。

        规则（用户口径）：兜底层只换**不属于任何已记录阵营**的满 24 心情干员；
        连白板都没有 → 这一班不换（宁可闲置者不进宿舍，也不拿有阵营联动的人去填坑）。
        ⚠️ 布局要让"①② 命不中"：宿舍全部**住满 5 人**（① 没空位可进），
        且**第 2~5 位的人心情都不满**（② 只吃第 2~5 位、且要求实时满 24）。
        """
        _s, detail = self._跑([("宿舍#1", ["菲亚梅塔", "普1a", "普1b", "普1c", "普1d"]),
                               ("宿舍#2", ["缪尔赛思", "普2a", "普2b", "普2c", "普2d"]),
                               ("宿舍#3", ["塞雷娅", "普3a", "普3b", "普3c", "普3d"]),
                               ("宿舍#4", ["赫默", "普4a", "普4b", "普4c", "普4d"])],
                              moods={n: 5 for n in
                                     ["菲亚梅塔", "缪尔赛思", "塞雷娅", "赫默"]
                                     + [f"普{i}{c}" for i in (1, 2, 3, 4) for c in "abcd"]})
        self.assertIn("优先级④", detail)
        self.assertNotIn("互换", detail)

    def test_兜底换白板而不是有阵营的(self):
        """③ 命中白板：**优先换白板**（哪怕"有阵营"的人位次更靠前、宿舍序号更靠后）。"""
        # 宿舍#4 第 1 位是白板（阿米娅）且满心情；宿舍#1~#3 的第 1 位都是"有阵营"、不满
        _s, detail = self._跑([("宿舍#1", ["菲亚梅塔", "普1a", "普1b", "普1c", "普1d"]),
                               ("宿舍#2", ["缪尔赛思", "普2a", "普2b", "普2c", "普2d"]),
                               ("宿舍#3", ["塞雷娅", "普3a", "普3b", "普3c", "普3d"]),
                               ("宿舍#4", ["阿米娅", "普4a", "普4b", "普4c", "普4d"])],
                              moods={n: 5 for n in
                                     ["菲亚梅塔", "缪尔赛思", "塞雷娅"]
                                     + [f"普{i}{c}" for i in (1, 2, 3, 4) for c in "abcd"]})
        self.assertIn("优先级③", detail)
        self.assertIn("阿米娅", detail)

    def test_轨迹带着每段实际用的布局(self):
        """`Trajectory.world_at(t)`：界面显示"她这一刻在哪"必须读它（引擎那份 = 模拟副本）。

        两份账的区别：`schedule.shifts[i].world` 是**排班快照**（进驻事件的位置互换、
        闲置入宿的换人都**不改它**）；`world_at(t)` 给的是**引擎实际用的副本**。
        这条钉住三件事：① 段信息齐全；② 边界取右侧（与 `mood_at`/`rate_at` 同口径）；
        ③ 闲置入宿换过人之后，两份账**确实不一样**（否则界面就没必要改）。
        """
        from store.schedule import Schedule, Shift

        dorm = ["阿米娅", "德克萨斯", "Mon3tr", "澄闪", "伊内丝"]
        sch = Schedule([
            Shift("A", D("12"), [{"type": "制造站", "level": 3, "operators": ["普通甲", "普通乙"]},
                                 {"type": "宿舍", "name": "宿舍#1", "level": 5,
                                  "operators": dorm}]),
            Shift("B", D("12"), [{"type": "制造站", "level": 3,
                                  "operators": ["泡泡", "普通甲", "普通乙"]},
                                 {"type": "宿舍", "name": "宿舍#1", "level": 5,
                                  "operators": dorm}]),
        ], D("24"))
        s = Session()
        s.schedule = sch
        s.set_initial_moods({"泡泡": 10, **{n: 24 for n in dorm}})
        s.idle_to_dorm = True
        s.recompute()
        tr = s.traj
        # ① 段信息：两个班次段
        self.assertEqual(len(tr.segments), 2)
        self.assertEqual(tr.segments[0][:2], (D(0), D(12)))
        self.assertEqual(tr.segments[1][:2], (D(12), D(24)))
        # ② 边界取右侧
        self.assertIs(tr.world_at(D("11.99")), tr.segments[0][2])
        self.assertIs(tr.world_at(D("12")), tr.segments[1][2])
        # ③ 第 1 班把泡泡换进了宿舍（引擎），而快照里她还是"未排班"
        self.assertIsNotNone(tr.world_at(D(1)).facility_of("泡泡"))
        self.assertIsNone(sch.shifts[0].world.facility_of("泡泡"))
        # 第 2 班她自己上班 → 快照与引擎一致（她就是那个班的在岗人）
        self.assertIsNotNone(tr.world_at(D(13)).facility_of("泡泡"))
        # 每个班次段给的都必须是"这一班"的副本（房间结构跟着班次走）
        for a, b, w in tr.segments:
            self.assertTrue(w.facilities)

    def test_换心情先于闲置入宿且用换后的实时心情(self):
        """**顺序与实时性**：换心情先结算，闲置入宿用**换完之后**的实时心情判优先级。

        菲亚梅塔（M15a 患难之交）与宿舍里某人互换心情后，两人的心情**当场变化** ——
        闲置入宿必须看到变化后的值（否则会挑一个"其实已经不满"的人来换 / 漏掉刚变满的人）。
        """
        facs = [{"type": "制造站", "level": 3, "operators": ["普通甲", "普通乙"]},
                {"type": "宿舍", "name": "宿舍#1", "level": 5,
                 "operators": ["菲亚梅塔", "路人乙", "路人丙", "路人丁", "路人戊"]}]
        s = Session()
        s.load_layout({"facilities": facs}, hours=24)
        # 让 路人乙 没满（5）：菲亚梅塔满心情进驻 → 与「前一位进驻」互换…… 这里直接指定对象
        s.set_initial_moods({"菲亚梅塔": 24, "路人乙": 5, "板凳甲": 10})
        s.set_detached(["板凳甲"], recompute=False)
        s.entry_events = True
        s.entry_swap_with = "路人乙"
        s.entry_when = "immediate"
        s.entry_scope = "dorm"
        s.idle_to_dorm = True
        s.recompute()
        # 换完之后 菲亚梅塔 = 5（不满）→ 她不该再被当成"满心情可换出"的对象
        self.assertEqual(q(s.mood_at("菲亚梅塔", D(0))), D("5"))
        self.assertNotIn("菲亚梅塔", " / ".join(m.label for m in s.traj.marks if m.kind == "idle"))

    def test_自回型干员不被换出宿舍(self):
        """**有宿舍自身回复技能的人（菲亚梅塔「自律」）不当"备用容量"被换出去。**

        踩过的 bug：她在宿舍里自己就能 +2/h 回满，却因为"满心情 + 坐在宿舍"被闲置入宿
        当成可换出的容量 —— 换出去之后她变成闲置、心情一条平线（看起来像"心情卡住不动"），
        而且白白丢掉一份恢复能力。

        本用例造的场景只差一个变量：宿舍里**除了她**没有别的满心情的人 ——
        修之前她会（作为③兜底的最后一位）被换出，修之后这一班应当走优先级④"不动"。
        """
        facs = [{"type": "制造站", "level": 3, "operators": ["普通甲", "普通乙"]},
                {"type": "宿舍", "name": "宿舍#1", "level": 5,
                 "operators": ["菲亚梅塔", "路人乙", "路人丙", "路人丁", "路人戊"]}]
        s = Session()
        s.load_layout({"facilities": facs}, hours=24)
        # 除她之外宿舍里全是没满的人；她是唯一的"满心情"
        s.set_initial_moods({"菲亚梅塔": 24, "路人乙": 5, "路人丙": 6,
                             "路人丁": 7, "路人戊": 8, "板凳甲": 10})
        s.set_detached(["板凳甲"], recompute=False)
        s.idle_to_dorm = True
        s.recompute()
        detail = " / ".join(m.label for m in s.traj.marks if m.kind == "idle")
        self.assertNotIn("菲亚梅塔", detail, "自回型干员不该被换出宿舍")
        self.assertIn("优先级④", detail)
        self.assertEqual(q(s.mood_at("板凳甲", D(1))), D("10"))     # 没入宿 → 平线
        self.assertEqual(q(s.mood_at("菲亚梅塔", D(1))), D("24"))    # 她留在宿舍（已是满心情）
        self.assertEqual(q(s.rate_at("菲亚梅塔", D("0.5"))), D("0"))  # 满心情 → 速率 0

    def test_自回型干员自己仍能入宿(self):
        """保护只作用于"**被选为被换出者**"：她自己是闲置候选时照旧能进宿舍恢复。"""
        facs = [{"type": "制造站", "level": 3, "operators": ["普通甲", "普通乙"]},
                {"type": "宿舍", "name": "宿舍#1", "level": 5,
                 "operators": ["路人甲", "路人乙", "路人丙", "路人丁", "路人戊"]}]
        s = Session()
        s.load_layout({"facilities": facs}, hours=24)
        s.set_initial_moods({"菲亚梅塔": 10,
                             **{n: 24 for n in ("路人甲", "路人乙", "路人丙", "路人丁", "路人戊")}})
        s.set_detached(["菲亚梅塔"], recompute=False)
        s.idle_to_dorm = True
        s.recompute()
        # 宿舍全满、全员满心情 → 她被安排进某个位次，并靠「自律」+2/h 上升
        self.assertEqual(q(s.rate_at("菲亚梅塔", D("0.5"))), D("-2"))
        self.assertEqual(q(s.mood_at("菲亚梅塔", D(1))), D("12"))



class Test不在基建(unittest.TestCase):
    """**「不在基建」的人**（既不在工作设施、也不在宿舍）：平线 + 不参与技能计数。

    他们由两处产生：
      - **自动**：整个排班都没排到位置的人，或"某一班没排到"的人（面板那一段会自动列）；
      - **显式**：场景 JSON 顶层 `"detached": [...]`（名单里的人）。
    数值上他们**心情整段恒定**（不消耗也不回复），且**不进任何技能计数**
    （计数读的是 `world.facilities` 里的进驻者）。
    """

    LAYOUT = {"facilities": [
        {"type": "控制中枢", "level": 5, "operators": ["玛恩纳", "重岳", "维什戴尔",
                                                      "路人1", "路人2"]},
        {"type": "制造站", "level": 3, "operators": ["泡泡", "黍", "路人甲"]},
    ], "detached": ["板凳甲", "板凳乙"]}

    def test_名单里的图线是平线且心情可设(self):
        s = Session()
        s.load_layout(self.LAYOUT)
        self.assertEqual(s.bench_names(), ["板凳甲", "板凳乙"])
        self.assertIn("板凳甲", s.traj.names)
        s.set_initial_mood("板凳甲", 7)
        s.recompute()
        for t in (0, 6, 12, 24, 48):
            self.assertEqual(q(s.mood_at("板凳甲", D(t))), D("7"), f"@{t}")
        self.assertEqual(s.rate_at("板凳甲", D(12)), D("0"))

    def test_不影响别人(self):
        """加/不加「不在基建」名单，**其它人的数值一字不变**（含技能计数类技能）。"""
        with_bench = Session()
        with_bench.load_layout(self.LAYOUT)
        without = Session()
        plain = dict(self.LAYOUT)
        plain.pop("detached")
        without.load_layout(plain)
        for name in ("泡泡", "黍", "路人甲", "路人1", "玛恩纳"):
            for t in (D("0"), D("8"), D("24")):
                self.assertEqual(q(with_bench.mood_at(name, t)), q(without.mood_at(name, t)),
                                 f"{name}@{t}")
        # 净速率也一样（重岳「孤光共照」按"岁"计数：板凳上的人不该被数进去）
        for name in ("泡泡", "玛恩纳"):
            self.assertEqual(with_bench.rate_at(name, D(0)), without.rate_at(name, D(0)), name)

    def test_不在基建的人不进基建计数(self):
        """把一个人放到「不在基建」= **同时从进驻位上摘下来**，于是不再被任何计数数到。"""
        s = Session()
        s.load_layout({"facilities": [
            {"type": "控制中枢", "level": 5,
             "operators": ["陈", "星熊", "诗怀雅", "路人1", "路人2"]},
        ]})
        before = s.rate_at("陈", D(0))          # 龙门近卫局 3 人 → 中枢全体回复 0.15
        s.add_detached("路人1")                 # 默认连位置一起摘（只是改状态）
        s.recompute()
        after = s.rate_at("陈", D(0))
        self.assertEqual(s.bench_names(), ["路人1"])
        # 中枢只剩 4 人：减免从 0.25 降到 0.20，且陈的「德才兼备」仍按 3 名龙门算 → 速率变了
        self.assertNotEqual(before, after)
        # 摘下来的人不再出现在任何房间的进驻列表里（count_of_type/base_operators 也数不到）
        world = s.schedule.shifts[0].world
        self.assertIsNone(world.facility_of("路人1"))
        self.assertNotIn("路人1", [o.name for o in world.base_operators()])
        self.assertEqual(q(s.mood_at("路人1", D(12))), q(s.mood_at("路人1", D(0))))

    def test_直接设名单也会摘位置(self):
        """`set_detached` 与 `add_detached` 必须**同一条路**：名单里的人不占位置、心情平线。

        （踩过：只有 `add_detached` 摘位置，于是"导入带名单的文件"与"直接设名单"
        给出不同数值 —— 名单里的人一边在名单里、一边在房间里有消耗。）
        """
        s = Session()
        s.load_layout({"facilities": [
            {"type": "制造站", "level": 3, "operators": ["泡泡", "黍", "路人甲"]},
        ]})
        s.set_detached(["泡泡"], recompute=True)
        self.assertEqual(s.bench_names(), ["泡泡"])
        self.assertIsNone(s.schedule.shifts[0].world.facility_of("泡泡"))
        self.assertNotIn("泡泡", [o.name for o in s.schedule.shifts[0].world.all_operators()])
        for t in (0, 6, 24):
            self.assertEqual(q(s.mood_at("泡泡", D(t))), q(s.mood_at("泡泡", D(0))))
        self.assertEqual(s.rate_at("泡泡", D(12)), D("0"))

    def test_名单里的人也能被闲置入宿安排进宿舍(self):
        """**「不在基建」名单里的人**同样进闲置入宿的候选；进宿舍后当班按宿舍回复。

        这条是"引擎早就支持、但没人写过"的路：`apply_idle_to_dorm` 对"不在布局里的人"
        会现造 `Operator` 再放进宿舍（`op is None` 分支）。实测：心情 10 → 1h 后 14 →
        6h 满（宿舍 Lv5 满氛围 4/h）。
        """
        s = Session()
        s.load_layout({"facilities": [
            {"type": "制造站", "level": 3, "operators": ["普通甲", "普通乙", "普通丙"]},
            {"type": "宿舍", "level": 5, "operators": []},
        ]}, hours=24)
        s.set_initial_moods({"板凳甲": 10})
        s.set_detached(["板凳甲"], recompute=True)     # 摘位置 + 进名单
        # ① 没开闲置入宿：平线（心情 10 不动）
        self.assertEqual(q(s.mood_at("板凳甲", D("6"))), D("10"))
        self.assertEqual(s.rate_at("板凳甲", D("0")), D("0"))
        # ② 开了闲置入宿：她进宿舍恢复
        s.idle_to_dorm = True
        s.recompute()
        rows = [r for _t, _sc, rs in s.idle_groups() for r in rs if r[0] == "板凳甲"]
        self.assertTrue(rows, "名单里的人应当出现在闲置入宿的候选里")
        self.assertEqual(rows[0][2], "不在基建")       # 位置标记
        self.assertEqual(q(s.mood_at("板凳甲", D("0"))), D("10"))
        self.assertAlmostEqual(float(s.mood_at("板凳甲", D("1"))), 14.0, places=6)
        self.assertEqual(q(s.mood_at("板凳甲", D("6"))), D("24"))   # 4/h × 3.5h 回满
        self.assertEqual(q(s.rate_at("板凳甲", D("1"))), D("-4"))   # 宿舍 Lv5 满氛围

    def test_JSON往返(self):
        """导出场景 → 再导入：`detached` 原样回来（键与 `facilities` 同层、向后兼容）。"""
        from api.ops import handle
        s = Session()
        handle(s, "load_schedule", {"facilities": [
            {"type": "制造站", "level": 3, "operators": ["泡泡"]}],
            "detached": ["板凳甲"]})
        ex = handle(s, "export_schedule")
        scenario = ex["shifts"][0]["scenario"]
        self.assertEqual(scenario["detached"], ["板凳甲"])
        s2 = Session()
        handle(s2, "load_schedule", scenario)
        self.assertEqual(s2.bench_names(), ["板凳甲"])
        self.assertIn("板凳甲", s2.operator_names())

    def test_老文件没有这个键(self):
        """**向后兼容**：场景 JSON 里没有 `detached` ⇒ 空名单，一切照旧。"""
        s = Session()
        s.load_layout({"facilities": [
            {"type": "制造站", "level": 3, "operators": ["泡泡", "黍", "路人甲"]}]})
        self.assertEqual(s.detached, [])
        self.assertEqual(s.bench_names(), [])          # 全员都在岗 → 没有"不在基建"的人
        self.assertEqual(s.not_in_shift(0), [])

    def test_某班没排到的人自动算不在基建(self):
        """多班里"本班没排到位置"的人，被 `not_in_shift` 自动列出（界面那一段的来源）。"""
        from store.schedule import Schedule, Shift
        shifts = [
            Shift("A", D("12"), [{"type": "制造站", "level": 3,
                                  "operators": ["泡泡", "黍", "路人甲"]}]),
            Shift("B", D("12"), [{"type": "制造站", "level": 3,
                                  "operators": ["泡泡", "黍", "路人乙"]}]),
        ]
        s = Session()
        s.schedule = Schedule(shifts, D("24"))
        s.recompute()
        self.assertEqual(s.not_in_shift(0), ["路人乙"])     # 第 1 班缺路人乙
        self.assertEqual(s.not_in_shift(1), ["路人甲"])     # 第 2 班缺路人甲
        # 两个班都不在的人（显式名单）才算"整份排班的不在基建"
        self.assertEqual(s.bench_names(), [])


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

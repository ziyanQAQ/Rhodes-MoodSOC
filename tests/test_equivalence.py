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
from store.schedule import load_schedule, simulate_schedule, MoodSetEvent        # noqa: E402
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
    | ③ | ②找不到 | 退到「宿舍里其余任何位置」的实时满心情、**"吃不到联动"的人**（白板，或阵营在**工作区**里没有同伴）＋满 24 的菲亚梅塔 |
    | ④ | 都找不到 | 有点名 → 与点名的那位"主动换"；没点名 → 与宿舍里**心情最高**的同类人换（不限 24）；连这样的都没有 → 不动 |

    ②③④ 的自动换人还都**跳过"挂件"**（`rules._is_pendant`：她一走别人就要吃亏 ——
    用户口径"有阵营效果，或者她在不在宿舍会影响其他干员的技能"）；**点名不受限**。
    ⚠️ **阵营门（2026-09 用户口径）**：`rules._faction_protected` —— 有阵营的人**只有在
    "工作区"（中枢/制造/贸易/发电/会客/办公）里有同阵营同伴时**才受保护；孤家寡人的阵营
    当白板看待、可以换出。在宿舍里休息的同伴**不算**。
    ⚠️ **"自回型不被换出"那道门已取消**（用户裁决 2026-09："在宿舍的自回型不进行门保护"）。

    几个容易写错、也最容易回归的点：**宿舍序号必须取自"未排序"的布局顺序**（否则"优先 #4"
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

    def test_第4级默认兜底_换宿舍里心情最高的白板(self):
        """**优先级④的默认兜底**（用户口径）：②③ 都挑不到**满 24** 的人时**不再直接不动**，
        而是与宿舍里**心情最高**的白板互换 —— **不限 24**（这里最高是 15，就跟 15 那位换）。

        "心情从高到低"＝换出损失最小的先换（同心情按名字）；宿舍顺序只用于同分。
        ⚠️ 仍要过"心情闸"：目标必须**比你（10）更满**（见下一个用例）。
        """
        s, detail = self._跑([("宿舍#1", ["A1", "A2", "A3", "A4", "A5"]),
                              ("宿舍#2", ["B1", "B2", "B3", "B4", "B5"])],
                             moods={**{f"A{i}": 5 for i in range(1, 6)},
                                    **{f"B{i}": 5 for i in range(1, 6)},
                                    "B4": 15})
        self.assertIn("优先级④的默认兜底", detail)
        self.assertIn("B4", detail)                                # 全宿舍心情最高的白板
        self.assertEqual(q(s.mood_at("板凳甲", D(1))), D("14"))      # 进宿舍 → 4/h 恢复
        self.assertEqual(q(s.mood_at("B4", D(1))), D("15"))          # 换出来 → 平线

    def test_目标心情不比候选更满就不换(self):
        """**心情闸**（用户口径）：任何互换都要求**目标的实时心情严格大于候选** ——
        换人是"拿一个人的宿舍位子换给另一个人"，目标不比候选更满时等于把**更需要恢复的那位挤出去**。

        实测过的场景：示例排班里 `梅 19.5`（闲置）想换 `温蒂 12.3`（在宿舍）——
        换进去的是没那么需要的、换出来的是更需要的，净亏 ⇒ 现在改成"这一班不动"。
        """
        from mood_soc.rules import apply_idle_to_dorm
        from store.layout import build_base_layout

        def world(甲心情):
            facs = [{"type": "宿舍", "level": 5, "slots": 1,
                     "operators": [{"name": "甲", "mood": str(甲心情)}]},
                    {"type": "加工站", "level": 1, "operators": [{"name": "丙", "mood": "10"}]}]
            return build_base_layout({"facilities": facs})

        # 目标更满（12 > 10）→ 照换
        w = world(12)
        events = apply_idle_to_dorm(w, enabled=True)
        self.assertEqual([e.group for e in events], ["idle_to_dorm"])
        self.assertEqual(events[0].target, "甲")
        # 目标更不满（8 < 10）→ 不换
        w2 = world(8)
        ev2 = apply_idle_to_dorm(w2, enabled=True)
        self.assertEqual([e.group for e in ev2], ["idle_to_dorm_skipped"])
        self.assertIn("目标并不比你更满", ev2[0].detail)
        self.assertEqual(w2.facility_of("丙").display_name, "加工站")
        self.assertEqual(w2.facility_of("甲").display_name, "宿舍")
        # **严格大于**：两边一样（10 == 10）也不换
        w3 = world(10)
        self.assertEqual([e.group for e in apply_idle_to_dorm(w3, enabled=True)],
                         ["idle_to_dorm_skipped"])

    def test_都不满足就这一班不动(self):
        """**优先级④的最后一道**：宿舍全满、②③ 挑不到满心情的人、
        ④ 的默认兜底挑到的那位**不比候选更满**（成员心情全是 5，候选 10）→ 心情闸拦下：
        不动，心情平线（+ 一条说明）。"""
        names = ["塞雷娅", "赫默", "伊芙利特", "诗怀雅", "齐尔查克",
                 "拉普兰德", "砾", "伺夜", "空弦", "娜斯提"]
        s, detail = self._跑([("宿舍#1", names[:5]), ("宿舍#2", names[5:])],
                             moods={n: 5 for n in names})
        self.assertIn("优先级④", detail)
        self.assertNotIn("互换", detail)
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

    def test_工作区没同阵营就当白板换出(self):
        """**阵营门（2026-09 用户口径）**：有阵营的人只有在**工作区里有同阵营同伴**时才受保护。

        这条钉**反面**：基建（制造站里只有白板 `普通甲/普通乙`）里没有同阵营的同伴 ⇒
        `塞雷娅`（莱茵生命）**吃不到任何阵营联动** ⇒ 当白板看待、被 ④ 兜底换出。
        （旧口径"有阵营就一律留在宿舍"在这里会让板凳甲进不去。）
        """
        from mood_soc.rules import _faction_protected

        s, detail = self._跑([("宿舍#1", ["菲亚梅塔", "普1a", "普1b", "普1c", "普1d"]),
                              ("宿舍#2", ["缪尔赛思", "普2a", "普2b", "普2c", "普2d"]),
                              ("宿舍#3", ["塞雷娅", "普3a", "普3b", "普3c", "普3d"]),
                              ("宿舍#4", ["赫默", "普4a", "普4b", "普4c", "普4d"])],
                             moods={n: 12 for n in
                                    ["菲亚梅塔", "缪尔赛思", "塞雷娅", "赫默"]
                                    + [f"普{i}{c}" for i in (1, 2, 3, 4) for c in "abcd"]})
        底本 = s.schedule.shifts[0].world
        self.assertFalse(_faction_protected(底本, 底本.get_operator("塞雷娅")),
                         "前提：工作区（制造站）里没有同阵营的同伴 ⇒ 她不受保护")
        self.assertIn("优先级④", detail)
        self.assertIn("塞雷娅", detail)                    # 她此刻是宿舍里心情最高、又吃不到联动的人
        self.assertGreater(q(s.mood_at("板凳甲", D(1))), D("10"))      # 进宿舍 → 开始恢复

    def test_工作区有同阵营就留在宿舍(self):
        """**阵营门**的正面：工作区里**有同阵营的同伴** ⇒ 她受保护、自动换人不碰她。

        夹具用显式的 `factions` 标注（`有阵营甲` 与制造站里的 `同伴甲` 同属"测试阵营"），
        这样钉的是**规则**而不是真实阵营表：②③④ 都挑不到她 ⇒ 换出的是纯白板 `路人乙`。
        """
        from mood_soc.rules import _faction_protected

        s = Session()
        s.load_layout({"facilities": [
            {"type": "制造站", "level": 3, "operators": [
                {"name": "同伴甲", "factions": ["测试阵营"]}]},
            {"type": "宿舍", "name": "宿舍#1", "level": 5, "slots": 2, "operators": [
                {"name": "有阵营甲", "mood": "24", "factions": ["测试阵营"]},
                {"name": "路人乙", "mood": "12"}]},
        ]}, hours=24)
        s.set_initial_moods({"板凳甲": 10})
        s.set_detached(["板凳甲"], recompute=False)
        s.idle_to_dorm = True
        s.recompute()
        detail = " / ".join(m.label for m in s.traj.marks if m.kind == "idle")
        底本 = s.schedule.shifts[0].world
        self.assertTrue(_faction_protected(底本, 底本.get_operator("有阵营甲")),
                        "前提：制造站里有同阵营的同伴 ⇒ 她受保护")
        self.assertNotIn("有阵营甲", detail, "受阵营门保护的人不该被自动换出")
        self.assertIn("路人乙", detail)                    # 换出的是纯白板
        self.assertGreater(q(s.mood_at("板凳甲", D(1))), D("10"))

    def test_兜底换白板而不是有阵营的(self):
        """③ 命中"吃不到联动"的人：**优先换白板**（哪怕"有阵营"的人位次更靠前、宿舍序号更靠后）。"""
        # 宿舍#4 第 1 位是白板（德克萨斯）且满心情；宿舍#1~#3 的第 1 位都是"有阵营"、不满
        _s, detail = self._跑([("宿舍#1", ["菲亚梅塔", "普1a", "普1b", "普1c", "普1d"]),
                               ("宿舍#2", ["缪尔赛思", "普2a", "普2b", "普2c", "普2d"]),
                               ("宿舍#3", ["塞雷娅", "普3a", "普3b", "普3c", "普3d"]),
                               ("宿舍#4", ["德克萨斯", "普4a", "普4b", "普4c", "普4d"])],
                              moods={n: 5 for n in
                                     ["菲亚梅塔", "缪尔赛思", "塞雷娅"]
                                     + [f"普{i}{c}" for i in (1, 2, 3, 4) for c in "abcd"]})
        self.assertIn("优先级③", detail)
        self.assertIn("德克萨斯", detail)

    def test_面板行序与引擎处理顺序一致(self):
        """「闲置入宿」面板里每个 (周期, 班次) 分组内的**行序＝引擎的安排顺序**（用户口径）：

        ① 先"不在工作也不在宿舍"的人（本班未排班 /「不在基建」名单），② 再挂件位（加工站/训练室）
        入驻者；每组内部按**心情从低到高**。候选是**依次**处理的，所以行序不能只是"按心情排"——
        否则表里排在前面的未必是引擎先安排的那位。
        """
        s = Session()
        s.load_layout({"facilities": [
            {"type": "宿舍", "level": 5, "slots": 1,
             "operators": [{"name": "丙", "mood": "24"}]},
            {"type": "加工站", "level": 1, "operators": [{"name": "甲", "mood": "5"}]},
        ]}, hours=24)
        s.set_initial_moods({"乙": 10, "丁": 3})
        s.set_detached(["乙", "丁"], recompute=True)      # 名单里的人＝"不在工作也不在宿舍"
        s.idle_to_dorm = True
        s.recompute()
        rows = [r for _t, _sc, rs in s.idle_groups() for r in rs]
        self.assertEqual([r[0] for r in rows], ["丁", "乙", "甲"],
                         "先未排班/不在基建（心情低→高），再挂件位入驻者")
        self.assertEqual([r[2] for r in rows], ["不在基建", "不在基建", "加工站"])

    def test_挂件不被自动换出(self):
        """**挂件**（用户口径："有阵营效果，或者她在不在宿舍会影响其他干员的技能"）
        自动换人一律不碰 —— ②③④ 都是。

        这里的挂件是**阿米娅**：她是白板，但「小提琴独奏」（`dorm_rec_all_010#1`）给同宿舍
        其他人各 +0.15/h 群体回复 —— 换出去别人就要吃亏。**只看变差这一侧**：她走了别人
        速率变大（负得少）才算挂件；反过来"她走了别人反而分得更多"（冰酿那种池分摊）不算。
        """
        from mood_soc.rules import _is_pendant

        # 宿舍满员：阿米娅满 24（本来会被 ③ 换出）、路人甲没满
        facs = self._布局([("宿舍#1", ["阿米娅", "路人甲"])])
        facs[1]["slots"] = 2                                  # 容量 2 → 住满
        s = Session()
        s.load_layout({"facilities": facs}, hours=24)
        s.set_initial_moods({"阿米娅": 24, "路人甲": 12, "板凳甲": 10})
        s.set_detached(["板凳甲"], recompute=False)
        s.idle_to_dorm = True
        s.recompute()
        detail = " / ".join(m.label for m in s.traj.marks if m.kind == "idle")
        底本 = s.schedule.shifts[0].world            # 判据看**换人前**那份布局
        self.assertTrue(_is_pendant(底本, "阿米娅"),
                        "阿米娅在宿舍里给别人 +0.15/h ⇒ 她是挂件")
        self.assertFalse(_is_pendant(底本, "路人甲"),
                         "路人甲是纯白板，不是挂件")
        self.assertNotIn("阿米娅", detail, "挂件不该被自动换出")
        self.assertIn("路人甲", detail)                        # ③ 落空 → ④ 换走心情 12 的白板
        self.assertIn("优先级④的默认兜底", detail)
        self.assertGreater(q(s.mood_at("板凳甲", D(1))), D("10"))
        self.assertEqual(q(s.mood_at("阿米娅", D(1))), D("24"))  # 她留在宿舍（已是满心情）

    def test_面板逐行候选按轮到她的那一刻算(self):
        """「闲置入宿」逐次表里每一行的「换谁」＝**轮到她的那一刻**宿舍里的人（引擎那份世界）。

        回归用户报的现象："面板里列着 清流、那一刻宿舍里并没有 清流"。根因：列表原先取的是
        **排班快照**，而候选是**依次**处理的 —— 排前面的人会把宿舍里的人换出去，
        轮到后面那位时，那人早就不在宿舍了（名单是"被换出"的对象，不是"当前住户"）。

        本例：宿舍满员（塞雷娅 24 有阵营、**制造站里有同阵营的同伴赫默** ⇒ 受阵营门保护、
        ③ 挑不到她；路人乙 12 是白板）；
        候选按心情升序 丁(5) → 戊(8)。
          ① 丁 走 ④ 兜底换走 心情最高的白板 **路人乙**；
          ② 轮到 戊 时，路人乙 已不在宿舍 ⇒ 她的「换谁」里**不该再有路人乙**，
             而该出现"刚被安排进去的 丁"。
        """
        from mood_soc.rules import _faction_protected, _factionless
        from store.session import idle_target_name

        s = Session()
        s.load_layout({"facilities": [
            {"type": "制造站", "level": 3, "operators": ["赫默"]},
            {"type": "宿舍", "level": 5, "slots": 2,
             "operators": [{"name": "塞雷娅", "mood": "24"},
                           {"name": "路人乙", "mood": "12"}]},
        ]}, hours=24)
        s.set_initial_moods({"丁": 5, "戊": 8})
        s.set_detached(["丁", "戊"], recompute=True)
        s.idle_to_dorm = True
        s.recompute()
        底本 = s.schedule.shifts[0].world                      # 前提：钉住这道闸的走向
        self.assertFalse(_factionless(底本.get_operator("塞雷娅")),
                         "前提：塞雷娅有阵营")
        self.assertTrue(_faction_protected(底本, 底本.get_operator("塞雷娅")),
                        "前提：工作区（制造站）里有同阵营的赫默 ⇒ ③ 挑不到她")
        self.assertTrue(_factionless(底本.get_operator("路人乙")),
                        "前提：路人乙是白板 ⇒ ④ 兜底会换走她")

        title, scope, rows = s.idle_groups()[0]
        self.assertEqual(scope, (1, 1))
        self.assertEqual([r[0] for r in rows], ["丁", "戊"], "行序＝引擎处理顺序（心情低→高）")
        opts = {r[0]: [idle_target_name(o) for o in r[5] if not o.startswith("宿舍")]
                for r in rows}
        self.assertIn("路人乙", opts["丁"], "丁 那一刻路人乙还在宿舍（她正是被换出的那位）")
        self.assertNotIn("路人乙", opts["戊"], "轮到戊时路人乙已被换出宿舍 ⇒ 不该再出现在候选里")
        self.assertIn("丁", opts["戊"], "戊 那一刻看到的是「已经被安排进宿舍的丁」")
        # 不变式：每一行的候选 == 引擎**轮到这一位**时那份宿舍态里的住户（去掉自己）
        for name, mood, _where, _use, _target, row_opts in rows:
            state = s.traj.idle_state_at(0, name)
            self.assertIsNotNone(state, f"{name} 应当有引擎逐位快照")
            want = {who for names in state["dorms"].values() for who in names if who != name}
            got = {idle_target_name(o) for o in row_opts if not o.startswith("宿舍")}
            self.assertEqual(got, want, f"{name} 的候选必须与那一刻的宿舍住户一致")
            self.assertEqual([o for o in row_opts if o.startswith("宿舍")],
                             [f"宿舍{no:02d}" for no in state["free"]])
        # 引擎侧同一件事：丁 真的换走了路人乙（面板说的和引擎做的是一回事）
        detail = " / ".join(m.label for m in s.traj.marks if m.kind == "idle")
        self.assertIn("丁", detail)
        self.assertIn("路人乙", detail)
        self.assertIn("优先级④", detail)

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

    def test_自回型也会被换出宿舍(self):
        """**"自回型不被换出"那道门已取消**（用户裁决 2026-09："在宿舍的自回型不进行门保护"）。

        旧口径：有宿舍自身回复技能的人（菲亚梅塔「自律」、缪尔赛思「天生丽质」）不当
        "备用容量"被换出去 —— 理由是她自己就能回满、换出去白丢一份恢复能力。
        新口径：**照换**。这里把满 24 的菲亚梅塔放在**第 2 间宿舍的第 2 位**（② 的范围内）
        ⇒ 引擎应当**在 ② 就把她换出来**（旧口径会跳过她、退到 ③ 才因例外名单放行）。
        """
        facs = [{"type": "制造站", "level": 3, "operators": ["普通甲", "普通乙"]},
                {"type": "宿舍", "name": "宿舍#1", "level": 5,
                 "operators": ["路人甲", "路人乙", "路人丙", "路人丁", "路人戊"]},
                {"type": "宿舍", "name": "宿舍#2", "level": 5,
                 "operators": ["路人己", "菲亚梅塔", "路人庚", "路人辛", "路人壬"]}]
        s = Session()
        s.load_layout({"facilities": facs}, hours=24)
        s.set_initial_moods({"菲亚梅塔": 24, "板凳甲": 10,
                             **{n: 5 for n in ("路人甲", "路人乙", "路人丙", "路人丁", "路人戊")},
                             **{n: 8 for n in ("路人己", "路人庚", "路人辛", "路人壬")}})
        s.set_detached(["板凳甲"], recompute=False)
        s.idle_to_dorm = True
        s.recompute()
        detail = " / ".join(m.label for m in s.traj.marks if m.kind == "idle")
        self.assertIn("菲亚梅塔", detail, "自回型现在也会被换出")
        self.assertIn("优先级②", detail, "满 24 + 在宿舍#2 第 2 位 ⇒ ② 直接命中")
        self.assertGreater(q(s.mood_at("板凳甲", D(1))), D("10"))     # 进宿舍 → 开始恢复
        self.assertEqual(q(s.mood_at("菲亚梅塔", D(1))), D("24"))      # 满心情换出来 → 平线不掉

    def test_满24的菲亚梅塔在兜底层被放行(self):
        """③ 兜底的**例外**：满 24 的菲亚梅塔可以被换出（用户裁决）。

        她的价值全在「患难之交」（M15a）——**进驻宿舍那一刻**把心情换给上一位，满 24 就够、
        不靠"待在宿舍里"；「自律」又会自己回满 ⇒ 把她当"备用容量"换出去不亏。
        所以这一班**不再走④"不动"**：板凳甲进宿舍恢复，她换出来闲置（已是满心情 → 平线）。
        """
        facs = [{"type": "制造站", "level": 3, "operators": ["普通甲", "普通乙"]},
                {"type": "宿舍", "name": "宿舍#1", "level": 5,
                 "operators": ["菲亚梅塔", "路人乙", "路人丙", "路人丁", "路人戊"]}]
        s = Session()
        s.load_layout({"facilities": facs}, hours=24)
        s.set_initial_moods({"菲亚梅塔": 24, "路人乙": 5, "路人丙": 6,
                             "路人丁": 7, "路人戊": 8, "板凳甲": 10})
        s.set_detached(["板凳甲"], recompute=False)
        s.idle_to_dorm = True
        s.recompute()
        detail = " / ".join(m.label for m in s.traj.marks if m.kind == "idle")
        self.assertIn("菲亚梅塔", detail, "满 24 的菲亚梅塔应当在 ③ 被放行")
        self.assertIn("优先级③", detail)
        self.assertGreater(q(s.mood_at("板凳甲", D(1))), D("10"))     # 进宿舍 → 开始恢复
        self.assertEqual(q(s.mood_at("菲亚梅塔", D(1))), D("24"))      # 满心情换出来 → 平线不掉

    def test_自回型干员自己仍能入宿(self):
        """自回型**自己是闲置候选**时照旧能进宿舍恢复（与"谁被换出"是两回事）。"""
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



class Test线程与精度(unittest.TestCase):
    """**同一份输入，在任何线程里都必须算出逐位相同的轨迹**。

    为什么单独立一条：`decimal` 的上下文是**线程局部**的，`mood_soc/config.py` 里那两行
    （`prec=28` + `ROUND_HALF_UP`）只设到"导入它的那个线程"。界面的异步重算（P5）把引擎丢进
    后台线程 —— 新线程默认是 `ROUND_HALF_EVEN`，末位差 1e-26，再经"跨阈值吸附"连锁改变事件
    时刻。实测**同一份输入**主线程与工作线程在 52 名干员的轨迹上不同（节点数一样、数值末位不同），
    所以引擎入口必须显式重设上下文（`config.use_project_decimal_context`）。
    """

    def test_主线程与工作线程逐位一致(self):
        import threading

        from data.paths import MAA_SAMPLE
        from store.session import Session

        s = Session()
        s.load_paths([MAA_SAMPLE])
        s.set_cycles(2)
        s.set_initial_mood("菲亚梅塔", Decimal("3"))
        s.recompute()
        main_traj = s.compute_trajectory(s.recompute_inputs())

        box = {}

        def work():
            box["traj"] = Session.compute_trajectory(s.recompute_inputs())

        th = threading.Thread(target=work, name="test-recalc")
        th.start()
        th.join()
        other = box["traj"]

        self.assertEqual(list(main_traj.times), list(other.times), "事件节点必须逐位相同")
        diff = [n for n in main_traj.moods
                if list(main_traj.moods[n]) != list(other.moods[n])]
        self.assertEqual(diff, [], "工作线程算出来的轨迹必须与主线程逐位一致")

    def test_引擎入口会重设本线程的上下文(self):
        """直接验证机制本身：把当前线程的上下文故意改坏，引擎入口应当把它设回来。"""
        import threading
        from decimal import ROUND_DOWN, getcontext

        from data.paths import MAA_SAMPLE
        from mood_soc.config import (DECIMAL_PREC, DECIMAL_ROUNDING,
                                     use_project_decimal_context)
        from store.session import Session

        box = {}

        def work():
            getcontext().prec = 6                      # 故意改坏
            getcontext().rounding = ROUND_DOWN
            s = Session()
            s.load_paths([MAA_SAMPLE])
            s.set_cycles(1)
            s.recompute()                              # 引擎入口应当先重设上下文
            box["ctx"] = (getcontext().prec, getcontext().rounding)

        th = threading.Thread(target=work, name="test-ctx")
        th.start()
        th.join()
        self.assertEqual(box["ctx"], (DECIMAL_PREC, DECIMAL_ROUNDING))
        use_project_decimal_context()                  # 收尾：把当前线程设回规范值


class Test挂件判据探针范围(unittest.TestCase):
    """**P4-3**：挂件判据的探针范围从"全基建逐个复核"缩到
    「她同设施的人 ∪ 依赖他人的技能持有者」（`rules._pendant_probe_names`）。

    为什么能缩：实测真值（摘掉她之后真的变差的人）始终落在这两类里 ——
    ① 宿舍回复/氛围·人数减免/同设施点名这类机制**都按设施计数**；
    ② 剩下的是**条件里读跨设施计数**的技能（如「潮汐守望」按全宿舍/宿舍外的深海猎人计数），
    它们的持有人由 `_dependent_holders` 兜住。
    因此这里钉两件事：窄探针与"全基建逐个复核"的老口径**逐条一致**（同种子随机世界），
    以及那个跨设施条件的持有人**确实进了探针名单**（只留"同设施"就会漏）。
    """

    SAMPLES = 60          # 随机世界个数（同种子 → 确定性）

    @staticmethod
    def _old_pendant(world, name) -> bool:
        """改造前的老口径：全基建逐个复核（对照用，原样保留）。"""
        from mood_soc import rules as R
        from mood_soc.config import FacilityType
        from mood_soc.rules import compute_net_rate

        fac = world.facility_of(name)
        if fac is None:
            return False
        if fac.ftype in (FacilityType.WORKSHOP, FacilityType.TRAINING):
            return True
        if any(o.name == name for o in fac.deputies):
            return True
        if fac.ftype != FacilityType.DORMITORY:
            return False
        before = {o.name: compute_net_rate(world, o.name) for o in world.all_operators()}
        probe = R._world_without(world, fac, name)
        for other, val in before.items():
            if other != name and compute_net_rate(probe, other) > val:
                return True
        return False

    def _random_worlds(self):
        """从示例排班造随机世界：随机挑一间宿舍，换上随机真人住客（含"依赖他人"技能的持有者）。"""
        import copy as _copy
        import random

        from data.paths import MAA_SAMPLE
        from mood_soc import rules as R
        from mood_soc.config import FacilityType
        from mood_soc.scenario import build_operator
        from store.session import Session

        s = Session()
        s.load_paths([MAA_SAMPLE])
        s.recompute()
        pool = list(s.operator_names())
        hot = list(R._dependent_holders(s.traj.segments[0][2]))
        rnd = random.Random(5)
        out = []
        for _ in range(self.SAMPLES):
            _a, _b, w = s.traj.segments[rnd.randrange(len(s.traj.segments))]
            w = _copy.deepcopy(w)
            dorms = [f for f in w.facilities if f.ftype == FacilityType.DORMITORY]
            if not dorms:
                continue
            dorm = rnd.choice(dorms)
            names = rnd.sample(hot, min(3, len(hot))) if hot else []
            rest = [n for n in pool if n not in names]
            names += rnd.sample(rest, max(0, 5 - len(names)))
            # ⚠️ 重名会让她同时落在工作设施与宿舍（`facility_of` 命中前一个）→ 先把同名摘干净
            for f in w.facilities:
                if f is dorm:
                    continue
                f.operators = [o for o in f.operators if o.name not in names]
                f.deputies = [o for o in f.deputies if o.name not in names]
            dorm.operators = [build_operator({"name": n, "mood": str(rnd.randint(3, 24))})
                              for n in names]
            w.invalidate_index()
            out.append((w, names))
        return out

    def test_窄探针与全探针逐条一致(self):
        from mood_soc.rules import _is_pendant_uncached

        n = pos = 0
        for w, names in self._random_worlds():
            memo = {}
            for x in names:
                if w.facility_of(x) is None:
                    continue
                new = _is_pendant_uncached(w, x, memo)
                old = self._old_pendant(w, x)
                n += 1
                pos += 1 if old else 0
                self.assertEqual(new, old, f"{x}：窄探针 {new} ≠ 全探针 {old}")
        self.assertGreater(n, 100, "样本太少，钉不住口径")
        self.assertGreater(pos, 10, "样本里几乎没判成挂件 ⇒ 等价性没验到东西")

    def test_跨设施条件的人也在探针名单里(self):
        """宿舍里的深海猎人会影响**别的宿舍**里「潮汐守望」持有者的条件 ⇒ 必须进探针名单。"""
        from data.paths import MAA_SAMPLE  # noqa: F401  （只为与其他用例同源）
        from mood_soc.rules import _pendant_probe_names
        from store.layout import build_base_layout

        world = build_base_layout({"facilities": [
            {"type": "宿舍", "level": 5, "operators": ["乌尔比安", "路人乙"]},
            {"type": "宿舍", "level": 5, "operators": ["歌蕾蒂娅", "路人丙"]},
        ]})
        fac = world.facility_of("乌尔比安")
        probe = _pendant_probe_names(world, fac, "乌尔比安", {})
        self.assertIn("歌蕾蒂娅", probe,
                      "「潮汐守望」的条件读的是全宿舍/宿舍外的深海猎人 ⇒ 她在别的宿舍也必须复核")
        self.assertNotIn("乌尔比安", probe, "探针名单里不该有她自己")

    def test_位置挂件与非宿舍成员不受探针范围影响(self):
        """前四行判定（位置）与探针范围无关：加工站＝挂件、正在上班＝不是。"""
        from mood_soc.rules import _is_pendant_uncached
        from store.layout import build_base_layout

        world = build_base_layout({"facilities": [
            {"type": "加工站", "level": 1, "operators": ["路人甲"]},
            {"type": "制造站", "level": 3, "operators": ["路人丁"]},
            {"type": "宿舍", "level": 5, "operators": ["路人乙"]},
        ]})
        self.assertTrue(_is_pendant_uncached(world, "路人甲", {}))
        self.assertFalse(_is_pendant_uncached(world, "路人丁", {}))


class Test增量重算(unittest.TestCase):
    """**P6a/P6b**：按"逐段输入指纹"从**第一个变了的段**续算（周期数变少＝只截断），
    结果必须与"从头全量算"**逐位相同**。

    为什么值得单独立一条：增量一旦把"段首事件之前的种子"取错（比如用 `mood_at(t0)` 拿到跳变**后**
    的值），段首的进驻事件就会被算两遍；而"切点算早了/算晚了"分别意味着数值出错与白算。所以这里
    既逐位比对，又钉住"切点落在哪一段"。
    """

    @staticmethod
    def _engine_reference(session, cycles: int):
        """直接用引擎从头全量算（绕开会话的增量路径），作为金标准。"""
        from store.schedule import simulate_schedule

        return simulate_schedule(
            session.schedule, cycles=cycles,
            initial_moods=dict(session.initial_moods),
            entry_events=session.entry_events,
            entry_swap_with=session.entry_swap_with,
            entry_scope=session.entry_scope,
            entry_restore_back=session.entry_restore_back,
            entry_when=session.entry_when,
            entry_per_shift=list(session.entry_per_shift),
            idle_to_dorm=session.idle_to_dorm,
            idle_entries=session.idle_entry_list(),
            mood_events=list(session.mood_events))

    def _assert_same(self, a, b, note=""):
        self.assertEqual([str(t) for t in a.times], [str(t) for t in b.times],
                         f"{note}：节点时刻不同")
        for n in a.names:
            self.assertEqual([str(v) for v in a.moods[n]], [str(v) for v in b.moods[n]],
                             f"{note}：{n} 的曲线不同")
        self.assertEqual([(str(m.t), m.kind, m.label) for m in a.marks],
                         [(str(m.t), m.kind, m.label) for m in b.marks], f"{note}：标记不同")
        self.assertEqual(sorted(str(t) for t in a.idle_states),
                         sorted(str(t) for t in b.idle_states), f"{note}：逐次宿舍态不同")
        self.assertEqual([(str(x), str(y)) for x, y, _w in a.segments],
                         [(str(x), str(y)) for x, y, _w in b.segments], f"{note}：段不同")

    def _session(self, cycles: int):
        from data.paths import MAA_SAMPLE
        from store.session import Session

        s = Session()
        s.load_paths([MAA_SAMPLE])           # 这一下算 1 个周期
        s.set_cycles(cycles)
        s.recompute()                        # ⚠️ `set_cycles` 只改数字，重算由调用方发起（界面也是）
        return s

    def _spy(self):
        """把引擎调用记下来（看 `continue_from` 到底传了什么）。"""
        from store import session as session_mod

        seen = []
        orig = session_mod.simulate_schedule

        def spy(*a, **kw):
            seen.append(kw.get("continue_from"))
            return orig(*a, **kw)

        session_mod.simulate_schedule = spy
        self.addCleanup(lambda: setattr(session_mod, "simulate_schedule", orig))
        return seen

    def test_加周期只算尾巴且逐位一致(self):
        seen = self._spy()
        s = self._session(2)                 # 1 → 2 周期：续算（种子 = 已算完的 1 个周期 = 3 段）
        self.assertTrue(seen and seen[-1] is not None, "加周期应当走增量（continue_from 非空）")
        self.assertEqual(seen[-1][1], 3, "种子应当是「已算完 3 段」（1 个周期 × 3 班）")
        self._assert_same(s.traj, self._engine_reference(s, 2), "1→2 周期")

        s.set_cycles(4)                      # 2 → 4 周期：再从 6 段处续
        s.recompute()
        self.assertEqual(seen[-1][1], 6)
        self._assert_same(s.traj, self._engine_reference(s, 4), "2→4 周期")

    def test_减周期就是截断且逐位一致(self):
        s = self._session(6)
        seen = self._spy()
        s.set_cycles(2)                      # 6 → 2：只截断，一段都不用算
        s.recompute()
        self.assertTrue(seen and seen[-1] is not None)
        self.assertEqual(seen[-1][1], 6, "种子是那份 6 周期（18 段）的轨迹，引擎自己按新长度截断")
        self._assert_same(s.traj, self._engine_reference(s, 2), "6→2 周期")

    def test_改了别的设置就不再用增量(self):
        s = self._session(3)
        # 改初始心情（全局设置）⇒ 全局指纹变了 ⇒ 必须从第 0 段整条重算
        s.set_initial_mood("菲亚梅塔", Decimal("9"))
        seen = self._spy()
        s.set_cycles(5)
        s.recompute()
        self.assertIsNone(seen[-1], "改了全局设置之后不能拿旧轨迹当种子")
        self._assert_same(s.traj, self._engine_reference(s, 5), "改心情后 3→5 周期")
        # 但**这次**算完之后缓存又有效了：再改周期数就能续算
        s.set_cycles(6)
        s.recompute()
        self.assertIsNotNone(seen[-1])
        self.assertEqual(seen[-1][1], 15, "5 个周期 = 15 段")
        self._assert_same(s.traj, self._engine_reference(s, 6), "5→6 周期")

    def test_改某班布局只从那一班起重算(self):
        """**P6b**：布局按班次存、那个班每周期都会出现 ⇒ 切点＝它在**第 1 周期**的出现处。"""
        s = self._session(4)
        facs = s.facilities_of(2)                       # 第 3 班
        facs[0]["operators"] = list(facs[0].get("operators", []))[:1]   # 少放一个人
        seen = self._spy()
        s.replace_facilities(2, facs)
        self.assertEqual(seen[-1][1], 2, "第 3 班 ⇒ 切点在第 2 段（0 基）")
        self._assert_same(s.traj, self._engine_reference(s, 4), "改第 3 班布局")

    def test_改末周期的心情锚点只算那一段(self):
        """**P6b**：锚点是"周期×时刻"的，命中哪一段就从那一段起算（第 7 周期第 1 班 = 3 段）。"""
        s = self._session(7)
        seen = self._spy()
        s.set_mood_at("菲亚梅塔", Decimal("6"), t=Decimal("2"))   # 第 1 周期第 1 班内
        s.recompute()                        # ⚠️ `set_mood_at` 只登记锚点，重算由调用方发起
        self.assertIsNone(seen[-1], "第 1 周期的锚点命中第 0 段 ⇒ 只能整条重算")
        self._assert_same(s.traj, self._engine_reference(s, 7), "第 1 周期锚点")
        # 换成最后一个周期的锚点：切点应当落在第 7 周期第 1 班 = 第 18 段
        s.clear_mood_events()
        s.recompute()                        # 先让"没有锚点"的那一版落地，作为续算基准
        ev2 = MoodSetEvent(name="菲亚梅塔", mood=Decimal("6"), t=Decimal("2"), cycle=7)
        seen = self._spy()
        s.mood_events = [ev2]
        s.recompute()
        self.assertEqual(seen[-1][1], 18, "第 7 周期的锚点 ⇒ 切点在第 18 段")
        self._assert_same(s.traj, self._engine_reference(s, 7), "第 7 周期锚点")

    def test_改末周期的逐次设置只算那一段(self):
        """**P6b**：闲置入宿的逐次设置按「周期×班次×干员」存 ⇒ 只算命中那一段。"""
        s = self._session(7)
        groups = s.idle_groups()
        title, (cyc, shf), rows = next(g for g in groups if g[1] == (7, 3))
        name = rows[0][0]
        seen = self._spy()
        s.idle_entries[(cyc, shf, name)] = (False, None)     # 第 7 周期第 3 班：这一位不参与
        s.recompute()
        self.assertEqual(seen[-1][1], 20, "第 7 周期第 3 班 = 第 20 段（0 基）")
        self._assert_same(s.traj, self._engine_reference(s, 7), "改第 7 周期第 3 班逐次设置")


class Test变量快照共享(unittest.TestCase):
    """**P7：一批人的净速率共享一份变量快照**（`rules.net_rates`）。

    为什么要有回归：`collect_variables(world)` 是**世界级**的量（人间烟火/热情值/无声共鸣…），
    优化后"算一批人只收一次"、由调用方传进 `mood_ledger` / `compute_net_rate`。
    这一步**不许改变任何数值** —— 所以既逐人核对，也把整条轨迹与"逐人自收"的旧行为逐位对照。
    """

    @staticmethod
    def _world():
        from data.paths import MAA_SAMPLE
        from store.schedule import load_schedule

        return load_schedule([str(MAA_SAMPLE)]).shifts[0].world

    def test_共享与逐人自收逐位相同(self):
        from mood_soc.rules import compute_net_rate, net_rates

        world = self._world()
        names = [o.name for o in world.all_operators()]
        shared = net_rates(world, names)
        self.assertEqual({n: str(v) for n, v in shared.items()},
                         {n: str(compute_net_rate(world, n)) for n in names},
                         "共享快照算出来的速率必须与逐人自收完全一致")

    def test_整条轨迹与逐人自收逐位相同(self):
        """把引擎退回"每人各收一份"的旧行为，同一条轨迹必须逐位相同。"""
        import mood_soc.rules as rules
        from data.paths import MAA_SAMPLE
        from store.session import Session

        s = Session()
        s.load_paths([MAA_SAMPLE])
        s.set_cycles(3)
        s.recompute()
        base = s.traj

        orig_rate, orig_ledger = rules.compute_net_rate, rules.mood_ledger

        def old_rate(world, name, variables=None):
            return orig_rate(world, name, None)          # 忽略传进来的快照 = 旧行为

        def old_ledger(world, name, variables=None):
            return orig_ledger(world, name, None)

        rules.compute_net_rate, rules.mood_ledger = old_rate, old_ledger
        try:
            s._resume_from = None                        # 强制整条重算
            s.recompute()
        finally:
            rules.compute_net_rate, rules.mood_ledger = orig_rate, orig_ledger

        self.assertEqual([str(t) for t in s.traj.times], [str(t) for t in base.times])
        for n in base.names:
            self.assertEqual([str(v) for v in s.traj.moods[n]],
                             [str(v) for v in base.moods[n]], n)
        self.assertEqual([(str(m.t), m.kind, m.label) for m in s.traj.marks],
                         [(str(m.t), m.kind, m.label) for m in base.marks])


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
        # ① **显式关掉**闲置入宿：平线（心情 10 不动）
        #    ⚠️ 闲置入宿现在**默认开**，所以"不动"必须显式 `False`。
        s.idle_to_dorm = False
        s.recompute()
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

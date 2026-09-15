"""tests/test_ui_schedule_blackbox.py —— 图形界面的**计算核心**黑盒测试。

只通过 `ui.schedule` 的公开 API 断言"输入 → 输出"（排班文件 → 整周期心情轨迹），
不测任何内部结构。另有一条结构性断言：**导入 `ui.schedule` 不得拉起 tkinter**
（引擎必须能在无显示器环境里被测试/复用）。

运行：.venv/Scripts/python.exe -m unittest tests.test_ui_schedule_blackbox -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from ui.schedule import (EVENT_THRESHOLDS, Schedule, all_operator_names,
                         default_initial_moods, load_schedule, shift_from_facilities,
                         simulate_schedule)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_MAA = ROOT / "resources" / "arknights-infra-schedule-maa.json"
D = Decimal
# Decimal 除法在 28 位有效数字处舍入，故"精确值"比较留 1e-6 的数值容差（≈ 3.6 毫秒的心情）
TOL = D("0.000001")


class MoodAssertMixin:
    """心情断言（带 1e-6 容差；见 TOL 说明）。"""

    def assertMood(self, actual, expected, msg=""):
        self.assertLessEqual(abs(D(actual) - D(expected)), TOL,
                             f"{msg} 期望 {expected}，实际 {actual}")


def maa_data(plans):
    """构造 MAA 排班 JSON：plans = [(班次名, {room: [[干员...], ...]})]，每个子列表是一个房间。"""
    out = []
    for name, rooms in plans:
        out.append({
            "name": name,
            "rooms": {k: [{"skip": False, "sort": True, "operators": list(room)}
                          for room in room_list]
                      for k, room_list in rooms.items()},
            "drones": {}, "Fiammetta": {}, "description": "",
        })
    return {"title": "测试用", "planTimes": f"{len(out)}班", "plans": out}


class Test排班装配(unittest.TestCase):
    def test_maa文件含多班(self):
        """一个 MAA 文件里的每个 plan 都是一个班次，时长从班次名的 `12h/6h` 解析。"""
        sch = load_schedule([SAMPLE_MAA])
        self.assertEqual([(s.label, s.hours) for s in sch.shifts],
                         [("Shift 1 · 12h", D("12")), ("Shift 2 · 6h", D("6")), ("Shift 3 · 6h", D("6"))])
        self.assertEqual(sch.cycle_hours, D("24"))
        self.assertEqual(sch.hour_labels(), ["0:00", "12:00", "18:00"])
        self.assertGreater(len(sch.operator_names()), 40)

    def test_三个文件各一班(self):
        """用户的常见用法：12h / 6h / 6h 三个文件各含一个 plan。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i, (name, ops) in enumerate([("12h", ["龙舌兰", "路人甲"]),
                                             ("6h", ["龙舌兰", "路人乙"]),
                                             ("6h", ["路人丙", "路人丁"])]):
                p = Path(tmp) / f"shift{i}.json"
                p.write_text(json.dumps(maa_data([(name, {"trading": [ops]})]), ensure_ascii=False),
                             encoding="utf-8")
                paths.append(p)
            sch = load_schedule(paths)
        self.assertEqual([s.hours for s in sch.shifts], [D("12"), D("6"), D("6")])
        self.assertEqual(sch.cycle_hours, D("24"))
        self.assertEqual(sch.index_at(0), 0)
        self.assertEqual(sch.index_at(12), 1)
        self.assertEqual(sch.index_at(19), 2)

    def test_场景文件也可作为一个班次(self):
        """本工具的场景 JSON（facilities）也能当班次导入，时长按给定时长。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "s.json"
            p.write_text(json.dumps({"facilities": [
                {"type": "控制中枢", "level": 1, "operators": ["路人1", "路人2"]},
                {"type": "制造站", "level": 3, "operators": ["泡泡"]},
            ]}, ensure_ascii=False), encoding="utf-8")
            sch = load_schedule([p], hours=[D("24")])
        self.assertEqual(len(sch.shifts), 1)
        self.assertEqual(sch.shifts[0].hours, D("24"))
        self.assertEqual([n for n in sch.operator_names()], ["路人1", "路人2", "泡泡"])

    def test_显式时长按顺序覆盖班次(self):
        """`load_schedule(..., hours=[12,6,6])` 要真的生效（曾把 hours 静默忽略）。

        三个场景文件各一班 —— 就是用户的"12h/6h/6h 三个文件"用法。
        """
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(3):
                p = Path(tmp) / f"shift{i}.json"
                p.write_text(json.dumps({"facilities": [
                    {"type": "宿舍", "level": 5, "operators": [{"name": f"路人{i}", "mood": 24}]},
                ]}, ensure_ascii=False), encoding="utf-8")
                paths.append(p)
            sch = load_schedule(paths, hours=[D("12"), D("6"), D("6")])
        self.assertEqual([s.hours for s in sch.shifts], [D("12"), D("6"), D("6")])
        self.assertEqual(sch.cycle_hours, D("24"))
        self.assertEqual(sch.starts, [D("0"), D("12"), D("18")])
        # 不给 hours 时按均分（没有任何时长提示）
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(3):
                p = Path(tmp) / f"shift{i}.json"
                p.write_text(json.dumps({"facilities": []}, ensure_ascii=False), encoding="utf-8")
                paths.append(p)
            self.assertEqual([s.hours for s in load_schedule(paths).shifts], [D("8")] * 3)

    def test_班次名没有时长则均分周期(self):
        """班次名里没有 `h` ⇒ 按班次数均分默认 24h（3 班 → 8/8/8）。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.json"
            p.write_text(json.dumps(maa_data([("A班", {"manufacture": [["泡泡"]]}),
                                              ("B班", {"manufacture": [["泡泡"]]}),
                                              ("C班", {"manufacture": [["泡泡"]]})]),
                                     ensure_ascii=False), encoding="utf-8")
            sch = load_schedule([p])
        self.assertEqual([s.hours for s in sch.shifts], [D("8"), D("8"), D("8")])

    def test_各班长之和不等于周期要报错(self):
        """不变式：各班长之和 == 周期时长，否则直接报错（不给"半截周期"）。"""
        s1 = shift_from_facilities("A", D("12"), [{"type": "制造站", "operators": ["泡泡"]}])
        s2 = shift_from_facilities("B", D("8"), [{"type": "制造站", "operators": ["泡泡"]}])
        with self.assertRaises(ValueError):
            Schedule([s1, s2], D("24"))
        self.assertEqual(Schedule([s1, s2], D("20")).cycle_hours, D("20"))   # 自洽则通过

    def test_无法识别的文件报错(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.json"
            p.write_text('{"hello": 1}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_schedule([p])


class Test轨迹(MoodAssertMixin, unittest.TestCase):
    """整周期轨迹：钳位、分段线性、红脸、未排班、跨周期连续。"""

    @classmethod
    def setUpClass(cls):
        cls.sch = load_schedule([SAMPLE_MAA])
        cls.traj = simulate_schedule(cls.sch, cycles=1)

    def test_钳位与连续性(self):
        """所有时刻心情都在 [0,24]；节点处取值与 mood_at 完全一致（无跳变）。"""
        for name in self.traj.names:
            for t, v in zip(self.traj.times, self.traj.moods[name]):
                self.assertGreaterEqual(v, D("0"), name)
                self.assertLessEqual(v, D("24"), name)
                self.assertEqual(self.traj.mood_at(name, t), v, f"{name}@{t}")

    def test_分段线性_中点等于两端平均(self):
        """轨迹是折线：相邻节点中点的取值 == 两端平均（这就是"精确"的可验证落点）。

        允许 1e-9 的数值容差（Decimal 除法在 28 位有效数字处舍入）。
        """
        name = "巫恋"
        ts, vs = self.traj.times, self.traj.moods[name]
        for i in range(1, min(len(ts), 40)):
            mid = (ts[i - 1] + ts[i]) / 2
            expect = (vs[i - 1] + vs[i]) / 2
            self.assertLessEqual(abs(self.traj.mood_at(name, mid) - expect), D("0.000000001"))

    def test_上班降_宿舍升(self):
        """班次内方向正确：贸易站工作 → 下降；进宿舍 → 上升到满。"""
        name = "锡人"           # 班次1 办公室、班次2 宿舍、班次3 办公室
        self.assertLess(self.traj.mood_at(name, 12), self.traj.mood_at(name, 0))
        self.assertGreater(self.traj.mood_at(name, 18), self.traj.mood_at(name, 12))
        self.assertMood(self.traj.mood_at(name, 18), "24")       # 2h 内回满
        self.assertMood(self.traj.mood_at(name, 24), "19.5")     # 末班 6h×0.75

    def test_红脸段与技能失效(self):
        """跑 3 个周期时巫恋会红脸：红脸区间内心情恒为 0，且此后不再下降（钳位）。"""
        traj = simulate_schedule(self.sch, cycles=3)
        spans = traj.red_face_spans("巫恋")
        self.assertTrue(spans, "3 周期内巫恋应当红脸")
        t0, t1 = spans[0]
        self.assertGreater(t0, D("24"))            # 第 2 周期才红脸
        self.assertEqual(traj.mood_at("巫恋", t0), D("0"))
        self.assertEqual(traj.mood_at("巫恋", (t0 + t1) / 2), D("0"))
        self.assertEqual(traj.mood_at("巫恋", D("72")), D("0"))

    def test_未排班干员心情不变(self):
        """某班次里没这个干员 ⇒ 视为不在基建内：速率 0、心情不变。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.json"
            p.write_text(json.dumps(maa_data([
                ("12h", {"manufacture": [["泡泡"]]}),
                ("12h", {"manufacture": [["火神"]]}),
            ]), ensure_ascii=False), encoding="utf-8")
            sch = load_schedule([p])
        traj = simulate_schedule(sch, cycles=1)
        self.assertEqual(traj.mood_at("火神", 6), D("24"))       # 前 12h 未排班
        self.assertLess(traj.mood_at("火神", 18), D("24"))       # 后 12h 上班
        # 制造站单人：1 − 泡泡自身「囤积者」0.25 − 无中枢 = 0.75/h
        self.assertMood(traj.mood_at("泡泡", 6), "19.5")

    def test_跨周期连续(self):
        """cycles=2：总长 48h，第 2 周期起点接着第 1 周期末端（不重置心情）。"""
        traj = simulate_schedule(self.sch, cycles=2)
        self.assertEqual(traj.total_hours, D("48"))
        self.assertLess(traj.mood_at("巫恋", D("24")), D("24"))
        # 第一周期净降 15.6 点 = 0.65/h × 24h（巫恋三班都在贸易站，净速率恒定）
        self.assertMood(traj.mood_at("巫恋", D("0")) - traj.mood_at("巫恋", D("24")), "15.6")
        # 第 2 周期接着降（不重置）：48h 处比 24h 处更低
        self.assertLess(traj.mood_at("巫恋", D("48")), traj.mood_at("巫恋", D("24")))

    def test_周期起点心情取布局里写的值(self):
        """排班/场景文件里写的心情就是"周期起点"（不是一律 24），否则进驻事件等条件会失真。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "d.json"
            p.write_text(json.dumps({"facilities": [
                {"type": "宿舍", "level": 5, "operators": [
                    {"name": "路人", "mood": "6"}, {"name": "菲亚梅塔", "mood": "24"}]},
            ]}, ensure_ascii=False), encoding="utf-8")
            sch = load_schedule([p], hours=[D("24")])
        self.assertEqual(default_initial_moods(sch), {"路人": D("6"), "菲亚梅塔": D("24")})
        traj = simulate_schedule(sch, cycles=1)
        self.assertEqual(traj.mood_at("菲亚梅塔", 0), D("24"))
        self.assertEqual(traj.mood_at("路人", 0), D("6"))

    def test_初始心情可指定(self):
        traj = simulate_schedule(self.sch, cycles=1, initial_moods={"巫恋": D("6")})
        self.assertEqual(traj.mood_at("巫恋", 0), D("6"))

    def test_对点查询与关键数值(self):
        """"对点"：给定干员名 → 全程取值、极值、每班最低点。"""
        lo, lo_t, hi, hi_t = self.traj.bounds("森蚺")
        self.assertMood(hi, "24")
        self.assertLess(lo, D("16.2"))                       # 末班回满前的低谷
        self.assertEqual(lo_t, D("18"))                      # 第 2 班末
        per = self.traj.min_mood_at_each_shift("森蚺")
        self.assertEqual([l for l, _ in per], [s.label for s in self.sch.shifts])
        self.assertMood(per[0][1], "16.2")                   # 12h × 0.65
        pts = self.traj.sample("森蚺", points=25)
        self.assertEqual(len(pts), 25)
        self.assertEqual(pts[0][0], D("0"))
        self.assertEqual(pts[-1][0], D("24"))

    def test_班次标记(self):
        labels = [(m.t, m.label) for m in self.traj.marks if m.kind == "shift"]
        self.assertEqual(labels, [(D("0"), "Shift 1 · 12h"), (D("12"), "Shift 2 · 6h"), (D("18"), "Shift 3 · 6h")])


class Test编辑接口(MoodAssertMixin, unittest.TestCase):
    """界面上的"改时长 / 改布局"走这两个方法（返回新 Schedule，不改原对象）。"""

    @classmethod
    def setUpClass(cls):
        cls.sch = load_schedule([SAMPLE_MAA])

    def test_改班次时长(self):
        new = self.sch.with_hours([D("8"), D("8"), D("8")])
        self.assertEqual([s.hours for s in new.shifts], [D("8")] * 3)
        self.assertEqual(new.cycle_hours, D("24"))
        self.assertEqual([s.hours for s in self.sch.shifts], [D("12"), D("6"), D("6")])   # 原对象不变

    def test_替换某个班次的布局(self):
        facs = [{"type": "制造站", "level": 3, "name": "制造站#1", "operators": ["泡泡", "火神"]}]
        new = self.sch.replaced_shift(1, facs)
        self.assertEqual(new.shifts[1].operators, ["泡泡", "火神"])
        self.assertEqual(new.shifts[0].operators, self.sch.shifts[0].operators)
        traj = simulate_schedule(new, cycles=1)
        self.assertEqual(traj.mood_at("泡泡", 12), D("24"))      # 班次2 才上班
        self.assertLess(traj.mood_at("泡泡", 18), D("24"))

    def test_手动搭一个班次(self):
        """界面"手动一个个设置位置"的底层：直接给 facilities 就行。"""
        s = shift_from_facilities("手动班", D("24"), [
            {"type": "控制中枢", "level": 1, "operators": ["路人1", "路人2", "路人3", "路人4", "路人5"]},
            {"type": "制造站", "level": 3, "name": "制造站#1", "operators": ["泡泡"]},
            {"type": "宿舍", "level": 5, "name": "宿舍#1", "operators": [{"name": "菲亚梅塔", "mood": "10"}]},
        ])
        sch = Schedule([s], D("24"))
        traj = simulate_schedule(sch, cycles=1)
        self.assertMood(traj.mood_at("泡泡", 0), "24")
        self.assertMood(traj.mood_at("泡泡", 24), "12")            # 0.5/h × 24h
        self.assertMood(traj.mood_at("菲亚梅塔", 24), "24")         # 独占 2/h：10 → 24（7h 到顶）


class Test进驻事件与口径(MoodAssertMixin, unittest.TestCase):
    def test_进驻事件开关(self):
        """M15a 患难之交：默认不结算；打开后在班次开始时互换心情。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.json"
            p.write_text(json.dumps({"facilities": [
                {"type": "宿舍", "level": 5,
                 "operators": [{"name": "路人", "mood": "6"}, {"name": "菲亚梅塔", "mood": "24"}]},
            ]}, ensure_ascii=False), encoding="utf-8")
            paths = [p]
            sch = load_schedule(paths, hours=[D("24")])
            off = simulate_schedule(sch, cycles=1, entry_events=False)
            on = simulate_schedule(sch, cycles=1, entry_events=True)
        self.assertEqual(off.mood_at("菲亚梅塔", 0), D("24"))     # 不结算
        self.assertEqual(on.mood_at("菲亚梅塔", 0), D("6"))       # 与前一位互换
        self.assertEqual(on.mood_at("路人", 0), D("24"))
        self.assertTrue([m for m in on.marks if m.kind == "entry"])

    def test_任意位置与强制等待(self):
        """跨设施换心情 + **到点没满就等她回满那一刻再换**（entry_force）。

        场景：菲亚梅塔在宿舍（独占回复 +2/h，从 10 起 → **7h 回满**），
        制造站里的乙此时正一路掉到 0（红脸）。
        """
        def build(force):
            data = {"entry_events": {"enabled": True, "scope": "anywhere",
                                     "swap_with": "乙", "force": force},
                    "facilities": [
                        {"type": "宿舍", "level": 5, "operators": [
                            {"name": "甲", "mood": "10"}, {"name": "菲亚梅塔", "mood": "10"}]},
                        {"type": "制造站", "level": 3, "name": "制造站#1", "operators": [
                            {"name": "乙", "mood": "2"}, {"name": "丙", "mood": "20"}]},
                    ]}
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "f.json"
                p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return load_schedule([p], hours=[D("24")])

        # ① 不强制：她不满心情 → 这一班不换（乙继续红脸）
        traj = simulate_schedule(build(False), cycles=1, entry_events=True)
        self.assertEqual([m for m in traj.marks if m.kind == "entry"], [])
        self.assertMood(traj.mood_at("菲亚梅塔", 24), "24")          # 她照常回满
        self.assertMood(traj.mood_at("乙", 24), "0")                 # 乙一直是 0

        # ② 强制：她回满的那一刻（t=7）才换
        traj = simulate_schedule(build(True), cycles=1, entry_events=True)
        swaps = [m for m in traj.marks if m.kind == "entry" and "互换" in m.label]
        self.assertEqual(len(swaps), 1)
        self.assertLessEqual(abs(swaps[0].t - D("7")), D("0.01"))
        self.assertMood(traj.mood_at("菲亚梅塔", D("7")), "0")        # 接下乙的 0
        self.assertMood(traj.mood_at("乙", D("7")), "24")            # 乙被换满
        self.assertLess(traj.mood_at("菲亚梅塔", D("6.9")), D("24"))  # 换之前她还没满
        # 班次开始时会先留一条"她没满、等她回满再换"的说明（不是静默）
        self.assertTrue([m for m in traj.marks
                         if m.kind == "entry" and "等她回满" in m.label])

    def test_位置也互换时轨迹不同(self):
        """`restore_back=False`：换完位置也对调 → 她被丢进制造站（开始掉），他回宿舍（保持满）。"""
        def build(restore_back):
            data = {"entry_events": {"enabled": True, "scope": "anywhere", "swap_with": "乙",
                                     "restore_back": restore_back},
                    "facilities": [
                        {"type": "宿舍", "level": 5, "operators": [
                            {"name": "菲亚梅塔", "mood": "24"}, {"name": "甲", "mood": "10"}]},
                        {"type": "制造站", "level": 3, "name": "制造站#1", "operators": [
                            {"name": "乙", "mood": "2"}, {"name": "丙", "mood": "20"}]},
                    ]}
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "f.json"
                p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return load_schedule([p], hours=[D("24")])

        keep = simulate_schedule(build(True), cycles=1, entry_events=True)
        swap = simulate_schedule(build(False), cycles=1, entry_events=True)
        self.assertIn("位置不变", [m.label for m in keep.marks if m.kind == "entry"][0])
        self.assertIn("位置也对调", [m.label for m in swap.marks if m.kind == "entry"][0])
        # 换完之后：留在宿舍的她（独占 +2/h）回升；被换到制造站的她一路掉到红脸
        self.assertGreater(keep.mood_at("菲亚梅塔", D("6")), D("10"))
        self.assertEqual(swap.mood_at("菲亚梅塔", D("6")), D("0"))
        self.assertEqual(swap.mood_at("乙", D("6")), D("24"))        # 他在宿舍 → 保持满

    def _three_shifts(self, entry_events, her_mood: str = "24"):
        """三个文件各一班（12/6/6），每班布局相同：

        宿舍 = 甲 20 + 菲亚梅塔；制造站 = 巫恋 6 + 乙 18；贸易站 = 龙舌兰 2 + 丙 12。
        进驻事件配置放在第一个文件里（读配置看的是第一个班次）。
        """
        tmpdir = tempfile.mkdtemp()
        paths = []
        names = ("Shift 1 · 12h", "Shift 2 · 6h", "Shift 3 · 6h")
        for i in range(3):
            p = Path(tmpdir) / f"s{i}.json"
            data = {"_source_plan": names[i],          # 班次名（按班次名定位覆盖时要用）
                    "facilities": [
                        {"type": "宿舍", "level": 5, "operators": [
                            {"name": "甲", "mood": "20"}, {"name": "菲亚梅塔", "mood": her_mood}]},
                        {"type": "制造站", "level": 3, "name": "制造站#1", "operators": [
                            {"name": "巫恋", "mood": "6"}, {"name": "乙", "mood": "18"}]},
                        {"type": "贸易站", "level": 3, "name": "贸易站#1", "operators": [
                            {"name": "龙舌兰", "mood": "2"}, {"name": "丙", "mood": "12"}]},
                    ]}
            if i == 0:
                data["entry_events"] = entry_events
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            paths.append(p)
        return load_schedule(paths, hours=[D("12"), D("6"), D("6")])

    @staticmethod
    def _swaps(traj):
        return [m for m in traj.marks if m.kind == "entry" and "互换" in m.label]

    def test_按班次逐班生效(self):
        """三班 12/6/6：1 班换指定的人、2 班自动挑最累的、3 班完全不用。"""
        sch = self._three_shifts({"enabled": True, "scope": "anywhere", "per_shift": [
            {"swap_with": "巫恋"}, {"swap_with": "any"}, {"enabled": False}]})
        self.assertEqual(sch.entry_config_for_shift(0).swap_with, "巫恋")
        self.assertEqual(sch.entry_config_for_shift(1).swap_with, "any")
        self.assertIs(sch.entry_config_for_shift(2).enabled, False)

        traj = simulate_schedule(sch, cycles=1, entry_events=True)
        swaps = self._swaps(traj)
        self.assertEqual(len(swaps), 2, [m.label for m in swaps])       # 第 3 班不换
        self.assertEqual([float(m.t) for m in swaps], [0.0, 12.0])
        self.assertIn("指定的 巫恋", swaps[0].label)
        self.assertIn("自动挑的 龙舌兰", swaps[1].label)                  # 龙舌兰 2 点，最累
        self.assertFalse([m for m in traj.marks if float(m.t) == 18.0 and m.kind == "entry"
                          and "互换" in m.label])

    def test_按班次触发方式_逐班(self):
        """逐班「什么时候换」：`wait`（等她回满）与 `full`（只在她满时换）互不影响。"""
        def entry(when1, when2):
            return {"enabled": True, "scope": "anywhere", "swap_with": "巫恋", "when": "full",
                    "per_shift": [{"when": when1}, {"when": when2}, {"enabled": False}]}

        # ① 第 1 班 wait：她在第 1 班回满的那一刻（7h）就换 → 之后到下个班次都还没满 → 只换 1 次
        traj = simulate_schedule(self._three_shifts(entry("wait", "full"), her_mood="10"),
                                 cycles=1, entry_events=True)
        swaps = self._swaps(traj)
        self.assertEqual(len(swaps), 1)
        self.assertLessEqual(abs(swaps[0].t - D("7")), D("0.01"))

        # ② 第 1 班 full（此刻不满 → 不换），第 2 班 full：到 12h 她已经满心情 → 在 12h 换
        traj = simulate_schedule(self._three_shifts(entry("full", "full"), her_mood="10"),
                                 cycles=1, entry_events=True)
        swaps = self._swaps(traj)
        self.assertEqual(len(swaps), 1)
        self.assertLessEqual(abs(swaps[0].t - D("12")), D("0.01"))

        # ③ 默认（不写 when）＝ 强制立刻换：第 1 班开局就换，不看双方心情
        traj = simulate_schedule(
            self._three_shifts({"enabled": True, "scope": "anywhere", "swap_with": "巫恋"},
                               her_mood="10"),
            cycles=1, entry_events=True)
        swaps = self._swaps(traj)
        self.assertLessEqual(abs(swaps[0].t - D("0")), D("0.01"))

    def test_多周期里每周期都结算(self):
        """**跨周期同样每班结算一次**（回归：副本复用导致第 2 周期起一次都不换）。"""
        sch = self._three_shifts({"enabled": True, "scope": "anywhere", "swap_with": "巫恋"})
        one = self._swaps(simulate_schedule(sch, cycles=1, entry_events=True))
        self.assertEqual([float(m.t) for m in one], [0.0, 12.0, 18.0])   # 各班开始时各一次

        two = self._swaps(simulate_schedule(sch, cycles=2, entry_events=True))
        self.assertEqual([float(m.t) for m in two],
                         [0.0, 12.0, 18.0, 24.0, 36.0, 42.0], [m.label for m in two])
        three = self._swaps(simulate_schedule(sch, cycles=3, entry_events=True))
        self.assertEqual([float(m.t) for m in three],
                         [0.0, 12.0, 18.0, 24.0, 36.0, 42.0, 48.0, 60.0, 66.0])

    def test_多周期与单周期第一段一致(self):
        """周期数只影响"跑到第几周"，不影响已经跑过的那一段（回归：别把单周期算歪）。"""
        sch = self._three_shifts({"enabled": True, "scope": "anywhere", "swap_with": "巫恋"})
        one = simulate_schedule(sch, cycles=1, entry_events=True)
        two = simulate_schedule(sch, cycles=2, entry_events=True)
        for name in one.names:
            self.assertEqual(one.mood_at(name, 6), two.mood_at(name, 6), name)
            self.assertEqual(one.mood_at(name, 20), two.mood_at(name, 20), name)

    def test_按班次写法与继承(self):
        """按序号 / 按班次名定位；显式 null 清空对象；没写的字段继承全局。"""
        sch = self._three_shifts({"enabled": True, "scope": "anywhere", "swap_with": "巫恋",
                                  "per_shift": {"2": {"swap_with": None, "force": True},
                                                "Shift 3 · 6h": {"enabled": False}}})
        eff1 = sch.entry_config_for_shift(0)
        self.assertEqual(eff1.swap_with, "巫恋")
        self.assertTrue(eff1.restore_back)          # 没写 → 继承全局默认
        eff2 = sch.entry_config_for_shift(1)
        self.assertIsNone(eff2.swap_with)           # 明确清空 → 回默认口径
        self.assertTrue(eff2.force)
        self.assertEqual(eff2.scope, "anywhere")    # 没写 scope → 继承
        self.assertIs(sch.entry_config_for_shift(2).enabled, False)

    def test_阈值集合覆盖已知心情条件(self):
        """事件阈值必须覆盖现有条件函数读的心情值（0/12/18/20/24）。"""
        self.assertEqual(set(EVENT_THRESHOLDS), {D("0"), D("12"), D("18"), D("20"), D("24")})

    def test_进驻事件可指定交换对象(self):
        """多班周期里的换心情也能指定对象：`entry_swap_with` > JSON 的 `swap_with` > 前一位进驻。"""
        import json as _json
        import tempfile as _tempfile
        from pathlib import Path as _Path

        def build(entry_events=None):
            data = {"facilities": [{"type": "宿舍", "level": 5, "operators": [
                {"name": "甲", "mood": "6"}, {"name": "乙", "mood": "9"},
                {"name": "菲亚梅塔", "mood": "24"}]}]}
            if entry_events is not None:
                data["entry_events"] = entry_events
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "d.json"
                p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return load_schedule([p], hours=[D("24")])

        # ① 指定与"甲"互换（参数优先）
        traj = simulate_schedule(build(), cycles=1, entry_events=True, entry_swap_with="甲")
        self.assertEqual(traj.mood_at("菲亚梅塔", 0), D("6"))
        self.assertEqual(traj.mood_at("甲", 0), D("24"))
        # ② 不指定 → 默认"前一位进驻"（乙）
        traj = simulate_schedule(build(), cycles=1, entry_events=True)
        self.assertEqual(traj.mood_at("菲亚梅塔", 0), D("9"))
        # ③ JSON 里指定 → 参数为 None 时按 JSON 走
        traj = simulate_schedule(build({"enabled": True, "swap_with": "甲"}),
                                 cycles=1, entry_events=True)
        self.assertEqual(traj.mood_at("菲亚梅塔", 0), D("6"))
        # ④ 配置能从排班里读出来（界面据此设初始开关与下拉值）
        sch = build({"enabled": True, "swap_with": "乙"})
        self.assertTrue(sch.entry_config().enabled)
        self.assertEqual(sch.entry_config().swap_with, "乙")
        # ⑤ 编辑接口不丢配置
        self.assertEqual(sch.replaced_shift(0, sch.shifts[0].facilities)
                         .entry_config().swap_with, "乙")
        self.assertEqual(sch.with_hours([D("24")]).entry_config().enabled, True)

    def test_干员名册来自全量文件(self):
        names = all_operator_names()
        self.assertGreater(len(names), 400)      # operators.txt 全量（含只有生产/训练技能的干员）
        self.assertIn("锡人", names)


class Test副本状态时效性(MoodAssertMixin, unittest.TestCase):
    """一类老 bug 的回归：**同一个副本被跨班次/跨周期复用**时的状态时效性。

    | 症状 | 原因 |
    |---|---|
    | 周期数 ≥ 2 时第 2 周期起换心情一次都不触发 | `Operator.entry_swapped` 幂等标记留在复用的副本上 |
    | 依赖心情的条件技能（宿舍单体回复选谁…）算错 | 算速率读副本里的 `.mood`，而时间推进的是另一个字典 |
    | `restore_back=false` + 多周期时那一班静默不换 | 位置互换改过副本，第 2 个周期她不在宿舍、触发条件不成立 |
    """

    @staticmethod
    def _dorm_schedule(plan_mood_of_c: str):
        """两班 12/12：宿舍 = 临光（宿舍单体回复提供者）+ 乙 + 丙；另一班只有甲。

        `plan_mood_of_c`＝写进**布局文件**里丙的心情（副本初始值）。
        """
        fac1 = [{"type": "宿舍", "level": 3,
                 "operators": [{"name": "临光", "mood": "24"}, {"name": "乙", "mood": "24"},
                               {"name": "丙", "mood": plan_mood_of_c}]}]
        fac2 = [{"type": "制造站", "level": 3, "operators": ["甲"]}]
        return Schedule([shift_from_facilities("A · 12h", 12, fac1),
                         shift_from_facilities("B · 12h", 12, fac2)], D("24"))

    def test_条件技能按真实心情选目标(self):
        """丙 是宿舍里唯一未满的人 ⇒ 宿舍单体回复该落到她身上（+0.5/h）。

        ⚠️ 不变量：**"心情写在布局文件里"与"只在模拟起点指定"必须得到同一个结果**——
        实现上要求"算速率前把真实心情同步进副本"。曾经只写起点指定时算错（3h 后差 1.5 点）。
        """
        in_file = self._dorm_schedule("5")                       # 副本里就写着 5
        at_start = self._dorm_schedule("24")                     # 副本写着 24，只在起点指定 5
        a = simulate_schedule(in_file, cycles=1)
        b = simulate_schedule(at_start, cycles=1, initial_moods={"丙": D("5")})
        for hours in ("1", "3"):
            self.assertMood(b.mood_at("丙", D(hours)), a.mood_at("丙", D(hours)),
                            f"{hours}h 时丙的心情")
        # 丙 拿到基础 3.0 + 单体回复 0.5 = 3.5/h：5 + 3.5×3 = 15.5
        self.assertMood(a.mood_at("丙", D("3")), D("15.5"))

    def test_位置互换在多周期下每班复位(self):
        """`restore_back=false`（位置也一起互换）时，每个班次都从**计划**出发。"""
        sch = load_schedule([SAMPLE_MAA])

        def swaps(cycles):
            traj = simulate_schedule(sch, cycles=cycles, entry_events=True,
                                     entry_swap_with="森蚺", entry_scope="anywhere",
                                     entry_restore_back=False, entry_when="full")
            return [float(m.t) for m in traj.marks if m.kind == "entry" and "互换" in m.label]

        one = swaps(1)
        self.assertEqual(one, [0.0, 18.0])
        two = swaps(2)
        # 第 2 个周期必须是第 1 个周期的**平移**（曾经缺 t=24：那一班她不在宿舍，静默不换）
        self.assertEqual(two[:len(one)], one)
        self.assertEqual(two[len(one):], [t + 24.0 for t in one])

    def test_默认路径不受影响(self):
        """`restore_back=true`（默认、界面口径）：位置每班本就复位，结果与上面无关。"""
        sch = load_schedule([SAMPLE_MAA])
        traj = simulate_schedule(sch, cycles=1, entry_events=True, entry_swap_with="塞雷娅",
                                 entry_scope="anywhere", entry_when="full")
        self.assertEqual(traj.mood_at("菲亚梅塔", D("12")), D("24"))
        self.assertMood(traj.mood_at("巫恋", D("6")), D("20.1"))


class Test闲置入宿(MoodAssertMixin, unittest.TestCase):
    """`simulate_schedule(..., idle_to_dorm=True)`：每班开始时把未满的闲置干员安排进宿舍。"""

    @classmethod
    def setUpClass(cls):
        cls.sch = load_schedule([SAMPLE_MAA])

    def test_开启后未满的闲置干员会回满(self):
        """示例排班里"未排班且未满"的人会被安排进宿舍恢复。

        注意挑人：地灵/梅 整个周期都没排班 ⇒ 入宿后能一路回到 24；
        虎狼丸只在**第 2 班**闲置（第 3 班又在会客室上班）⇒ 只看第 2 班末回到 24。
        """
        off = simulate_schedule(self.sch, cycles=1)
        on = simulate_schedule(self.sch, cycles=1, idle_to_dorm=True)
        for name in ("地灵", "梅"):
            self.assertLess(off.mood_at(name, 24), D("24"))
            self.assertMood(on.mood_at(name, 24), D("24"), f"{name} 入宿后应当回满")
        self.assertLess(off.mood_at("虎狼丸", 18), D("24"))
        self.assertMood(on.mood_at("虎狼丸", 18), D("24"), "虎狼丸 第 2 班入宿后应当回满")
        self.assertGreater(on.mood_at("虎狼丸", 24), off.mood_at("虎狼丸", 24))
        # 事件流水账：真的记了"谁进宿舍 / 与谁互换"
        idle = [m for m in on.marks if m.kind == "idle"]
        self.assertTrue(idle)
        self.assertTrue(any("进宿舍" in m.label or "互换" in m.label for m in idle))

    def test_默认关闭时一切照旧(self):
        """不传 idle_to_dorm（默认 False）→ 轨迹与"没有这个功能"时逐位相同。"""
        a = simulate_schedule(self.sch, cycles=1)
        b = simulate_schedule(self.sch, cycles=1, idle_to_dorm=False)
        for name in a.names:
            self.assertEqual(a.mood_at(name, 24), b.mood_at(name, 24), name)
        self.assertFalse([m for m in b.marks if m.kind == "idle"])

    def test_逐人设置_不参与与指定(self):
        """`idle_entries`：`enabled=False` 的人不动；`swap_with` 指定与谁互换。"""
        from mood_soc.models import IdleToDormEntry

        skip = simulate_schedule(self.sch, cycles=1, idle_to_dorm=True,
                                 idle_entries=[IdleToDormEntry(name="地灵", enabled=False)])
        self.assertFalse([m for m in skip.marks
                          if m.kind == "idle" and "地灵" in m.label])
        self.assertLess(skip.mood_at("地灵", 24), D("24"))       # 没被安排 → 还是没满
        pick = simulate_schedule(self.sch, cycles=1, idle_to_dorm=True,
                                 idle_entries=[IdleToDormEntry(name="地灵", swap_with="塞雷娅")])
        evs = [m for m in pick.marks if m.kind == "idle" and "地灵" in m.label]
        self.assertEqual(len(evs), 1)
        self.assertIn("塞雷娅", evs[0].label)

    def test_跨周期每班都结算(self):
        """多周期：每个周期的每一班都要结算（复用同一套时效性机制）。"""
        one = simulate_schedule(self.sch, cycles=1, idle_to_dorm=True)
        two = simulate_schedule(self.sch, cycles=2, idle_to_dorm=True)
        n1 = len([m for m in one.marks if m.kind == "idle"])
        n2 = len([m for m in two.marks if m.kind == "idle"])
        self.assertGreater(n2, n1, "第 2 个周期也应当有闲置入宿事件")


    def test_逐次设置_同一人不同班次可以不一样(self):
        """`IdleToDormEntry` 可带 `cycle`/`shift`：同一人在第 2 班不动、第 3 班仍参与。"""
        from mood_soc.models import IdleToDormEntry

        # 地灵 只在第 3 班（=shift 3）闲置 → 限定"第 3 班不参与"之后应当完全不动
        only3 = simulate_schedule(self.sch, cycles=1, idle_to_dorm=True,
                                  idle_entries=[IdleToDormEntry(name="地灵", enabled=False,
                                                                shift=3)])
        self.assertFalse([m for m in only3.marks if m.kind == "idle" and "地灵" in m.label])
        self.assertLess(only3.mood_at("地灵", 24), D("24"))
        # 限定成"第 1 班不参与" → 第 3 班照样入宿
        only1 = simulate_schedule(self.sch, cycles=1, idle_to_dorm=True,
                                  idle_entries=[IdleToDormEntry(name="地灵", enabled=False,
                                                                shift=1)])
        self.assertTrue([m for m in only1.marks if m.kind == "idle" and "地灵" in m.label])
        self.assertMood(only1.mood_at("地灵", 24), D("24"))


class Test引擎不依赖GUI(unittest.TestCase):
    def test_导入schedule不加载tkinter(self):
        """结构性不变式：计算核心必须能在无显示器环境导入（tkinter 只在 app 层）。"""
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; import ui.schedule; "
             "print('tkinter' in sys.modules)"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
            env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "False", "ui.schedule 不该拉起 tkinter")


class Test显示格式化(unittest.TestCase):
    """`ui/theme.py` 的显示层工具（不依赖 GUI，可单独测）。

    这些是**显示边界**：引擎内部是 Decimal 精确运算，跨事件分割会留下
    `16.19999999999999999999999999` 这类 28 位尾巴——只在显示时舍入。
    """

    def test_心情与小时的格式化(self):
        from ui import theme
        self.assertEqual(theme.fmt_mood(D("16.2")), "16.2")
        self.assertEqual(theme.fmt_mood(D("16.19999999999999999999999999")), "16.2")
        self.assertEqual(theme.fmt_mood(D("24")), "24")
        self.assertEqual(theme.fmt_mood(D("0.00")), "0")
        self.assertEqual(theme.fmt_hours(D("12")), "12h")
        self.assertEqual(theme.fmt_hours(D("12.5")), "12.5h")

    def test_时长换算成人话(self):
        from ui import theme
        self.assertEqual(theme.fmt_mins(D("0.25")), "15 分钟")
        self.assertEqual(theme.fmt_mins(D("1")), "1 小时")
        self.assertEqual(theme.fmt_mins(D("1.5")), "1 小时 30 分")
        self.assertEqual(theme.fmt_mins(D("6")), "6 小时")

    def test_时钟与跨天(self):
        from ui import theme
        self.assertEqual(theme.fmt_clock(D("0")), "00:00")
        self.assertEqual(theme.fmt_clock(D("12.5")), "12:30")
        self.assertEqual(theme.fmt_clock(D("25")), "01:00（第2天）")

    def test_心情配色单调(self):
        """心情越高越"绿"：红脸的 R 分量应显著高于满心情。"""
        from ui import theme
        red_at_0 = int(theme.mood_color(D("0"))[1:3], 16)
        red_at_24 = int(theme.mood_color(D("24"))[1:3], 16)
        self.assertGreater(red_at_0, red_at_24)
        self.assertNotEqual(theme.mood_tint(D("12")), theme.mood_tint(D("24")))


if __name__ == "__main__":
    unittest.main(verbosity=2)

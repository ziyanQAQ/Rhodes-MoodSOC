"""tests/test_ui_app_smoke.py —— 图形界面的**端到端冒烟测试**（真要建窗口）。

它覆盖"界面到引擎"的接线：导入排班 → 看板出位置 → 时间滑动改心情 → 左键换人 →
右键设心情 → 对点出曲线 → 班次设置改时长。对话框用**打桩**替换（不弹真窗口），
其余全部走真实代码路径。

无图形环境（Tk 建不出来，如无桌面的 CI）时整体跳过——计算核心另有
`tests/test_ui_schedule_blackbox.py` 覆盖，不依赖显示器。

运行：.venv/Scripts/python.exe -m unittest tests.test_ui_app_smoke -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "resources" / "arknights-infra-schedule-maa.json"


def _tk_available() -> bool:
    try:
        r = tk.Tk()
        r.withdraw()
        r.destroy()
        return True
    except Exception:            # noqa: BLE001 —— 无显示器/无 Tk 都视作不可用
        return False


TK_OK = _tk_available()


class Test入口跑法(unittest.TestCase):
    """入口必须"当包跑"和"当脚本跑"都成立（不需要图形环境，故不跳过）。

    ⚠️ 回归用例：用文件路径直接运行 `ui/__main__.py` 时 Python **不把 `ui/` 当包**，
    原先的 `from .app import main` 会报
    `ImportError: attempted relative import with no known parent package`。
    现在入口先做 `sys.path` 引导、再绝对导入，两种跑法都要能走到 `main()`。

    手法：把 `ui.app` 预塞进 `sys.modules` 并用一个返回哨兵退出码的 `main` 顶掉，
    这样既验证"脚本路径真的执行到了 main"，又不会真开窗口。
    """

    STUB = ("import sys, types\n"
            "m = types.ModuleType('ui.app')\n"
            "m.main = lambda: 7\n"          # 7 = 哨兵退出码
            "sys.modules['ui.app'] = m\n")

    def _run(self, code: str):
        return subprocess.run(
            [sys.executable, "-c", code], cwd=str(ROOT), capture_output=True,
            text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})

    def test_直接运行入口文件(self):
        """`python ui/__main__.py`（IDE 里 Run 的常见形态）不再报相对导入。"""
        code = (self.STUB +
                "import runpy, sys\n"
                "sys.argv = ['ui']\n"
                "runpy.run_path(r'ui/__main__.py', run_name='__main__')\n")
        r = self._run(code)
        self.assertEqual(r.returncode, 7, f"stdout={r.stdout!r} stderr={r.stderr!r}")

    def test_直接运行app模块文件(self):
        """`python ui/app.py` / IDE 直接 Run 该模块：**模块级导入**必须成立。

        这里用 `run_name` 避开 `__main__` 分支（不真开窗口）——要回归的正是模块顶层那些
        `from ui.xxx import ...` 在"当脚本跑"时是否还能解析。
        """
        code = ("import runpy\n"
                "runpy.run_path(r'ui/app.py', run_name='ui_app_probe')\n"
                "print('imported-ok')\n")
        r = self._run(code)
        self.assertEqual(r.returncode, 0, f"stdout={r.stdout!r} stderr={r.stderr!r}")
        self.assertIn("imported-ok", r.stdout)
        self.assertNotIn("ImportError", r.stderr)

    def test_模块方式运行(self):
        """`python -m ui`（把 ui 当包）走的是同一条入口。"""
        code = (self.STUB + "import runpy\n"
                "runpy.run_module('ui', run_name='__main__')\n")
        r = self._run(code)
        self.assertEqual(r.returncode, 7, f"stdout={r.stdout!r} stderr={r.stderr!r}")


@unittest.skipUnless(TK_OK, "无图形环境（Tk 不可用），跳过界面冒烟测试")
class Test界面冒烟(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.app import MoodSocApp
        cls.app = MoodSocApp()
        cls.app.withdraw()                     # 测试时不弹窗
        cls.app.load_paths([SAMPLE])
        cls.app.update_idletasks()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        """每个用例都从"示例排班 + 周期数 1"的干净状态开始（用例之间不串状态）。"""
        app = self.app
        app.cycles_var.set("1")
        app.cycles = 1
        app.load_paths([SAMPLE])
        app.update_idletasks()

    def test_导入后看板与曲线就绪(self):
        app = self.app
        self.assertEqual(len(app.schedule.shifts), 3)
        self.assertEqual(app._total_hours(), Decimal("24"))
        self.assertGreater(len(app.board.slots), 40)          # 看板画出了位置
        self.assertIsNotNone(app.traj)
        self.assertEqual(app.chart.name, app.curve_operator)  # 曲线已绑定干员
        self.assertTrue(app.stats.cget("text"))               # 关键数值已填
        self.assertEqual(len(app.shift_buttons), 3)

    def _slot_for(self, name: str):
        """按干员名取当前看板上的位置控件（班次切换会重建控件，故不能缓存引用）。"""
        return next(s for s in self.app.board.slots if s.operator == name)

    def test_时间滑动改变心情(self):
        """滑块/时间跳转 → 看板心情文字实时变化（这就是需求③）。"""
        app = self.app
        who = "锡人"                      # 三个班次都在排班里，便于跨班比较
        app.set_time(Decimal("0"))
        at0 = self._slot_for(who).mood_label.cget("text")
        app.set_time(Decimal("12"))       # 跳到第二个班次（看板会重建）
        at12 = self._slot_for(who).mood_label.cget("text")
        self.assertTrue(at0 and at12)
        self.assertNotEqual(at0, at12, f"12h 后 {who} 的心情应已变化（都在为 '{at0}'）")
        app._on_scale(6.0)                # 模拟拖动滑块
        self.assertLessEqual(abs(app.current_t - Decimal("6")), Decimal("0.001"))
        app.set_time(Decimal("0"))

    def test_左键换人与右键设心情(self):
        """需求②：位置可换人、可设心情（对话框打桩，其余走真路径）。"""
        from ui import app as app_mod
        app = self.app
        app.set_time(Decimal("0"))
        slot = app.board.slots[0]
        old_ops = len(app.schedule.shifts[0].world.facilities[slot.fac_index].operators)

        orig_op, orig_mood = app_mod.ask_operator, app_mod.ask_mood
        try:
            app_mod.ask_operator = lambda *a, **k: "泡泡"           # 换人
            app.on_slot_left(slot.fac_index, 0)
            self.assertEqual(app.schedule.shifts[0].world.facilities[slot.fac_index]
                             .operators[0].name, "泡泡")
            app_mod.ask_operator = lambda *a, **k: ""               # 清空该位置
            app.on_slot_left(slot.fac_index, 0)
        finally:
            app_mod.ask_operator, app_mod.ask_mood = orig_op, orig_mood
        self.assertLessEqual(len(app.schedule.shifts[0].world.facilities[slot.fac_index].operators),
                             max(old_ops, 1))

        # 右键设心情 → 影响周期起点
        app.set_time(Decimal("0"))
        slot = next(s for s in app.board.slots if s.operator)
        who = slot.operator
        try:
            app_mod.ask_mood = lambda *a, **k: Decimal("6")
            app.on_slot_right(slot.fac_index, slot.slot_index)
        finally:
            app_mod.ask_mood = orig_mood
        self.assertEqual(app.initial_moods.get(who), Decimal("6"))
        self.assertEqual(app.traj.mood_at(who, 0), Decimal("6"))

    def test_对点查询切换干员(self):
        """需求④：选人 → 曲线与该干员的关键数值一起切换。"""
        app = self.app
        names = list(app.op_box.cget("values"))
        target = "巫恋" if "巫恋" in names else names[1]
        app.op_var.set(target)
        app._on_operator_pick()
        self.assertEqual(app.chart.name, target)
        self.assertIn("最低", app.stats.cget("text"))
        self.assertIn("各班最低", app.stats.cget("text"))
        app._step_operator(1)
        self.assertNotEqual(app.chart.name, target)

    def test_班次设置改时长(self):
        """需求①：自设每班时长（打桩返回 8/8/8）→ 周期仍 24h。"""
        from ui import app as app_mod
        app = self.app
        orig = app_mod.ask_shift_hours
        try:
            app_mod.ask_shift_hours = lambda *a, **k: [Decimal("8"), Decimal("8"), Decimal("8")]
            app.edit_shifts()
        finally:
            app_mod.ask_shift_hours = orig
        self.assertEqual([s.hours for s in app.schedule.shifts], [Decimal("8")] * 3)
        self.assertEqual(app.schedule.cycle_hours, Decimal("24"))
        # 复原成 12/6/6，避免影响其它用例
        app_mod.ask_shift_hours = lambda *a, **k: [Decimal("12"), Decimal("6"), Decimal("6")]
        try:
            app.edit_shifts()
        finally:
            app_mod.ask_shift_hours = orig

    def test_全员一览覆盖所有干员(self):
        """需求：**直观看到所有干员**——整个周期出现过的干员都要在「全员一览」里，
        包括只在**别的班次**里上班的那些（看板按当前班次画，本来会看不到他们）。"""
        app = self.app
        names = set(app.traj.names)
        self.assertEqual({c.operator for c in app.roster.chips}, names)
        self.assertEqual(len(app.roster.chips), len(names))
        only_other = [n for n in names if n not in app.schedule.shifts[0].operators]
        self.assertTrue(only_other, "示例排班里应当有只出现在其它班次的干员")
        for n in only_other:
            self.assertIn(n, app.roster.by_name)
            self.assertEqual(app.roster.by_name[n].tag_text, "休")   # 标记为"本班次未排班"

    def test_全员一览点击联动对点与设心情(self):
        """"全员一览"左键 = 对点看曲线，右键 = 设心情。"""
        from ui import app as app_mod
        app = self.app
        names = list(app.op_box.cget("values"))
        target = "歌蕾蒂娅" if "歌蕾蒂娅" in names else names[3]
        app.on_roster_pick(target)
        self.assertEqual(app.chart.name, target)
        self.assertEqual(app.op_var.get(), target)
        self.assertTrue(app.roster.by_name[target].selected)
        orig = app_mod.ask_mood
        try:
            app_mod.ask_mood = lambda *a, **k: Decimal("7")
            app.on_roster_set_mood(target)
        finally:
            app_mod.ask_mood = orig
        self.assertEqual(app.initial_moods.get(target), Decimal("7"))

    def test_看板位置标记与班次一致(self):
        """位置标记（制1/贸3/宿2/中…）应当与当前班次的布局一致。"""
        app = self.app
        app.set_time(Decimal("0"))
        shift = app.schedule.shifts[0]
        tags = app._room_tags(shift)
        # 示例排班（333）：3 间制造站 / 3 间贸易站 / 1 间办公室
        self.assertEqual(tags.get("森蚺"), "制1")
        self.assertEqual(tags.get("结城理"), "制2")
        self.assertEqual(tags.get("巫恋"), "贸3")
        self.assertEqual(tags.get("锡人"), "办")          # 单间设施不带序号
        self.assertEqual(tags.get("八幡海铃"), "中")       # 控制中枢
        self.assertTrue(any(t.startswith("宿") for t in tags.values()))
        for v in app.board.slots:
            if v.operator:
                self.assertIn(v.operator, tags, f"{v.operator} 应在当前班次里")

    def test_播放与关键盘微调(self):
        app = self.app
        app.set_time(Decimal("0"))
        app.toggle_play()
        self.assertTrue(app._playing)
        app.toggle_play()
        self.assertFalse(app._playing)
        app.set_time(Decimal("0"))
        app.nudge(Decimal("0.25"))
        self.assertLessEqual(abs(app.current_t - Decimal("0.25")), Decimal("0.001"))
        app.set_time(Decimal("0"))

    def test_周期数切换(self):
        app = self.app
        app.cycles_var.set("2")
        app._on_cycles()
        self.assertEqual(app._total_hours(), Decimal("48"))
        app.cycles_var.set("1")
        app._on_cycles()
        self.assertEqual(app._total_hours(), Decimal("24"))


class Test看板布局(unittest.TestCase):
    """布局契约：**所有房间要一屏放下**（旧版单列 695px 装不进 577px，必须滚动，
    于是"看到所有干员"成了空话）。这条测试盯着它不要退回去。"""

    def setUp(self):
        if not TK_OK:
            self.skipTest("无图形环境（Tk 不可用）")

    def test_一屏放下全部房间(self):
        from ui.app import MoodSocApp
        app = MoodSocApp()
        app.deiconify()
        try:
            app.load_paths([SAMPLE])
            for _ in range(3):
                app.update()
            board = app.board
            content = board.inner.winfo_reqheight()
            visible = board.canvas.winfo_height()
            if visible < 200:
                self.skipTest("窗口未真实布局（无显示器），跳过高度断言")
            self.assertLessEqual(
                content, visible,
                f"看板内容 {content}px 超过可视 {visible}px —— 又需要滚动了（布局退回单列？）")
            # 房间没有被截断：每个位置都画了出来
            shift = app.schedule.shifts[0]
            expected = {o.name for f in shift.world.facilities for o in f.operators}
            on_board = {s.operator for s in board.slots if s.operator}
            self.assertEqual(on_board, expected)
            self.assertEqual(len(board.slots),
                             sum(max(f.capacity, len(f.operators), 1)
                                 for f in shift.world.facilities))
            # 「全员一览」把整个周期的干员都摆出来了
            self.assertEqual(len(app.roster.chips), len(app.traj.names))
        finally:
            app.destroy()


class Test新增交互(unittest.TestCase):
    """进驻事件开关与小白天说明、播放倍速、滚轮作用域、切换布局的芯片复用。"""

    @classmethod
    def setUpClass(cls):
        if not TK_OK:
            raise unittest.SkipTest("无图形环境（Tk 不可用）")
        from ui.app import MoodSocApp
        cls.app = MoodSocApp()
        cls.app.deiconify()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        app = self.app
        app.cycles_var.set("1")
        app.cycles = 1
        app.load_paths([SAMPLE])
        app.geometry("1560x950")
        for _ in range(2):
            app.update()

    # ------------------------------------------------------- 进驻事件（换心情）
    def test_进驻事件开关有说明且状态可见(self):
        """开关旁的旁注要说清"换不换/换谁/在哪换/要不要等"（小白不打开对话框也能知道）。"""
        from ui import app as app_mod
        app = self.app
        app.entry_events.set(False)
        app._sync_entry_label()
        self.assertIn("不结算", app.entry_detail.cget("text"))
        app.entry_events.set(True)
        app.entry_swap_with = None
        app.entry_scope = "dorm"
        app.entry_force = False
        app.entry_restore_back = True
        app._sync_entry_label()
        self.assertIn("前一位", app.entry_detail.cget("text"))
        app.entry_swap_with = "塞雷娅"
        app._sync_entry_label()
        self.assertIn("塞雷娅", app.entry_detail.cget("text"))
        # 任意位置 + 自动挑 + 等她满 + 位置也换 → 旁注用紧凑写法带出这几项
        app.entry_swap_with = "any"
        app.entry_scope = "anywhere"
        app.entry_force = True
        app.entry_restore_back = False
        app._sync_entry_label()
        text = app.entry_detail.cget("text")
        for token in ("最累的", "任意位置", "等她满", "位置也换"):
            self.assertIn(token, text)
        # 状态栏那一句话也要说全
        summary = app._entry_summary()
        for token in ("全基建最累的那位", "基建任意位置", "位置也对调", "等她回满"):
            self.assertIn(token, summary)

    def test_进驻事件换心情端到端(self):
        """设定「菲亚梅塔 24 / 塞雷娅 6」后：开启并指定对象 → 真的互换。"""
        from ui import app as app_mod
        app = self.app
        preset = {"菲亚梅塔": Decimal("24"), "塞雷娅": Decimal("6")}
        orig_mood, orig_dlg = app_mod.ask_mood, app_mod.ask_entry_event
        try:
            app_mod.ask_mood = lambda parent, who, cur, note="": preset.get(who)
            for who in preset:
                app._ask_and_set_mood(who)
        finally:
            app_mod.ask_mood = orig_mood

        # ① 不开 → 按初始心情
        app.entry_events.set(False)
        app.recompute()
        self.assertEqual(app.traj.mood_at("菲亚梅塔", 0), Decimal("24"))
        self.assertEqual(app.traj.mood_at("塞雷娅", 0), Decimal("6"))

        # ② 开启 + 指定「塞雷娅」（仅同宿舍）→ 互换
        try:
            app_mod.ask_entry_event = lambda *a, **k: (True, "塞雷娅", "dorm", True, False)
            app.edit_entry_events()
        finally:
            app_mod.ask_entry_event = orig_dlg
        self.assertTrue(app.entry_events.get())
        self.assertEqual(app.entry_swap_with, "塞雷娅")
        self.assertEqual(app.entry_scope, "dorm")
        self.assertTrue(app.entry_restore_back)
        self.assertFalse(app.entry_force)
        self.assertEqual(app.traj.mood_at("菲亚梅塔", 0), Decimal("6"))
        self.assertEqual(app.traj.mood_at("塞雷娅", 0), Decimal("24"))
        self.assertTrue([m for m in app.traj.marks if m.kind == "entry"])

        # ③ 关掉 → 复原（记得清掉手动心情，避免影响其它用例）
        app.entry_events.set(False)
        app.initial_moods.clear()
        app.recompute()
        self.assertEqual(app.traj.mood_at("菲亚梅塔", 0), Decimal("24"))

    def test_进驻事件任意位置与自动挑(self):
        """UI 也能走"基建任意位置 + 自动挑最累的 + 等她满 + 位置也换"这条组合。"""
        from ui import app as app_mod
        app = self.app
        preset = {"菲亚梅塔": Decimal("24"), "巫恋": Decimal("1")}
        orig_mood, orig_dlg = app_mod.ask_mood, app_mod.ask_entry_event
        try:
            app_mod.ask_mood = lambda parent, who, cur, note="": preset.get(who)
            for who in preset:
                app._ask_and_set_mood(who)
            app_mod.ask_entry_event = lambda *a, **k: (True, "any", "anywhere", False, True)
            app.edit_entry_events()
        finally:
            app_mod.ask_mood, app_mod.ask_entry_event = orig_mood, orig_dlg
        self.assertEqual(app.entry_swap_with, "any")
        self.assertEqual(app.entry_scope, "anywhere")
        self.assertFalse(app.entry_restore_back)
        self.assertTrue(app.entry_force)
        events = [m for m in app.traj.marks if m.kind == "entry"]
        self.assertTrue(events, "应当发生了一次换心情")
        self.assertTrue(any("自动挑" in m.label for m in events))
        self.assertTrue(any("位置也对调" in m.label for m in events))
        # 收尾：恢复默认，别把状态留给其它用例
        app.entry_events.set(False)
        app.entry_scope = "dorm"
        app.entry_restore_back = True
        app.entry_force = False
        app.entry_swap_with = None
        app.initial_moods.clear()
        app.recompute()

    def test_指定对象不在同宿舍时给出提示(self):
        """示例排班里「塞雷娅」只在部分班次与菲亚梅塔同宿舍 → 其余班次记"未执行"事件。"""
        app = self.app
        app.entry_events.set(True)
        app.entry_swap_with = "塞雷娅"
        app.recompute()
        skipped = [m for m in app.traj.marks
                   if m.kind == "entry" and "不在" in m.label]
        self.assertTrue(skipped, "应当记录「未执行」的事件标记")

    # ------------------------------------------------------- 播放倍速
    def test_播放倍速(self):
        """1x = 1 小时/秒；2x 的推进量约为 1x 的两倍（按真实流逝时间算）。"""
        app = self.app
        measured = {}
        for speed in ("1x", "2x"):
            app.speed_var.set(speed)
            app._on_speed()
            app.set_time(Decimal("0"))
            app.toggle_play()
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 0.6:
                app.update()
                time.sleep(0.003)
            elapsed = time.perf_counter() - t0
            app.toggle_play()
            measured[speed] = (float(app.current_t), elapsed)
        one, two = measured["1x"], measured["2x"]
        self.assertLessEqual(abs(one[0] - one[1]), one[1] * 0.35,
                             f"1x 应约等于 1 小时/秒（实测 {one}）")
        self.assertLessEqual(abs(two[0] / one[0] - 2.0), 0.35,
                             f"2x 应是 1x 的两倍（实测 {measured}）")

    # ------------------------------------------------------- 滚轮作用域
    def test_滚轮只在看板上生效(self):
        """看板内容超出可视区时：滚轮落在看板上要滚，落在全员一览/曲线上不能滚。"""
        app = self.app
        old_min = app.minsize()
        app.minsize(1, 1)                           # 临时放开最小尺寸，好把窗口压矮
        app.geometry("1560x420")                    # 压矮窗口逼出滚动条
        for _ in range(3):
            app.update()
        board = app.board
        if board.inner.winfo_reqheight() <= board.canvas.winfo_height():
            app.minsize(*old_min)
            app.geometry("1560x950")
            self.skipTest("窗口没能压到需要滚动（环境限制）")
        board.canvas.yview_moveto(0)
        app.update()
        start = board.canvas.yview()

        board.canvas.event_generate("<MouseWheel>", delta=-120)
        app.update()
        after_canvas = board.canvas.yview()
        self.assertNotEqual(after_canvas, start, "在看板上滚轮应当滚动")

        chip = next(iter(board._chips.values()))
        chip.event_generate("<MouseWheel>", delta=-120)
        app.update()
        self.assertNotEqual(board.canvas.yview(), after_canvas, "在位置上滚轮也应当滚动")

        before_roster = board.canvas.yview()
        app.roster.chips[0].event_generate("<MouseWheel>", delta=-120)
        app.chart.event_generate("<MouseWheel>", delta=-120)
        app.update()
        self.assertEqual(board.canvas.yview(), before_roster,
                         "滚轮落在全员一览/曲线上时不该动看板")
        app.minsize(*old_min)
        app.geometry("1560x950")
        app.update()

    # ------------------------------------------------------- 切换布局
    def test_切换班次复用芯片(self):
        """房间结构没变时切换班次只换内容，不重建控件（旧写法每次要重建 ~50 个芯片）。"""
        app = self.app
        app.set_time(Decimal("0"))
        app.update()
        chips_before = dict(app.board._chips)
        names_before = {v.operator for v in app.board.slots if v.operator}
        self.assertTrue(chips_before)
        app.set_time(Decimal("12"))                 # 跨到第二个班次
        app.update()
        self.assertEqual(set(app.board._chips), set(chips_before))
        for key, chip in chips_before.items():
            self.assertIs(app.board._chips[key], chip, "同一个位置应当复用同一个芯片对象")
        # 内容确实换过了：表头指向新班次，看板上的人在变
        self.assertIn("Shift 2", app.board.header.cget("text"))
        names_after = {v.operator for v in app.board.slots if v.operator}
        self.assertNotEqual(names_after, names_before)

    def test_时间滑块两侧按钮与步长提示(self):
        """滑块两侧改成纯箭头（原来写 "◀ 15min" 容易被误读成"15 分钟前/时长"），
        步长与快捷键改用右侧一句人话提示。"""
        app = self.app
        app.set_time(Decimal("6"))
        app.back_btn.invoke()
        self.assertEqual(app.current_t, Decimal("5.75"))     # 一档 = 15 分钟
        app.fwd_btn.invoke()
        self.assertEqual(app.current_t, Decimal("6"))
        hint = app.slider_hint.cget("text")
        self.assertIn("分钟", hint)
        self.assertNotIn("min", hint)

    def test_拖动跨班时推迟全员一览刷新(self):
        """拖动中跨班：看板立刻换、全员一览标记延后到停手（避免拖动噎顿）。"""
        app = self.app
        app.set_time(Decimal("0"))
        app.refresh_view()
        self.assertFalse(app._roster_dirty)
        app.set_time(Decimal("12"), quick=True)     # 模拟拖动
        self.assertTrue(app._roster_dirty)
        app._settle_refresh()
        self.assertFalse(app._roster_dirty)


if __name__ == "__main__":
    unittest.main(verbosity=2)

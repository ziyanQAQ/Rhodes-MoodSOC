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
    def test_工具栏不再有换心情开关(self):
        """开关只有**一个**入口（设置框里的「① 开启心情交换」）——工具栏不能再冒出第二个。

        背景：工具栏原来放着一个 `Checkbutton`，而框里又有一个 ①，两处写同一个变量；
        虽然状态永远一致（同一个 `BooleanVar`），但"同一个开关出现两次"看上去像两个打架的设置。
        现在工具栏那颗按钮只负责**打开设置框**，右边的文字只报**当前状态**。
        这条测试盯着它别再回来。
        """
        from tkinter import ttk

        app = self.app
        bar = app.entry_detail.master
        boxes = []
        buttons = []

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, ttk.Checkbutton):
                    boxes.append(str(c))
                elif isinstance(c, ttk.Button):
                    buttons.append(str(c.cget("text")))
                walk(c)

        walk(bar)
        self.assertEqual(boxes, [], "工具栏不该再有换心情开关（唯一入口＝设置框里的 ①）")
        self.assertIn("换心情设置", buttons, "入口按钮要在工具栏上（点开设置框）")
        # 右边界面的状态：关着也写"未开启"，开着写"已开启 · 换谁 [· 等她满]"
        app.entry_events.set(False)
        app._sync_entry_label()
        self.assertEqual(app.entry_detail.cget("text"), "未开启")
        self.assertIn("换心情：未开启", app._entry_status())
        app.entry_events.set(True)
        app.entry_swap_with, app.entry_scope, app.entry_when = None, "dorm", "full"
        app._sync_entry_label()
        self.assertEqual(app.entry_detail.cget("text"), "已开启 · 换前一位进驻")
        self.assertIn("换心情：与同宿舍前一位进驻者互换·没满就不换", app._entry_status())
        app.entry_events.set(False)
        app._sync_entry_label()

    def test_进驻事件开关有说明且状态可见(self):
        """工具栏右侧那串只报"当前状态"：开没开 + 换谁 + 要不要等她满。"""
        app = self.app
        app.entry_events.set(False)
        app._sync_entry_label()
        self.assertEqual(app.entry_detail.cget("text"), "未开启")
        app.entry_events.set(True)
        app.entry_swap_with, app.entry_scope, app.entry_when = None, "dorm", "full"
        app._sync_entry_label()
        self.assertEqual(app.entry_detail.cget("text"), "已开启 · 换前一位进驻")
        app.entry_swap_with = "塞雷娅"
        app._sync_entry_label()
        self.assertEqual(app.entry_detail.cget("text"), "已开启 · 换塞雷娅")
        app.entry_swap_with, app.entry_scope, app.entry_when = "any", "anywhere", "wait"
        app._sync_entry_label()
        self.assertEqual(app.entry_detail.cget("text"), "已开启 · 换最累的 · 等她满")
        # 状态栏那一句话要说全：谁 / 在哪 / 只换心情 / 两种强制口径
        summary = app._entry_summary()
        for token in ("全基建最累的那位", "基建任意位置", "只换心情、位置不动", "等她回满"):
            self.assertIn(token, summary)
        app.entry_when = "full"
        self.assertIn("没勾强制切换", app._entry_summary())
        app.entry_swap_with = None
        app.entry_scope = "dorm"
        app._sync_entry_label()

    def test_进驻事件是一张逐班表(self):
        """设置框＝① 总开关 + **一张表**（每行：用 / 换谁 / 强制切换）——不再有 ②③④ 与折叠区。"""
        import tkinter as tk

        from mood_soc.models import EntryShiftOverride
        from ui.dialogs import EntryEventDialog

        app = self.app
        holders, cands = app._entry_candidates()
        labels = app.schedule.shift_labels()
        dlg = EntryEventDialog(app, True, None, cands, holders, shift_labels=labels)
        try:
            app.update()
            self.assertTrue(dlg.enabled.get())
            self.assertEqual(len(dlg.shift_use), len(labels))
            # 每行预填＝当前全局配置；默认每班都"用"
            self.assertEqual([v.get() for v in dlg.shift_use], [True] * len(labels))
            self.assertEqual([v.get() for v in dlg.shift_who], [EntryEventDialog.PREV] * len(labels))
            self.assertEqual([v.get() for v in dlg.shift_force], [False] * len(labels))
            self.assertNotIn("位置也一起互换", str(dlg.result))
            # 三班完全相同 ⇒ 不产生任何覆盖项（与"没有这张表"逐位相同）
            dlg._ok()
            self.assertEqual(dlg.result[:5], (True, None, "dorm", True, "full"))
            self.assertEqual(dlg.result[5], [])
        finally:
            dlg.destroy()
        # 全选 / 全不选
        dlg2 = EntryEventDialog(app, True, None, cands, holders, shift_labels=labels)
        try:
            app.update()
            dlg2._set_all_shifts(False)
            self.assertEqual([v.get() for v in dlg2.shift_use], [False] * len(labels))
            dlg2._set_all_shifts(True)
            self.assertEqual([v.get() for v in dlg2.shift_use], [True] * len(labels))
        finally:
            dlg2.destroy()
        # 逐班不同：第 1 班换塞雷娅+等她满、第 2 班用默认口径、第 3 班不用
        dlg3 = EntryEventDialog(app, True, None, cands, holders, shift_labels=labels)
        try:
            app.update()
            dlg3.shift_who[0].set("塞雷娅")
            dlg3.shift_force[0].set(True)
            dlg3.shift_use[2].set(False)
            dlg3._ok()
            self.assertEqual(dlg3.result[:5], (True, "塞雷娅", "anywhere", True, "wait"))
            self.assertEqual([(o.key, o.enabled, o.swap_with, o.scope, o.when)
                              for o in dlg3.result[5]],
                             [(2, True, "", "dorm", "full"),      # 与第 1 班不同 → 写一条
                              (3, False, "", "dorm", "full")])    # 第 3 班不用
        finally:
            dlg3.destroy()
        # 载入已有逐班配置 → 逐行预填
        dlg4 = EntryEventDialog(app, True, None, cands, holders, shift_labels=labels,
                                per_shift=[EntryShiftOverride(key=1, swap_with="巫恋", when="wait"),
                                           EntryShiftOverride(key=3, enabled=False)])
        try:
            app.update()
            self.assertEqual([v.get() for v in dlg4.shift_who],
                             ["巫恋", EntryEventDialog.PREV, EntryEventDialog.PREV])
            self.assertEqual([v.get() for v in dlg4.shift_force], [True, False, False])
            self.assertEqual([v.get() for v in dlg4.shift_use], [True, True, False])
        finally:
            dlg4.destroy()
        # 旧配置「立刻换」不再提供：那一行显示为"不勾强制切换"
        dlg6 = EntryEventDialog(app, True, None, cands, holders, when="immediate",
                                shift_labels=labels)
        try:
            app.update()
            self.assertEqual([v.get() for v in dlg6.shift_force], [False, False, False])
        finally:
            dlg6.destroy()
        # ① 关掉 → 整张表置灰
        dlg5 = EntryEventDialog(app, True, None, cands, holders, shift_labels=labels)
        try:
            app.update()
            dlg5.enabled.set(False)
            dlg5._sync()
            ttk_widgets = [w for w in dlg5._shift_widgets if hasattr(w, "instate")]
            tk_chips = [w for w in dlg5._shift_widgets if isinstance(w, tk.Checkbutton)]
            self.assertTrue(ttk_widgets and tk_chips)
            self.assertTrue(all(w.instate(["disabled"]) for w in ttk_widgets))
            self.assertTrue(all(w.cget("state") == "disabled" for w in tk_chips))
        finally:
            dlg5.destroy()

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

        # ② 开启 + 指定「塞雷娅」+ 不勾强制切换（她满 24 → 判定时照换）
        try:
            app_mod.ask_entry_event = lambda *a, **k: (True, "塞雷娅", "anywhere", True, "full")
            app.edit_entry_events()
        finally:
            app_mod.ask_entry_event = orig_dlg
        self.assertTrue(app.entry_events.get())
        self.assertEqual(app.entry_swap_with, "塞雷娅")
        self.assertEqual(app.entry_scope, "anywhere")
        self.assertTrue(app.entry_restore_back)          # 界面固定：只换心情
        self.assertEqual(app.entry_when, "full")
        self.assertEqual(app.traj.mood_at("菲亚梅塔", 0), Decimal("6"))
        self.assertEqual(app.traj.mood_at("塞雷娅", 0), Decimal("24"))
        self.assertTrue([m for m in app.traj.marks if m.kind == "entry"])

        # ③ 她没满心情时：不勾=不换 / 勾了=等她回满再换（同一份「她 10」的起点）
        try:
            app_mod.ask_mood = lambda parent, who, cur, note="": (Decimal("10")
                                                                 if who == "菲亚梅塔" else None)
            app._ask_and_set_mood("菲亚梅塔")
        finally:
            app_mod.ask_mood = orig_mood
        app.entry_when = "full"
        app.recompute()
        self.assertEqual(app.traj.mood_at("菲亚梅塔", 0), Decimal("10"))
        app.entry_when = "wait"
        app.recompute()
        labels = [m.label for m in app.traj.marks if m.kind == "entry"]
        self.assertTrue(any("等她回满再换" in t for t in labels))
        self.assertTrue(any("菲亚梅塔 24 → 24" in t for t in labels), "她回到满心情后才换")

        # 收尾：关掉 + 清掉手动心情，别把状态留给其它用例
        app.entry_events.set(False)
        app.entry_when = "full"
        app.entry_swap_with = None
        app.entry_scope = "dorm"
        app.initial_moods.clear()
        app.recompute()
        self.assertEqual(app.traj.mood_at("菲亚梅塔", 0), Decimal("24"))

    def test_进驻事件任意位置与自动挑(self):
        """UI 也能走「基建任意位置 + 自动挑最累的 + 勾了强制切换」这条组合。"""
        from ui import app as app_mod
        app = self.app
        preset = {"菲亚梅塔": Decimal("24"), "巫恋": Decimal("1")}
        orig_mood, orig_dlg = app_mod.ask_mood, app_mod.ask_entry_event
        try:
            app_mod.ask_mood = lambda parent, who, cur, note="": preset.get(who)
            for who in preset:
                app._ask_and_set_mood(who)
            app_mod.ask_entry_event = lambda *a, **k: (True, "any", "anywhere", True, "wait")
            app.edit_entry_events()
        finally:
            app_mod.ask_mood, app_mod.ask_entry_event = orig_mood, orig_dlg
        self.assertEqual(app.entry_swap_with, "any")
        self.assertEqual(app.entry_scope, "anywhere")
        self.assertTrue(app.entry_restore_back)
        self.assertEqual(app.entry_when, "wait")
        events = [m for m in app.traj.marks if m.kind == "entry"]
        self.assertTrue(events, "应当发生了一次换心情")
        self.assertTrue(any("自动挑" in m.label for m in events))
        self.assertTrue(any("位置不变" in m.label for m in events), "界面固定只换心情")
        # 收尾：恢复默认，别把状态留给其它用例
        app.entry_events.set(False)
        app.entry_scope = "dorm"
        app.entry_restore_back = True
        app.entry_when = "full"
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
    def test_播放速度单位是秒每秒(self):
        """速度单位 = **模拟秒 / 真实秒（s/s）**：`1x` 是实时，`3600x` 是 1 小时/秒。"""
        app = self.app
        measured = {}
        for speed in ("1x", "3600x"):
            app.speed_var.set(speed)
            app._on_speed()
            self.assertEqual(float(app.play_speed), float(speed[:-1]))
            app.set_time(Decimal("0"))
            app.toggle_play()
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 0.6:
                app.update()
                time.sleep(0.003)
            elapsed = time.perf_counter() - t0
            app.toggle_play()
            # 推进量（模拟秒/真实秒）应当 ≈ 标称倍率
            measured[speed] = (float(app.current_t) * 3600 / elapsed, elapsed)
        one, fast = measured["1x"], measured["3600x"]
        self.assertLessEqual(abs(one[0] - 1), 1, f"1x 应约等于 1 模拟秒/秒（实测 {one}）")
        self.assertLessEqual(abs(fast[0] / 3600 - 1), 0.35,
                             f"3600x 应约等于 1 小时/秒（实测 {fast}）")
        # 提示文字要说清这一档是多少
        app.speed_var.set("3600x")
        app._on_speed()
        self.assertIn("小时/秒", app.speed_hint.cget("text"))
        app.speed_var.set("1x")
        app._on_speed()
        self.assertIn("实时", app.speed_hint.cget("text"))

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

    def test_按班次面板与逐班联动(self):
        """表格里改某一班 → 只有那一班写覆盖项，其余班跟随"默认口径"（第 1 个勾选的班）。"""
        from mood_soc.models import EntryShiftOverride
        from ui.dialogs import EntryEventDialog

        app = self.app
        holders, cands = app._entry_candidates()
        labels = app.schedule.shift_labels()
        self.assertEqual(len(labels), 3)
        dlg = EntryEventDialog(app, True, None, cands, holders, shift_labels=labels)
        try:
            app.update()
            dlg.shift_who[1].set("巫恋")          # 只改第 2 班
            dlg._ok()
            self.assertEqual(dlg.result[:5], (True, None, "dorm", True, "full"))
            self.assertEqual([(o.key, o.enabled, o.swap_with, o.when) for o in dlg.result[5]],
                             [(2, True, "巫恋", "full")])
        finally:
            dlg.destroy()
        # 对照：磁盘上那份"第 1 班换巫恋 + 第 3 班不用"的配置能原样跑起来
        app.entry_events.set(True)
        app.entry_scope, app.entry_swap_with, app.entry_when = "anywhere", None, "full"
        app.entry_per_shift = [EntryShiftOverride(key=1, swap_with="巫恋", when="wait"),
                               EntryShiftOverride(key=3, enabled=False)]
        app._sync_entry_label()
        app.recompute()
        self.assertEqual(app.entry_detail.cget("text"), "已开启 · 按班次")
        summary = app._entry_summary()
        self.assertIn("按班次覆盖", summary)
        self.assertIn("3不用", summary)
        self.assertIn("1巫恋", summary)
        self.assertIn("最累的", summary)          # 全局口径：任意位置 + 不点名 = 自动挑
        # 收尾：恢复默认，别把状态留给其它用例
        app.entry_events.set(False)
        app.entry_per_shift = []
        app.entry_scope = "dorm"
        app.initial_moods.clear()
        app.recompute()

    def test_换哪个班多选端到端(self):
        """④ 取消勾选某班 → 那一班不再换心情；勾着的班照换（引擎侧＝逐班 `enabled`）。"""
        from mood_soc.models import EntryShiftOverride

        app = self.app
        app.entry_events.set(True)
        app.entry_swap_with, app.entry_scope, app.entry_when = "塞雷娅", "anywhere", "full"
        app.entry_per_shift = []
        app.recompute()
        self.assertEqual(self._swap_times(app), [Decimal("0"), Decimal("12"), Decimal("18")])
        # ④ 里取消第 3 班（面板上就是那个勾选框 → 一条 enabled=False 的覆盖项）
        app.entry_per_shift = [EntryShiftOverride(key=3, enabled=False)]
        app.recompute()
        self.assertEqual(self._swap_times(app), [Decimal("0"), Decimal("12")])
        # 全不选 ⇒ 一班都不换
        app.entry_per_shift = [EntryShiftOverride(key=i + 1, enabled=False) for i in range(3)]
        app.recompute()
        self.assertEqual(self._swap_times(app), [])
        # 收尾：恢复默认，别把状态留给其它用例
        app.entry_events.set(False)
        app.entry_per_shift = []
        app.entry_swap_with, app.entry_scope = None, "dorm"
        app.initial_moods.clear()
        app.recompute()

    @staticmethod
    def _swap_times(app):
        """轨迹里"真的换了心情"的时刻（跳过"未执行"那类说明）。"""
        return [m.t for m in app.traj.marks if m.kind == "entry" and "互换" in m.label]

    def test_进驻模式三种口径(self):
        """`full`（没满就不换）/ `wait`（等她回满再换）/ `immediate`（连她满不满都不看）。"""
        app = self.app
        app.entry_events.set(True)
        app.entry_swap_with, app.entry_scope = "塞雷娅", "anywhere"
        app.initial_moods = {"菲亚梅塔": Decimal("10")}
        try:
            app.entry_when = "full"
            app.recompute()
            self.assertNotIn(Decimal("0"), self._swap_times(app), "她没满 → 第 1 班不换")
            app.entry_when = "wait"
            app.recompute()
            self.assertTrue(any(Decimal("0") < t < Decimal("12") for t in self._swap_times(app)),
                            "等她回满那一刻才换（落在第 1 班中间）")
            app.entry_when = "immediate"      # 引擎仍支持（界面不再提供）
            app.recompute()
            self.assertEqual(app.traj.mood_at("菲亚梅塔", 0), Decimal("24"))
            self.assertIn(Decimal("0"), self._swap_times(app))
        finally:
            app.entry_events.set(False)
            app.entry_when = "full"
            app.entry_swap_with, app.entry_scope = None, "dorm"
            app.initial_moods.clear()
            app.recompute()

    # ------------------------------------------------------- 闲置入宿
    def test_闲置入宿按周期班次分组(self):
        """表按时间排（周期 → 班次），每组的候选与「换谁」都按**那一刻**算。

        回归：不同周期/班次的候选集合本来就不一样（心情跨班跨周期连续），
        所以表必须逐次展开，而不是"一人一行取最危险那次"。
        """
        app = self.app
        app.cycles_var.set("2")
        app._on_cycles()
        app.update()
        groups = app._idle_groups()
        self.assertGreaterEqual(len(groups), 4)
        scopes = [g[1] for g in groups]
        self.assertEqual(scopes, sorted(scopes), "必须按时间从早到晚排")
        # 不同组的候选集合不一样（至少两组的成员不同）
        sets = {g[1]: {r[0] for r in g[2]} for g in groups}
        self.assertGreater(len({frozenset(s) for s in sets.values()}), 1)
        # 同一个班次在不同周期里连数值都不同（心情跨周期连续）
        moods = {g[1]: {r[0]: r[1] for r in g[2]} for g in groups}
        self.assertIn((1, 2), moods)
        self.assertIn((2, 2), moods)
        self.assertIn("虎狼丸", moods[(1, 2)])
        self.assertIn("虎狼丸", moods[(2, 2)])
        self.assertNotEqual(moods[(1, 2)]["虎狼丸"], moods[(2, 2)]["虎狼丸"])
        # 「换谁」的可选项也是按那一刻算的（不是全局并集）
        first_of = {g[1]: frozenset(g[2][0][5]) for g in groups if g[2]}
        self.assertTrue(first_of)
        self.assertGreater(len(set(first_of.values())), 1)
        app.cycles_var.set("1")
        app._on_cycles()
        app.update()

    def test_闲置入宿改动实时生效(self):
        """对话框里改动 → 回调把设置套进模拟重算 → 表与主界面一起刷新；取消则回滚。"""
        from ui.dialogs import IdleToDormDialog

        app = self.app
        groups = app._idle_groups()
        self.assertTrue(groups)
        title, scope, rows = groups[0]
        name = rows[0][0]
        before = app.traj.mood_at(name, 24)

        seen = []

        def live(enabled, entries):
            seen.append((enabled, dict(entries)))
            app.idle_to_dorm.set(bool(enabled))
            app.idle_entries = dict(entries)
            app.recompute()                       # 主界面（看板/曲线/状态栏）跟着刷新
            return app._idle_groups()

        snapshot = (app.idle_to_dorm.get(), dict(app.idle_entries))
        dlg = IdleToDormDialog(app, False, groups, on_change=live)
        try:
            app.update()
            self.assertEqual(len(dlg._rows), sum(len(g[2]) for g in groups))
            dlg.enabled.set(True)
            dlg._on_toggle()
            dlg._rebuild()                        # 不等防抖，直接重建
            self.assertTrue(seen, "改动必须回调（实时重算）")
            self.assertTrue(app.idle_to_dorm.get())
            self.assertIn("闲置入宿：已开启", app._idle_status())
            self.assertTrue([m for m in app.traj.marks if m.kind == "idle"])
            self.assertGreater(app.traj.mood_at(name, 24), before)   # 入宿后心情变好
            dlg._ok()                             # 「应用」：拿到逐次设置
            enabled, entries = dlg.result
        finally:
            dlg.destroy()
        self.assertTrue(enabled)
        self.assertTrue(entries, "应当有设置项")
        self.assertTrue(all(len(k) == 3 for k in entries), "键是 (周期, 班次, 干员)")
        # 收尾：回滚
        app.idle_to_dorm.set(snapshot[0])
        app.idle_entries = snapshot[1]
        app._sync_idle_label()
        app.recompute()
        self.assertEqual(app.idle_detail.cget("text"), "未开启")

    def test_闲置入宿可选空位(self):
        """「去哪／与谁换」里同时有空位（宿舍01、宿舍02…）与满心情的人；选空位就进那间。"""
        app = self.app
        app.cycles_var.set("2")
        app._on_cycles()
        app.idle_to_dorm.set(True)
        app.recompute()
        app.update()
        groups = app._idle_groups()
        # 至少有一组同时给出"空位"与"人"两类选项，且空位标签是「宿舍NN」
        slot_rows = [r for _t, _s, rows in groups for r in rows
                     if any(o.startswith("宿舍") for o in r[5])]
        self.assertTrue(slot_rows, "有空位的时刻应当给出宿舍空位选项")
        labels = [o for o in slot_rows[0][5] if o.startswith("宿舍")]
        self.assertTrue(all(len(x) == 4 and x[2:].isdigit() for x in labels), labels)
        name = slot_rows[0][0]
        scope = next(s for t, s, rows in groups if any(r[0] == name and r[5] == slot_rows[0][5]
                                                       for r in rows))
        # 关掉总开关 → 没有空位选项可谈（表还在，但引擎不结算）
        app.idle_to_dorm.set(False)
        app.recompute()
        self.assertFalse([m for m in app.traj.marks if m.kind == "idle"])
        # 选一个空位 → 那一位真的按指定的宿舍进
        app.idle_to_dorm.set(True)
        app.idle_entries = {(scope[0], scope[1], name): (True, labels[0])}
        app.recompute()
        evs = [m for m in app.traj.marks if m.kind == "idle" and name in m.label]
        self.assertTrue(evs)
        self.assertTrue(any(labels[0] in m.label for m in evs), [m.label for m in evs])
        from ui.app import _dorm_index_of
        self.assertEqual(_dorm_index_of(labels[0]), int(labels[0][2:]))
        entries = app._idle_entry_list()
        self.assertTrue(any(e.dorm == int(labels[0][2:]) for e in entries))
        # 收尾：恢复默认
        app.idle_to_dorm.set(False)
        app.idle_entries = {}
        app.cycles_var.set("1")
        app._on_cycles()
        app.recompute()

    def test_空格只管播放(self):
        """空格在任何焦点下都只切换播放/暂停：不会"按下"工具栏按钮（曾经会又开一次设置框）。

        背景：`ttk.Button` 自带 `<Key-space>` 类绑定＝"按下当前聚焦的按钮"，所以点过
        「换心情设置」之后按空格会再弹一次那个模态框；现在主窗口里的控件都挂了
        widget 级 `<space>`（播放/暂停 + `break`），而且工具栏按钮不参与 Tab 焦点。
        """
        from tkinter import ttk

        app = self.app
        bar = app.entry_detail.master
        btns = {}

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, ttk.Button):
                    btns[c.cget("text")] = c
                walk(c)

        walk(bar)
        self.assertIn("换心情设置", btns)
        tabbable = [t for t, b in btns.items() if str(b.cget("takefocus")) not in ("0", "False")]
        self.assertEqual(tabbable, [], "工具栏按钮不该参与 Tab 焦点")

        opened = []

        def collect():
            opened.extend(w for w in app.winfo_children() if w.winfo_class() == "Toplevel")
            for w in list(opened):
                w.destroy()

        btn = btns["换心情设置"]
        before = app._playing
        btn.focus_force()
        app.update()
        app.after(0, lambda: btn.event_generate("<space>"))
        app.after(300, collect)                      # 万一真弹了框，这里把它收掉（不会卡住）
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 0.8:
            app.update()
            time.sleep(0.01)
        self.assertEqual(opened, [], "空格不该触发工具栏按钮（尤其是又开一次设置框）")
        self.assertNotEqual(app._playing, before, "空格应当仍然是播放/暂停")
        if app._playing:
            app.toggle_play()                        # 复原，别把状态留给其它用例
        app.focus_set()

    def test_曲线刻度不重叠且跨天另标天数(self):
        """横轴刻度只写 `HH:MM`（不带「（第2天）」），跨天另起一行标「第N天」。

        回归：刻度最后一条落在周期末尾，`fmt_clock(24)` 会返回 `00:00（第2天）`（约 90px），
        塞进 ~43px 的刻度间距里会和左右两条叠字。
        """
        import re

        app = self.app
        app.cycles_var.set("2")
        app._on_cycles()
        app.update()
        chart = app.chart
        chart.set_data(app.traj, app.curve_operator)
        app.update()
        texts = [(i, chart.itemcget(i, "text")) for i in chart.find_all()
                 if chart.type(i) == "text"]
        ticks = [(i, t) for i, t in texts if re.fullmatch(r"\d{2}:\d{2}", t)]
        days = [t for _i, t in texts if re.fullmatch(r"第\d+天", t)]
        self.assertGreaterEqual(len(ticks), 4)
        self.assertEqual(days, ["第1天", "第2天"], "跨两个周期应当标出两天")
        boxes = sorted((chart.bbox(i)[0], chart.bbox(i)[2]) for i, _t in ticks)
        for (a0, a1), (b0, b1) in zip(boxes, boxes[1:]):
            self.assertLessEqual(a1, b0, f"刻度标签重叠了：{boxes}")
        # 单周期时不该出现天数那一行
        app.cycles_var.set("1")
        app._on_cycles()
        app.update()
        chart.redraw()
        app.update()
        texts1 = [chart.itemcget(i, "text") for i in chart.find_all() if chart.type(i) == "text"]
        self.assertFalse([t for t in texts1 if re.fullmatch(r"第\d+天", t)])
        self.assertTrue(all(not t.startswith("00:00（") for t in texts1), texts1)

    def test_图下显示此刻与本班速率(self):
        """曲线图下方的关键数值里要能看到"心情增加/下降的速度"（此刻 + 本班平均）。"""
        app = self.app
        app.set_time(Decimal("3"))
        app._update_chart()
        text = app.stats.cget("text")
        self.assertIn("此刻", text)
        self.assertIn("/时", text)
        self.assertIn("本班平均", text)
        # 悬停读数里也带上速率
        chart = app.chart
        chart.event_generate("<Motion>", x=int(chart.winfo_width() // 2), y=60)
        app.update()
        hover = [chart.itemcget(i, "text") for i in chart.find_withtag("hover")
                 if chart.type(i) == "text"]
        self.assertTrue(hover and any("速率" in t for t in hover), hover)

    def test_速率读数随时间实时更新(self):
        """拖滑块换时刻 → 图下的「此刻速率 / 本班平均（班次名）」跟着变（曾经是死的）。

        回归：stats 只在"换干员 / 重算"时才更新，所以拖滑块时那两行一直停在旧值上，
        连"本班平均（Shift N）"都不会跟着班次走。
        """
        app = self.app
        app.set_time(Decimal("3"))
        app.update()
        first = app.stats.cget("text").split("\n")[0]
        app.set_time(Decimal("13"))                    # 跨到第 2 班
        app.update()
        second = app.stats.cget("text").split("\n")[0]
        self.assertIn("此刻", first)
        self.assertIn("/时", first)
        self.assertNotEqual(first, second, "换时刻后「此刻 / 本班平均」应当跟着变")
        self.assertIn(app.schedule.shifts[1].label, second, "班次名也要跟上")
        app.set_time(Decimal("0"))
        app.update()

    def test_房间等级可改且容量跟着变(self):
        """点看板房间卡头 → 改等级 → 容量/校验结果跟着变（上游 phases[lv].maxStationedNum）。"""
        from ui import app as app_mod

        app = self.app
        app.set_time(Decimal("0"))
        shift = app.schedule.shifts[0]
        idx = next(i for i, f in enumerate(shift.world.facilities)
                   if f.ftype.name == "MANUFACTURING")
        self.assertEqual(shift.world.facilities[idx].level, 3)
        orig = app_mod.ask_level
        try:
            app_mod.ask_level = lambda *a, **k: 2          # 制造站#1 → Lv2
            app.on_room_left(idx)
        finally:
            app_mod.ask_level = orig
        fac = app.schedule.shifts[0].world.facilities[idx]
        self.assertEqual(fac.level, 2)
        self.assertEqual(fac.capacity, 2)
        self.assertIn("Lv2", app.status.cget("text"))
        # 3 个人塞进容量 2 的房间 → 自检要报出来（看板卡头也会标红）
        self.assertIn("超过 Lv2 容量 2 人", app._layout_issues())
        # 改回去
        try:
            app_mod.ask_level = lambda *a, **k: 3
            app.on_room_left(idx)
        finally:
            app_mod.ask_level = orig
        self.assertEqual(app.schedule.shifts[0].world.facilities[idx].capacity, 3)
        self.assertEqual(app._layout_issues(), "")

    def test_批量设置能改房间等级(self):
        """批量设置的「房间等级」区：改等级 → 表格行数/容量按新等级算，应用后落到模型。"""
        import tkinter as tk

        from ui.batch import BatchDialog

        app = self.app
        dlg = BatchDialog(app, app.schedule, shift_index=0, initial_moods={},
                          imported_moods={}, moods_now={})
        try:
            app.update()
            self.assertEqual(len(dlg._level_vars), len(dlg._fac_names))
            self.assertIn("9/9", dlg.level_note.cget("text"))     # 示例排班正好用满 9 个建造位
            ftype = app.schedule.shifts[0].world.facilities[1].ftype
            from mood_soc.config import facility_slots
            dlg._level_vars[1].set("2")
            dlg._on_level_change(1, dlg._level_vars[1])
            app.update()
            cap = dlg._slot_count(dlg._fac_names[1], 1)
            self.assertGreaterEqual(cap, facility_slots(ftype, 2))
            dlg._ok()
            changes, _moods = dlg.result
        finally:
            dlg.destroy()
        self.assertIn(0, changes)
        self.assertEqual(changes[0][1]["level"], 2)

    def test_精英化角标与练度摘要(self):
        """练度（精英化）要"看得见"：看板 / 全员一览的芯片带 `E1` 角标，
        对点查询说明"少算了哪条技能、为什么"。"""
        app = self.app
        app.set_time(Decimal("0"))
        self.assertEqual(app._elite_badges(), {})          # 缺省口径＝E2 满练 → 无角标
        # 把卡夫卡（「手工艺品·β」要 E2）降成 E1
        facs = app._facilities_of(0)
        for f in facs:
            f["operators"] = [({"name": n, "elite": 1} if n == "卡夫卡" else n)
                              for n in f.get("operators", [])]
        app._apply_facilities(0, facs)
        app.update()
        self.assertEqual(app._elite_badges().get("卡夫卡"), "E1")
        board_chip = next(s for s in app.board.slots if s.operator == "卡夫卡")
        self.assertIn("E1", board_chip.chip.name.cget("text"))
        self.assertIn("E1", app.roster.by_name["卡夫卡"].name.cget("text"))
        # 对点查询的练度摘要：要说清"少了几条、缺在哪一档"
        app.on_roster_pick("卡夫卡")
        text = app.stats.cget("text")
        self.assertIn("练度 E1", text)
        self.assertIn("已解锁", text)
        self.assertIn("手工艺品·β", text)
        self.assertIn("E2", text)
        # 改回满练 → 角标消失、摘要不再告警
        facs = app._facilities_of(0)
        for f in facs:
            f["operators"] = [n.get("name", "") if isinstance(n, dict) else n
                              for n in f.get("operators", [])]
        app._apply_facilities(0, facs)
        app.update()
        self.assertEqual(app._elite_badges(), {})
        self.assertNotIn("因未满练少", app.stats.cget("text"))

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

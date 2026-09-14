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


if __name__ == "__main__":
    unittest.main(verbosity=2)

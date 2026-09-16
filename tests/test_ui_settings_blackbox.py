"""tests/test_ui_settings_blackbox.py —— 「设置」中心的黑盒测试（真要建窗口）。

盯住"**统一大小 + 丝滑切换**"这条要求的可验证部分：

| 断言 | 为什么值得盯 |
|---|---|
| 四个分区都装得进**固定尺寸**的内容区 | 装不进就得让窗口跟着内容缩放，切换就会一跳一跳 |
| 切页**不重建**面板（对象是同一个） | 「干员与心情」那页有 50 行控件，重建 ~290ms，就是"切页一顿"的来源 |
| 切页**不改窗口尺寸** | 尺寸一变，人的视线就要重新找位置 |
| 切页平均耗时够低 | "丝滑"的量化口径（缓存命中应当 < 几十毫秒） |
| 时间轴改动会把别的分区**标脏** | 班次数量/时长变了，别人的班次下拉必须重建（否则拿到旧排班） |
| 干员池进了设置中心 | 导入 v4 蓝图后要能选到池里的人、一键铺位置 |

无图形环境（Tk 建不出来）时整体跳过。

运行：.venv/Scripts/python.exe -m unittest tests.test_ui_settings_blackbox -v
"""
from __future__ import annotations

import time
import tkinter as tk
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "resources" / "arknights-infra-schedule-maa.json"
V4 = ROOT / "resources" / "import_v4_input.json"


def _tk_available() -> bool:
    try:
        r = tk.Tk()
        r.withdraw()
        r.destroy()
        return True
    except Exception:            # noqa: BLE001 —— 无显示器/无 Tk 都视作不可用
        return False


TK_OK = _tk_available()


@unittest.skipUnless(TK_OK, "无图形环境（Tk 不可用），跳过设置中心测试")
class Test设置中心(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.app import MoodSocApp
        cls.app = MoodSocApp()
        cls.app.deiconify()
        cls.app.load_paths([SAMPLE])
        cls.app.geometry("1560x950")
        for _ in range(2):
            cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        app = self.app
        app.cycles_var.set("1")
        app.cycles = 1
        app.load_paths([SAMPLE])
        for _ in range(2):
            app.update()
        self.dlg = None

    def tearDown(self):
        if self.dlg is not None and self.dlg.winfo_exists():
            self.dlg.destroy()
        self.app.settings_dlg = None

    def _open(self, page=None):
        self.dlg = self.app.open_settings(page)
        self.app.update()
        return self.dlg

    # ------------------------------------------------------------- 统一大小
    def test_四个分区都装得进固定内容区(self):
        """每个分区的**自然尺寸**都不超过内容区（超了就得让窗口缩放/裁剪 → 切换会跳、内容会看不见）。

        ⚠️ **宽度也要量**：只量高度会漏掉「房间等级区一行塞 17 间房要 2131px」这种溢出
        （右侧几间房的等级会被裁掉、根本改不了）。
        """
        from ui.settings import PAGES, PAGE_H

        dlg = self._open()
        host_w = dlg.host.winfo_width()
        for key, title, _desc in PAGES:
            dlg.open_page(key)
            self.app.update()
            need_w, need_h = dlg.panel.winfo_reqwidth(), dlg.panel.winfo_reqheight()
            self.assertLessEqual(need_w, host_w,
                                 f"「{title}」需要 {need_w}px 宽，超过内容区 {host_w}px")
            self.assertLessEqual(need_h, PAGE_H,
                                 f"「{title}」需要 {need_h}px 高，超过内容区 {PAGE_H}px")
            self.assertNotIn("⚠", dlg.note.cget("text"), f"「{title}」触发了装不下的提示")

    def test_二十二间房也不溢出(self):
        """极值用例：一间不落的全设施（22 间）——等级区是网格，行数会长但**不许超宽**。"""
        import json
        import tempfile
        from ui.settings import PAGE_H

        def fac(t, lv):
            return {"type": t, "level": lv, "operators": []}

        facs = ([fac("控制中枢", 5)] + [fac("制造站", 3) for _ in range(5)]
                + [fac("贸易站", 3) for _ in range(5)] + [fac("发电站", 1) for _ in range(3)]
                + [fac("宿舍", 5) for _ in range(4)]
                + [fac("办公室", 3), fac("会客室", 2), fac("训练室", 3), fac("加工站", 1)])
        tmp = Path(tempfile.gettempdir()) / "moodsoc_wide_scenario.json"
        tmp.write_text(json.dumps({"facilities": facs}, ensure_ascii=False), encoding="utf-8")
        self.app.load_paths([tmp])
        self.app.update()

        dlg = self._open("batch")
        self.assertEqual(len(dlg.panel._level_vars), 22)
        self.assertLessEqual(dlg.panel.winfo_reqwidth(), dlg.host.winfo_width())
        self.assertLessEqual(dlg.panel.winfo_reqheight(), PAGE_H)
        # 等级区排成网格：每行 5 间 → 22 间要 5 行
        from ui.batch import LEVEL_COLS

        grid = dlg.panel.level_note.master.winfo_children()[0]
        rows = {int(w.grid_info()["row"]) for w in grid.winfo_children()}
        self.assertEqual(max(rows) + 1, -(-22 // LEVEL_COLS))

    def test_切页不改窗口尺寸(self):
        from ui.settings import PAGES

        dlg = self._open()
        size = dlg.geometry().split("+")[0]
        for key, title, _desc in PAGES:
            dlg.open_page(key)
            self.app.update()
            self.assertEqual(dlg.geometry().split("+")[0], size,
                             f"切到「{title}」之后窗口尺寸变了")
        self.assertEqual(dlg.host.winfo_height(), __import__("ui.settings",
                                                             fromlist=["x"]).PAGE_H)

    # ------------------------------------------------------------- 丝滑切换
    def test_切页不重建面板(self):
        """缓存命中 → 同一个 Panel 对象（没被 destroy 重建）。"""
        dlg = self._open("batch")
        first = dlg.panel
        first_id = str(first)
        dlg.open_page("entry")
        self.app.update()
        entry = dlg.panel
        dlg.open_page("batch")
        self.app.update()
        self.assertIs(dlg.panel, first, "切回来应当是同一个面板对象（缓存命中）")
        self.assertEqual(str(dlg.panel), first_id)
        self.assertTrue(entry.winfo_exists(), "另一个分区也不该被销毁")
        self.assertEqual(len(dlg._pages), 2)

    def test_切页平均耗时够低(self):
        """缓存命中后：切页本身只花**毫秒级**（重建一页要 ~150ms，差两个数量级）。

        - `open_page` 只做"place 一次 + tkraise" → 实测 < 1ms；
        - 后面那次 `update()` 是 Tk 自己把新页重绘出来（「干员与心情」那页 400+ 控件），
          实测 ~16ms——这是 Tk 的绘制成本，不是我们重建了控件（那条由
          `test_切页不重建面板` 盯着）。
        """
        from ui.settings import PAGES

        dlg = self._open()
        keys = [k for k, _t, _d in PAGES]
        for key in keys:                              # 先各建一次（首次会慢）
            dlg.open_page(key)
            self.app.update()

        rounds = 5
        t0 = time.perf_counter()
        for _ in range(rounds):
            for key in keys:
                dlg.open_page(key)
        only = (time.perf_counter() - t0) * 1000 / (rounds * len(keys))
        self.assertLess(only, 5.0, f"切页本身 {only:.2f}ms，太慢（像是又在重建）")

        t0 = time.perf_counter()
        for _ in range(rounds):
            for key in keys:
                dlg.open_page(key)
                self.app.update()
        full = (time.perf_counter() - t0) * 1000 / (rounds * len(keys))
        self.assertLess(full, 60.0, f"切页+重绘 {full:.1f}ms，超过一帧的量级了")

    def test_导航当前项高亮(self):
        from ui import theme

        dlg = self._open("entry")
        self.assertEqual(dlg._nav_btn["entry"].cget("fg"), theme.ACCENT)
        self.assertEqual(dlg._nav_bar["entry"].cget("bg"), theme.ACCENT)
        self.assertIn("bold", dlg._nav_btn["entry"].cget("font"))
        for other in ("timeline", "batch", "idle"):
            self.assertEqual(dlg._nav_btn[other].cget("fg"), theme.TEXT)
            self.assertEqual(dlg._nav_bar[other].cget("bg"), theme.PANEL)

    # ------------------------------------------------------------- 失效与重建
    def test_时间轴改动让别的分区失效(self):
        """改班次时长 → 「干员与心情 / 换心情 / 闲置入宿」下次进入时重建（它们是旧排班）。"""
        dlg = self._open("timeline")
        dlg.open_page("batch")
        self.app.update()
        stale = dlg.panel
        dlg.open_page("timeline")
        self.app.update()
        for i, v in enumerate(("8", "8", "8")):
            dlg.timeline.rows[i].set(v)
        self.app.update()
        self.assertIn("batch", dlg._dirty)
        dlg.open_page("batch")
        self.app.update()
        self.assertIsNot(dlg.panel, stale, "标脏过的分区必须重建（否则表格还是旧时长）")
        self.assertFalse(stale.winfo_exists())
        # 复原
        for i, v in enumerate(("12", "6", "6")):
            dlg.timeline.rows[i].set(v)
        self.app.update()

    def test_周期数改动让闲置入宿失效(self):
        """「闲置入宿」的逐次表按周期展开 —— 周期数一变就旧了，要标脏。

        （当前显示的那一页不标脏：它是改动来源。所以这里先切到别的分区。）
        """
        dlg = self._open("batch")
        self.assertNotIn("idle", dlg._dirty)
        self.app.cycles_var.set("2")
        self.app.on_cycles_changed()
        self.app.update()
        self.assertIn("idle", dlg._dirty)
        self.app.cycles_var.set("1")
        self.app.on_cycles_changed()
        self.app.update()

    def test_设置窗口是单例(self):
        dlg = self._open("timeline")
        again = self.app.open_settings("idle")
        self.assertIs(again, dlg, "重复点「设置…」应当把已有窗口提到前面，而不是再开一个")
        self.assertEqual(again.page, "idle", "已经开着就切到指定分区")

    # ------------------------------------------------------------- 滚轮
    def test_两张表都能用滚轮滚动(self):
        """「干员与心情」与「闲置入宿」的表：悬停在表格/行/**滚动条本体**上，滚轮都要生效。

        （闲置入宿那张表原先**完全没绑滚轮**，只能拖滚动条；滚动条本体也没绑。）
        """
        for key in ("batch", "idle"):
            if key == "idle":
                # 闲置入宿的逐次表按周期展开 —— 跑 3 个周期才有得滚（否则会跳过这条断言）
                self.app.cycles_var.set("3")
                self.app.on_cycles_changed()
                self.app.update()
            dlg = self._open(key)
            panel = dlg.panel
            canvas = panel.canvas
            canvas.yview_moveto(0)
            self.app.update()
            self.assertGreater(panel.inner.winfo_reqheight(), canvas.winfo_height(),
                               f"{key}：这张表没能撑到需要滚动，用例白跑")
            before = canvas.yview()
            canvas.event_generate("<MouseWheel>", delta=-120)      # ① 表格本体
            self.app.update()
            self.assertNotEqual(canvas.yview(), before, f"{key}：表格上滚轮没生效")
            row = panel.inner.winfo_children()[0]
            mid = canvas.yview()
            row.event_generate("<MouseWheel>", delta=-120)         # ② 表格里的行
            self.app.update()
            self.assertNotEqual(canvas.yview(), mid, f"{key}：行上滚轮没生效")
            mid2 = canvas.yview()
            panel.scroll.event_generate("<MouseWheel>", delta=-120)  # ③ 滚动条本体
            self.app.update()
            self.assertNotEqual(canvas.yview(), mid2, f"{key}：滚动条上滚轮没生效")
            dlg.destroy()
            self.app.settings_dlg = None

    def test_装得下时滚轮不吃掉事件(self):
        """内容装得下 → 滚轮返回 None（不 break），免得"滚了没反应"还挡住了别的处理。"""
        dlg = self._open("batch")
        panel = dlg.panel
        if panel.inner.winfo_reqheight() <= panel.canvas.winfo_height():
            self.assertIsNone(panel.vs.on_wheel(type("E", (), {"delta": -120})()))
        else:
            self.assertEqual(panel.vs.on_wheel(type("E", (), {"delta": -120})()), "break")


@unittest.skipUnless(TK_OK, "无图形环境（Tk 不可用），跳过设置中心测试")
class Test干员池进设置(unittest.TestCase):
    """导入 v4 蓝图（房间空 + 有干员池）之后，「干员与心情」要能用池。"""

    @classmethod
    def setUpClass(cls):
        from ui.app import MoodSocApp
        cls.app = MoodSocApp()
        cls.app.deiconify()
        cls.app.geometry("1560x950")

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.app.load_paths([V4])
        for _ in range(2):
            self.app.update()
        self.dlg = None

    def tearDown(self):
        if self.dlg is not None and self.dlg.winfo_exists():
            self.dlg.destroy()
        self.app.settings_dlg = None

    def test_导入后房间是空的但池可用(self):
        app = self.app
        self.assertTrue(app.operator_pool, "v4 蓝图应当带来干员池")
        self.assertEqual([p["name"] for p in app.operator_pool][:2], ["阿米娅", "能天使"])
        # 房间里没人（该格式本来就没有"谁在哪"）
        self.assertEqual(app.schedule.operator_names(), [])
        self.assertIn("v4 蓝图+干员池", app.status.cget("text"))

    def test_一键从池中依次填入(self):
        app = self.app
        self.dlg = app.open_settings("batch")
        app.update()
        panel = self.dlg.panel
        self.assertIn("干员池", panel.pool_note.cget("text"))
        panel._fill_from_pool()
        app.update()
        names = app.schedule.operator_names()
        self.assertTrue(names, "一键填入之后排班里应当有人")
        self.assertEqual(names[0], "阿米娅", "按池的顺序铺")
        # 池里的练度也带上了（阿米娅 E2/90 → 满练口径，写回就是纯名字）
        self.assertIn("已按池顺序填入", panel.err.cget("text"))

    def test_池里练度不足的干员写回成对象(self):
        app = self.app
        self.dlg = app.open_settings("batch")
        app.update()
        panel = self.dlg.panel
        panel._apply_names(["巫恋"], clear_first=True)      # 池里是 E1 / 50 级
        app.update()
        facs = app.schedule.shifts[0].facilities
        specs = [o for f in facs for o in f.get("operators", []) if isinstance(o, dict)]
        self.assertEqual(specs, [{"name": "巫恋", "elite": 1, "level": 50}])
        op = app.schedule.shifts[0].world.get_operator("巫恋")
        self.assertEqual((int(op.elite), int(op.level)), (1, 50))


if __name__ == "__main__":
    unittest.main(verbosity=2)

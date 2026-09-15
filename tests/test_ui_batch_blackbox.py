"""tests/test_ui_batch_blackbox.py —— 「批量设置」对话框的黑盒测试（真要建窗口）。

它盯着这条需求的接线：**当前布局的所有干员 + 心情，一张表一次改完**。
覆盖表格口径（行数＝当前班次的位置数、心情预填＝周期起点）、五个心情批量动作、
粘贴名单（按房间顺序 + 去重）、越界拒绝、班次之间互不串改，以及 `app.batch_edit()`
的端到端接线（对话框打桩，其余走真实代码路径）。

无图形环境（Tk 建不出来，如无桌面的 CI）时整体跳过。

运行：.venv/Scripts/python.exe -m unittest tests.test_ui_batch_blackbox -v
"""
from __future__ import annotations

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


class Test名单拆分(unittest.TestCase):
    """`split_names` 是纯函数（不依赖 Tk）：换行 / 逗号 / 顿号 / 空格都算分隔符。"""

    def test_各种分隔符(self):
        from ui.batch import split_names

        self.assertEqual(split_names("森蚺,温蒂\n清流、水月  炎熔"),
                         ["森蚺", "温蒂", "清流", "水月", "炎熔"])
        self.assertEqual(split_names("  森蚺 \n\n 温蒂  "), ["森蚺", "温蒂"])
        self.assertEqual(split_names(""), [])


@unittest.skipUnless(TK_OK, "无图形环境（Tk 不可用），跳过批量设置测试")
class Test批量设置(unittest.TestCase):
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
        self.top = None

    def tearDown(self):
        if self.top is not None and self.top.winfo_exists():
            self.top.destroy()

    # ------------------------------------------------------------- 工具
    def _open(self, shift_index: int = 0, moods=None, moods_now=None):
        """建一个「批量设置」面板并挂到测试用的窗口上。

        （面板＝设置中心「干员与心情」分区的内容本体；它自己是 Frame，
        所以这里给它一个 Toplevel 当宿主。）
        """
        from ui.batch import BatchPanel
        from ui.schedule import default_initial_moods

        app = self.app
        self.top = tk.Toplevel(app)
        self.top.withdraw()
        panel = BatchPanel(self.top, app.schedule, shift_index=shift_index,
                           initial_moods=(moods or {}),
                           imported_moods=default_initial_moods(app.schedule),
                           moods_now=(moods_now if moods_now is not None
                                      else app.traj.moods_at(Decimal("0"))),
                           current_t=app.current_t)
        panel.pack(fill="both", expand=True)
        app.update()
        return panel

    def _ops_of(self, changes):
        """把 `{班次下标: 布局}` 里所有干员按班次顺序摊平。"""
        return [n for i in sorted(changes) for f in changes[i] for n in f.get("operators", [])]

    # ------------------------------------------------------------- 表格
    def test_表格覆盖当前班次的全部位置(self):
        """行数＝该班次的位置总数（容量之外若还有人也要画出来）；心情列＝周期起点。"""
        app = self.app
        dlg = self._open(0)
        shift = app.schedule.shifts[0]
        expected = sum(max(f.capacity, len(f.operators), 1) for f in shift.world.facilities)
        self.assertEqual(len(dlg._cells), expected)
        self.assertEqual(len(dlg._mood_vars), len(shift.operators))
        self.assertEqual(set(dlg._mood_vars), set(shift.operators))
        # 预填＝当前起点（缺省 24；没有手动设过的心情）
        self.assertEqual(dlg._mood_vars["菲亚梅塔"].get(), "24")
        # 房间名按模型口径（带序号的如 制造站#2）
        names = [dlg._room_name(i) for i in range(len(dlg._fac_names))]
        self.assertIn("制造站#1", names)
        self.assertIn("控制中枢", names)

    def test_预填手动设过的心情(self):
        """手动设过"塞雷娅 6" → 表格里就该是 6（而不是导入值 24）。"""
        dlg = self._open(0, moods={"塞雷娅": Decimal("6")})
        self.assertEqual(dlg._mood_vars["塞雷娅"].get(), "6")

    # ------------------------------------------------------------- 心情批量
    def test_全部满心情与全部零(self):
        """两个一键：全部 24 / 全部 0 → 覆盖**整个排班**的干员（不只当前班次）。"""
        from mood_soc.config import MOOD_MAX

        app = self.app
        dlg = self._open(0)
        dlg._set_all(MOOD_MAX)
        dlg._mood_vars["温蒂"].set("3")          # 手改一行，验证它会被一起收走
        changes, moods = self._apply(dlg)
        self.assertEqual(moods, {"温蒂": Decimal("3")})   # 差集：只有手改的那一行
        self.assertEqual(app.traj.mood_at("温蒂", 0), Decimal("3"))

        dlg = self._open(0, moods=moods)
        dlg._set_all(Decimal("0"))
        changes, moods = self._apply(dlg)
        self.assertEqual(len(moods), len(app.schedule.operator_names()))
        self.assertTrue(all(v == 0 for v in moods.values()))
        self.assertEqual(app.traj.mood_at("菲亚梅塔", 0), Decimal("0"))
        self.assertEqual(app.traj.mood_at("锡人", 0), Decimal("0"))
        # 收尾
        app.initial_moods.clear()
        app.recompute()

    def test_统一设为一个值(self):
        dlg = self._open(0)
        dlg.uniform.set("12.5")
        dlg._set_uniform()
        self.assertEqual(dlg._mood_vars["森蚺"].get(), "12.5")
        self.assertEqual(dlg.err.cget("text"), "")
        dlg.uniform.set("30")                    # 越界 → 报错且不改
        dlg._set_uniform()
        self.assertIn("0 ~ 24", dlg.err.cget("text"))
        self.assertEqual(dlg._mood_vars["森蚺"].get(), "12.5")

    def test_按当前时刻回填(self):
        """把滑块所在时刻的**实际心情**写成新的周期起点。"""
        app = self.app
        app.set_time(Decimal("12"))
        app.update()
        dlg = self._open(0, moods_now=app.traj.moods_at(Decimal("12")))
        dlg._fill_from_now()
        changes, moods = self._apply(dlg)
        self.assertTrue(moods, "回填后应当有一批与导入值不同的心情")
        for name, v in moods.items():
            self.assertEqual(app.traj.mood_at(name, 0), v)
        app.initial_moods.clear()
        app.recompute()

    def test_恢复导入值(self):
        dlg = self._open(0, moods={"塞雷娅": Decimal("6")})
        dlg._restore_imported()
        changes, moods = self._apply(dlg)
        self.assertEqual(moods, {})              # 全部回到导入值 → 差集为空

    def test_心情越界被拒(self):
        panel = self._open(0)
        panel._mood_vars["温蒂"].set("99")
        self.assertIsNone(panel.value())          # 越界 → 收不出结果
        self.assertIn("0 ~ 24", panel.err.cget("text"))
        self.assertTrue(panel.winfo_exists(), "报错时面板不该被销毁（提示要留在屏幕上）")

    # ------------------------------------------------------------- 干员批量
    def test_粘贴名单按房间顺序填入(self):
        """粘贴的名单按"房间顺序 + 位次"填满：填到几号房取决于**该房间当前等级的容量**。

        （等级由导入时按人数推断，见 `mood_soc/maa.py`：示例排班的控制中枢是 Lv5 = 5 个位置。）
        """
        app = self.app
        dlg = self._open(0)
        names = ["泡泡", "慕斯", "克洛丝", "米格鲁", "芬", "玫兰莎"]
        dlg._apply_names(names, clear_first=True)
        self.assertIn(f"已填入 {len(names)} 人", dlg.err.cget("text"))
        changes, moods = self._apply(dlg)
        self.assertEqual(self._ops_of(changes), names)          # 顺序不变
        world = app.schedule.shifts[0].world
        cap0 = world.facilities[0].capacity
        self.assertEqual([o.name for o in world.facilities[0].operators], names[:cap0])
        if len(names) > cap0:                                   # 装不下的顺延到下一间房
            self.assertEqual(world.facilities[1].operators[0].name, names[cap0])

    def test_粘贴名单去重与截断(self):
        app = self.app
        dlg = self._open(0)
        total = len(dlg._all_slots())
        # 同一个人写两次 → 只填一次；名单比位置多 → 截断并提示
        dlg._apply_names(["泡泡", "泡泡"] + ["慕斯"] * (total + 3), clear_first=True)
        self.assertIn("重名", dlg.err.cget("text"))
        self.assertIn("没有位置", dlg.err.cget("text"))
        changes, moods = self._apply(dlg)
        self.assertEqual(self._ops_of(changes), ["泡泡", "慕斯"])

    def test_清空本班次(self):
        app = self.app
        dlg = self._open(0)
        dlg._clear_shift()
        changes, moods = self._apply(dlg)
        self.assertEqual(self._ops_of(changes), [])
        self.assertEqual(app.schedule.shifts[0].world.facilities[0].operators, [])

    # ------------------------------------------------------------- 练度（精英化）
    def test_练度列写回与回读(self):
        """练度列：只有**非 E2** 才写成对象 `{"name":…, "elite":…}`，其余保持字符串；
        再打开时能读回、`全部设为 E2` 能把对象写法收干净。"""
        app = self.app
        dlg = self._open(0)
        self.assertEqual(dlg._elite_vars["卡夫卡"].get(), "E2")      # 缺省口径＝满练
        dlg._elite_vars["卡夫卡"].set("E1")
        dlg._on_elite_change("卡夫卡")
        self.assertEqual(dlg._op_text("卡夫卡"), "卡夫卡 E1")        # 表格里立刻带角标
        changes, _moods = self._apply(dlg)
        self.assertEqual([n for f in changes[0] for n in f["operators"] if isinstance(n, dict)],
                         [{"name": "卡夫卡", "elite": 1}])
        self.assertEqual(app.schedule.shifts[0].world.get_operator("卡夫卡").elite, 1)

        # 再打开：读回 E1（对象写法不能把表格搞崩）
        again = self._open(0)
        self.assertEqual(again._elite["卡夫卡"], 1)
        self.assertEqual(again._elite_vars["卡夫卡"].get(), "E1")
        again._set_all_elite(2)
        changes, _moods = self._apply(again)
        self.assertEqual([n for f in changes[0] for n in f["operators"] if isinstance(n, dict)], [])
        self.assertEqual(app.schedule.shifts[0].world.get_operator("卡夫卡").elite, 2)

    def test_练度不足会少算技能(self):
        """同一个人 E2 / E1 两档：卡片的技能条数不同，心情速率也随之不同。"""
        from mood_soc.rules import mood_skill_summary

        app = self.app
        self.assertEqual(mood_skill_summary(
            app.schedule.shifts[0].world.get_operator("卡夫卡"))[1], [])
        dlg = self._open(0)
        dlg._elite_vars["卡夫卡"].set("E1")
        dlg._on_elite_change("卡夫卡")
        self._apply(dlg)
        op = app.schedule.shifts[0].world.get_operator("卡夫卡")
        self.assertEqual(op.elite, 1)
        unlocked, locked = mood_skill_summary(op)
        self.assertTrue(locked, "E1 的卡夫卡应当少一条心情技能（「手工艺品·β」要 E2）")
        self.assertIn("手工艺品·β", [n for n, _e, _lv in locked])
        app.initial_moods.clear()
        app.recompute()

    # ------------------------------------------------------------- 班次隔离
    def test_班次之间互不串改(self):
        """干员改动只作用于被改过的那一班；没碰的班次一个位置都不变。"""
        app = self.app
        labels = app.schedule.shift_labels()
        shift2_ops = [[o.name for o in f.operators] for f in app.schedule.shifts[1].world.facilities]
        dlg = self._open(0)
        dlg._apply_names(["泡泡"], clear_first=True)
        dlg.shift_var.set(labels[1])              # 切到第 2 班：拿到的应是原样布局
        dlg._on_shift_change()
        self.assertEqual([f["operators"] for f in dlg._fac_names], shift2_ops)
        dlg.shift_var.set(labels[0])              # 切回来：第 1 班未应用的改动还在
        dlg._on_shift_change()
        self.assertEqual(dlg._fac_names[0]["operators"], ["泡泡"])
        changes, moods = self._apply(dlg)
        self.assertEqual(list(changes), [0])      # 只有第 1 班被改
        self.assertEqual(self._ops_of(changes), ["泡泡"])
        self.assertEqual([[o.name for o in f.operators] for f in
                          app.schedule.shifts[1].world.facilities], shift2_ops)

    # ------------------------------------------------------------- 端到端
    def test_设置中心里改干员与心情立即生效(self):
        """`app.open_settings("batch")`：面板里一改，布局与起点心情**立刻**落地、状态栏给回执。

        合并前这里是"对话框打桩 → `app.batch_edit()` 收结果"；现在设置中心是唯一入口，
        面板通过 `on_change` 直接调 `app.apply_batch`，不再有"应用"这一步。
        """
        app = self.app
        dlg = app.open_settings("batch")
        app.update()
        try:
            panel = dlg.panel
            panel._apply_names(["泡泡", "慕斯"], clear_first=True)
            panel._mood_vars["泡泡"].set("8")
            self._pump(app)                       # 心情输入走 250ms 防抖
            self.assertEqual(app.initial_moods.get("泡泡"), Decimal("8"))
            self.assertEqual(app.traj.mood_at("泡泡", 0), Decimal("8"))
            self.assertEqual(app.schedule.shifts[0].world.facilities[0].operators[0].name, "泡泡")
            self.assertIn("设置已生效", app.status.cget("text"))
            # 越界的心情不会落地（面板显示错误、排班保持上一次的合法状态）
            panel._mood_vars["泡泡"].set("99")
            self._pump(app)
            self.assertEqual(app.initial_moods.get("泡泡"), Decimal("8"))
            self.assertIn("0 ~ 24", panel.err.cget("text"))
        finally:
            dlg.destroy()
        app.initial_moods.clear()
        app.recompute()

    def test_设置中心四个分区都能开(self):
        """左导航的 4 个分区都能打开，且各自挂到正确的面板上。"""
        from ui.batch import BatchPanel
        from ui.dialogs import EntryEventPanel, IdleToDormPanel
        from ui.settings import PAGES

        app = self.app
        dlg = app.open_settings()
        app.update()
        try:
            self.assertEqual([k for k, _t, _d in PAGES],
                             ["timeline", "batch", "entry", "idle"])
            for key in ("timeline", "batch", "entry", "idle"):
                dlg.open_page(key)
                app.update()
                self.assertTrue(dlg.panel.winfo_exists(), f"{key} 分区应当建出内容")
            self.assertIsInstance(dlg.panel, IdleToDormPanel)
            dlg.open_page("batch")
            app.update()
            self.assertIsInstance(dlg.panel, BatchPanel)
            dlg.open_page("entry")
            app.update()
            self.assertIsInstance(dlg.panel, EntryEventPanel)
            self.assertEqual(len(dlg.panel.shift_use), len(app.schedule.shifts))
        finally:
            dlg.destroy()

    # ------------------------------------------------------------- 小工具
    def _pump(self, app, seconds: float = 0.4):
        """空转事件循环若干秒（等防抖定时器到点）。"""
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < seconds:
            app.update()
            time.sleep(0.01)

    def _apply(self, panel):
        """把面板收出来的结果真正落到排班上（等价于 `app.apply_batch`）。"""
        res = panel.value()
        if res is None:                          # 报错时面板还在，能读到提示
            self.fail(f"收结果失败：{panel.err.cget('text')}")
        changes, moods = res
        app = self.app
        app.apply_batch(changes, moods)
        return changes, moods


if __name__ == "__main__":
    unittest.main(verbosity=2)

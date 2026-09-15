"""tests/test_ui_batch_blackbox.py —— 「批量设置」对话框的黑盒测试（真要建窗口）。

它盯着这条需求的接线：**当前布局的所有干员 + 心情，一张表一次改完**。
覆盖表格口径（行数＝当前班次的位置数、心情预填＝周期起点）、五个心情批量动作、
粘贴名单（按房间顺序 + 去重）、越界拒绝、班次之间互不串改，以及 `app.batch_edit()`
的端到端接线（对话框打桩，其余走真实代码路径）。

无图形环境（Tk 建不出来，如无桌面的 CI）时整体跳过。

运行：.venv/Scripts/python.exe -m unittest tests.test_ui_batch_blackbox -v
"""
from __future__ import annotations

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
        self.dlg = None

    def tearDown(self):
        if self.dlg is not None and self.dlg.winfo_exists():
            self.dlg.destroy()

    # ------------------------------------------------------------- 工具
    def _open(self, shift_index: int = 0, moods=None, moods_now=None):
        from ui.batch import BatchDialog
        from ui.schedule import default_initial_moods

        app = self.app
        dlg = BatchDialog(app, app.schedule, shift_index=shift_index,
                          initial_moods=(moods or {}),
                          imported_moods=default_initial_moods(app.schedule),
                          moods_now=(moods_now if moods_now is not None
                                     else app.traj.moods_at(Decimal("0"))),
                          current_t=app.current_t)
        app.update()
        self.dlg = dlg
        return dlg

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
        dlg = self._open(0)
        dlg._mood_vars["温蒂"].set("99")
        dlg._ok()
        self.assertIsNone(dlg.result)
        self.assertIn("0 ~ 24", dlg.err.cget("text"))
        self.assertTrue(dlg.winfo_exists(), "报错时对话框不该关掉")

    # ------------------------------------------------------------- 干员批量
    def test_粘贴名单按房间顺序填入(self):
        app = self.app
        dlg = self._open(0)
        names = ["泡泡", "慕斯", "克洛丝", "米格鲁"]
        dlg._apply_names(names, clear_first=True)
        self.assertIn("已填入 4 人", dlg.err.cget("text"))
        changes, moods = self._apply(dlg)
        got = self._ops_of(changes)
        self.assertEqual(got, names)
        # 看板/引擎侧确实变了：第 1 班的干员名单跟着换
        self.assertEqual([o.name for o in app.schedule.shifts[0].world.facilities[0].operators],
                         ["泡泡"])
        self.assertEqual(app.schedule.shifts[0].world.facilities[1].operators[0].name, "慕斯")

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
    def test_batch_edit端到端(self):
        """`app.batch_edit()`：对话框打桩 → 布局与起点心情一起落地、状态栏给回执。"""
        from ui import app as app_mod
        app = self.app
        dlg = self._open(0)
        dlg._apply_names(["泡泡", "慕斯"], clear_first=True)
        dlg._mood_vars["泡泡"].set("8")
        dlg._ok()
        result = dlg.result
        if result is None:
            self.fail(f"应用失败：{dlg.err.cget('text')}")
        self.dlg = None
        dlg.destroy()

        orig = app_mod.ask_batch
        try:
            app_mod.ask_batch = lambda *a, **k: result
            app.batch_edit()
        finally:
            app_mod.ask_batch = orig
        self.assertEqual(app.initial_moods.get("泡泡"), Decimal("8"))
        self.assertEqual(app.traj.mood_at("泡泡", 0), Decimal("8"))
        self.assertEqual(app.schedule.shifts[0].world.facilities[0].operators[0].name, "泡泡")
        self.assertIn("批量设置已应用", app.status.cget("text"))
        # 取消（返回 None）→ 什么都不变
        before = list(app.schedule.shifts[0].operators)
        try:
            app_mod.ask_batch = lambda *a, **k: None
            app.batch_edit()
        finally:
            app_mod.ask_batch = orig
        self.assertEqual(list(app.schedule.shifts[0].operators), before)
        app.initial_moods.clear()
        app.recompute()

    # ------------------------------------------------------------- 小工具
    def _apply(self, dlg):
        """按「应用」并把结果真正落到排班上（等价于 `app.batch_edit` 的后半段）。"""
        dlg._ok()
        if dlg.result is None:                   # 报错时对话框还在，能读到提示
            self.fail(f"应用失败：{dlg.err.cget('text')}")
        changes, moods = dlg.result
        app = self.app
        for i in sorted(changes):
            app.schedule = app.schedule.replaced_shift(i, changes[i])
        app.initial_moods = dict(moods)
        app._layout_sig = None
        app.recompute()
        return changes, moods


if __name__ == "__main__":
    unittest.main(verbosity=2)

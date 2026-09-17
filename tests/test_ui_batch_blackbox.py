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
from data.paths import MAA_SAMPLE as SAMPLE  # noqa: E402


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
    def _open(self, shift_index: int = 0, moods=None, moods_now=None, mood_events=None):
        """建一个「干员与心情」面板并挂到测试用的窗口上。

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
                           current_t=app.current_t,
                           cycles=app.cycles,
                           mood_events=(app.mood_events if mood_events is None
                                        else mood_events),
                           moods_at=app.moods_at_abs)
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
        # 房间那一段的心情列＝本班在岗的人；「不在基建」那一段是**本班未排班**的人
        stationed = set(shift.operators)
        for name in stationed:
            self.assertIn(name, dlg._mood_vars)
        self.assertEqual(set(dlg._mood_vars),
                         stationed | set(dlg._detached_all()))
        # 「不在基建」段 = 全排班人员 − 本班在岗（本项目示例排班每班人员不同）
        self.assertEqual(set(dlg._detached_all()),
                         set(app.schedule.operator_names()) - stationed)
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

    # ------------------------------------------------------------- 不在基建
    def test_不在基建那一段自动列出本班未排班的人(self):
        """「不在基建」段 = 全排班人员 − **本班次在岗的人**；每行都给得出心情、可改。"""
        from ui.batch import DETACHED_FI

        app = self.app
        dlg = self._open(0)
        shift = app.schedule.shifts[0]
        auto = dlg._detached_all()
        self.assertEqual(set(auto), set(app.schedule.operator_names()) - set(shift.operators))
        # 段首那一行是**分组卡片**（通栏写段名 + 一句口径），后面每人一行
        bench_rows = [r for r in dlg._rows if r["kind"] == dlg.KIND_BENCH]
        self.assertEqual([r["name"] for r in bench_rows], list(auto))
        card = [r for r in dlg._rows if r["kind"] == dlg.KIND_ROOM
                and r["key"][1] == DETACHED_FI]
        self.assertEqual(len(card), 1)
        self.assertIn("不在基建", card[0]["room"].cget("text"))
        # 这些人在表格里能改心情（写的是**周期起点**）
        name = auto[0]
        dlg._mood_vars[name].set("9")
        app.update()
        dlg._collect_moods()
        self.assertEqual(dlg._moods[name], Decimal("9"))
        self.assertTrue(all(r["key"][1] == DETACHED_FI for r in bench_rows))

    def test_走人不占位置(self):
        """「不在基建」的人**不占进驻位**：段里的行没有位次号、也不进 `_cells`。"""
        from ui.batch import DETACHED_FI

        dlg = self._open(0)
        self.assertTrue(all(fi != DETACHED_FI for fi, _si in dlg._cells))

    def test_其他班在哪一列(self):
        """「本班未排班 ≠ 整份排班都不在基建」：`—` 列要写出她在别的班在哪。

        示例排班的每班人员不同：第 1 班没排到的人，多半在第 2/3 班有活。
        """
        app = self.app
        dlg = self._open(0)
        from ui.batch import DETACHED_FI

        shift1 = set(app.schedule.shifts[0].operators)
        elsewhere = [n for n in dlg._detached_all()
                     if any(s.world.facility_of(n) is not None for s in app.schedule.shifts[1:])]
        self.assertTrue(elsewhere, "示例排班应当有'第 1 班没排到、别的班有活'的人")
        name = elsewhere[0]
        text = dlg._detached_elsewhere(name)
        self.assertTrue(text.startswith("其他班："))
        row = next(r for r in dlg._rows if r["kind"] == dlg.KIND_BENCH and r["name"] == name)
        self.assertEqual(row["dash"].cget("text"), text)
        # 两个班都没排到的人 → 没有"其他班"标记（真正的不在基建）
        for other in dlg._detached_all():
            if all(s.world.facility_of(other) is None for s in app.schedule.shifts):
                self.assertEqual(dlg._detached_elsewhere(other), "")
        self.assertNotIn("", [n for n in shift1])          # 只是说明 shift1 有用（避免空断言）

    def test_添加干员把它移出位置并写进名单(self):
        """「＋ 添加干员…」的效果：从所有班次的位置上摘下来 + 进「不在基建」名单。

        这里直接调 `_detach_operator`（等价于点完搜索窗选人的结果），不走模态框。
        """
        app = self.app
        dlg = self._open(0)
        who = app.schedule.shifts[0].operators[0]          # 本班在岗的一个人
        dlg._detach_operator(who)
        self.assertIn(who, dlg._detached)
        self.assertTrue(dlg._detached_edited)
        # 本班的工作副本里已经没有他了
        self.assertNotIn(who, [n for f in dlg._fac_names for n in f.get("operators", [])])
        self.assertIn(who, dlg._detached_all())
        # 收结果 → 落地 → 会话的名单也更新了
        changes, _m, _e, detached = dlg.value()
        app.apply_batch(changes, _m, _e, detached)
        app.update()
        self.assertIn(who, app.detached)

    def test_移除按钮把它移出名单(self):
        """行尾「×」＝移出**显式**名单（位置不自动恢复，提示会说明这一点）。"""
        app = self.app
        dlg = self._open(0)
        who = app.schedule.shifts[0].operators[0]
        dlg._detach_operator(who)
        self.assertIn(who, dlg._detached)
        dlg._undetach_operator(who)
        self.assertNotIn(who, dlg._detached)
        self.assertTrue(dlg._detached_edited)

    def test_没动过名单就不覆盖(self):
        """`detached` 只有用户**动过**才交出去（`None` = 别抹掉导入带来的名单）。"""
        dlg = self._open(0)
        self.assertIsNone(dlg.value()[3])
        dlg._detach_operator(self.app.schedule.shifts[0].operators[0])
        self.assertIsNotNone(dlg.value()[3])

    def test_切换班次时自动名单跟着变(self):
        """干员列跟着「时刻/班次」走 → 「不在基建」那一段也要跟着换。"""
        app = self.app
        dlg = self._open(0)
        first = set(dlg._detached_all())
        dlg._set_view(1, app.schedule.starts[1])          # 挪到第 2 班
        app.update()
        second = set(dlg._detached_all())
        self.assertNotEqual(first, second)
        self.assertEqual(second,
                         set(app.schedule.operator_names())
                         - set(app.schedule.shifts[1].operators))

    def test_不加名单时数值一字不变(self):
        """只"看得见"不算改动：打开面板不动任何东西 ⇒ 起点心情差集为空。"""
        dlg = self._open(0)
        changes, moods, events, detached = dlg.value()
        self.assertEqual(moods, {})
        self.assertEqual(events, [])
        self.assertIsNone(detached)

    # ------------------------------------------------------------- 表格外观
    def test_表头与分组卡片(self):
        """表格要**一眼能看懂**：固定表头（列名）+ 每个房间一张分组卡片。"""
        from ui.batch import TABLE_COLUMNS

        dlg = self._open(0)
        self.assertEqual([c[0] for c in TABLE_COLUMNS],
                         ["房间", "位次", "干员", "练度", "心情", "说明"])
        cards = [r for r in dlg._rows if r["kind"] == dlg.KIND_ROOM]
        # 每间房一张卡片，标题写 `制1 制造站#1 · Lv3 · 3/3 人`
        ftype_count = {}
        for fi in range(len(dlg._fac_names)):
            ftype_count[dlg._fac_names[fi]["type"]] = ftype_count.get(
                dlg._fac_names[fi]["type"], 0) + 1
        self.assertEqual(len(cards), len(dlg._fac_names) + 1)     # +1 = 「不在基建」那张
        text = cards[1]["room"].cget("text")
        self.assertIn("制造站", text)
        self.assertIn("Lv", text)
        self.assertIn("人", text)
        self.assertIn("·", text)

    def test_等宽列与对齐(self):
        """位置行的各列都**落在同一列**上（共享 grid 等宽）。

        表格的全部单元格都在 `inner` 的**同一个 grid** 上 —— 列宽只有一个来源，
        所以"表头 / 空位行 / 有人的行 / 不在基建行"的每一列 x 坐标都完全一致
        （曾经每行一个独立 Frame，列宽只在行内成立，实测跨行差 9px）。
        """
        app = self.app
        dlg = self._open(0)
        slots = [r for r in dlg._rows if r["kind"] == dlg.KIND_SLOT and r["name"]]
        self.assertTrue(slots)
        self.assertEqual(slots[0]["grid_row"], 1)       # 第 0 行是房间分组卡片
        for r in slots[:5]:
            for key in ("pos", "op", "elite", "entry"):
                info = r[key].grid_info()
                self.assertEqual(int(info["row"]), r["grid_row"], key)
                self.assertEqual(str(info["in"]), str(dlg.inner), key)
            self.assertEqual(int(r["pos"].grid_info()["column"]), 1)
            self.assertEqual(int(r["op"].grid_info()["column"]), 2)
            self.assertEqual(int(r["elite"].grid_info()["column"]), 3)
            self.assertEqual(int(r["entry"].grid_info()["column"]), 4)
        # 同一列的 x 坐标逐行一致（这是"对齐"的硬指标）
        app.update_idletasks()
        import collections
        xs = collections.defaultdict(set)
        for r in dlg._rows:
            for key in ("pos", "op", "elite", "entry", "dash"):
                w = r.get(key)
                if w is None or not w.winfo_manager() or w.winfo_width() <= 1:
                    continue
                xs[int(w.grid_info()["column"])].add(w.winfo_x())
        self.assertTrue(xs)
        for col, got in xs.items():
            self.assertEqual(len(got), 1, f"列 {col} 的 x 坐标有 {sorted(got)} 种（未对齐）")
        # 像素固定列：房间 96 / 位次 40 / 练度 56 / 心情 72（不随内容变宽）
        for col, want in ((0, 96), (1, 40), (3, 56), (4, 72)):
            self.assertEqual(dlg.inner.grid_columnconfigure(col)["minsize"], want,
                             f"列 {col} 该是固定 {want}px")

    def test_搜索过滤(self):
        """搜索框：输入即过滤（匹配名字/房间），清空恢复全部，且**不动心情状态**。"""
        app = self.app
        dlg = self._open(0)
        all_names = dlg._visible_names()
        self.assertEqual(len(all_names), len(dlg._mood_vars))

        who = all_names[0]
        dlg.filter.set(who)
        app.update()
        self.assertEqual(dlg._visible_names(), [who])
        self.assertIn("匹配 1 人", dlg.filter_note.cget("text"))
        # 过滤只是"看不见"，不是"删掉"：状态里还有她、切回全部也在
        self.assertIn(who, dlg._moods)
        dlg.filter.set("")
        app.update()
        self.assertEqual(dlg._visible_names(), all_names)
        # 搜房间名：宿舍里的人都该出现
        dlg.filter.set("宿舍")
        app.update()
        vis = dlg._visible_names()
        self.assertTrue(vis)
        dorm_ops = {o.name for s in app.schedule.shifts for f in s.world.all_dormitories()
                    for o in f.operators}
        self.assertTrue(set(vis) & dorm_ops, "搜「宿舍」至少该出现一个住宿舍的人")
        # 搜不到时给出提示，而不是空白
        dlg.filter.set("不可能存在的名字")
        app.update()
        self.assertEqual(dlg._visible_names(), [])
        self.assertIn("匹配 0 人", dlg.filter_note.cget("text"))
        dlg.filter.set("")
        app.update()

    def test_一键动作只作用于看得见的行(self):
        """搜索过滤之后点「全部满心情」，**不该**改到看不见的人（否则很反直觉）。"""
        app = self.app
        dlg = self._open(0)
        who = dlg._visible_names()[0]
        dlg.filter.set(who)
        app.update()
        dlg._set_all(Decimal("24"))              # 全部满心情
        app.update()
        others = [n for n in dlg._moods if n != who]
        self.assertTrue(others)
        for n in others[:5]:                     # 看不见的人没被动过
            self.assertNotEqual(dlg._moods[n], Decimal("0"))
        dlg.filter.set("")
        app.update()

    def test_滚轮速度与键盘(self):
        """滚轮：默认 3 行 / Shift 整页 / Ctrl 10 行；另有 PgUp·PgDn·Home·End。"""
        from ui.scroll import FAST_UNITS, UNITS_PER_NOTCH

        dlg = self._open(0)
        self.assertEqual(dlg.vs.units_per_notch, UNITS_PER_NOTCH)

        class Ev:
            delta = -120
            state = 0

        e = Ev()
        self.assertEqual(dlg.vs._units(e), UNITS_PER_NOTCH)
        e.state = 0x0001                          # Shift
        self.assertGreaterEqual(abs(dlg.vs._units(e)), 2 * UNITS_PER_NOTCH)
        e.state = 0x0004                          # Ctrl
        self.assertEqual(dlg.vs._units(e), FAST_UNITS)
        e.delta = 120                             # 向上滚 → 负值
        self.assertEqual(dlg.vs._units(e), -FAST_UNITS)
        # 键盘绑定在这个表的 bindtag 上（滚轮 tag 同一套）
        self.assertIn(dlg.vs.tag, dlg.canvas.bindtags())
        for seq in ("<Prior>", "<Next>", "<Home>", "<End>"):
            self.assertTrue(dlg.canvas.bind_class(dlg.vs.tag, seq), seq)

    # ------------------------------------------------------------- 心情批量
    def test_全部满心情与全部零(self):
        """两个一键：全部 24 / 全部 0 → 覆盖**整个排班**的干员（不只当前班次）。"""
        from mood_soc.config import MOOD_MAX

        app = self.app
        dlg = self._open(0)
        dlg._set_all(MOOD_MAX)
        dlg._mood_vars["温蒂"].set("3")          # 手改一行，验证它会被一起收走
        changes, moods, _events = self._apply(dlg)
        self.assertEqual(moods, {"温蒂": Decimal("3")})   # 差集：只有手改的那一行
        self.assertEqual(app.traj.mood_at("温蒂", 0), Decimal("3"))

        dlg = self._open(0, moods=moods)
        dlg._set_all(Decimal("0"))
        changes, moods, _events = self._apply(dlg)
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

    def test_按这一刻回填(self):
        """「按这一刻回填」＝ 把**当前指定时刻**的实际心情写成锚点（钉住此刻的实际值）。

        视图在第 1 周期 12:00：回填后应当出现一批 `t=12` 的锚点，且 12:00 的心情
        **一个都不变**（回填的是"此刻实际是多少"，不是"改一个数"）。
        """
        app = self.app
        app.set_time(Decimal("12"))
        app.update()
        dlg = self._open(0)                       # current_t=12 → 视图＝第 1 周期 12:00
        self.assertEqual(float(dlg._view_t), 12.0)
        before = {n: app.traj.mood_at(n, Decimal("12")) for n in dlg._table_names()}
        dlg._fill_from_now()
        changes, moods, events = self._apply(dlg)
        self.assertTrue(events, "回填后应当有一批锚点")
        self.assertTrue(all(e.t == Decimal("12") and e.cycle == 1 for e in events))
        for e in events:
            self.assertEqual(float(app.traj.mood_at(e.name, Decimal("12"))), float(before[e.name]),
                             f"{e.name} 在 12:00 的心情不该被回填改掉")
        self.assertEqual(moods, {}, "12:00 的锚点不该改动周期起点心情")
        app.mood_events.clear()
        app.recompute()

    def test_周期起点视图的回填与老口径一致(self):
        """视图停在「第 1 周期 0:00」（老口径）时，回填写的仍是**周期起点心情**。"""
        app = self.app
        app.set_time(Decimal("12"))
        app.update()
        dlg = self._open(0)
        dlg._set_view(1, Decimal("0"))             # 手动把时刻挪回周期起点
        before = {n: app.traj.mood_at(n, Decimal("0")) for n in dlg._table_names()}
        dlg._fill_from_now()
        changes, moods, events = self._apply(dlg)
        self.assertEqual(events, [], "起点视图不产生锚点")
        for name, v in moods.items():
            self.assertEqual(v, before[name])
        app.initial_moods.clear()
        app.recompute()

    def test_恢复导入值(self):
        dlg = self._open(0, moods={"塞雷娅": Decimal("6")})
        dlg._restore_imported()
        changes, moods, _events = self._apply(dlg)
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
        changes, moods, _events = self._apply(dlg)
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
        changes, moods, _events = self._apply(dlg)
        self.assertEqual(self._ops_of(changes), ["泡泡", "慕斯"])

    def test_清空本班次(self):
        app = self.app
        dlg = self._open(0)
        dlg._clear_shift()
        changes, moods, _events = self._apply(dlg)
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
        changes, _moods, _events = self._apply(dlg)
        self.assertEqual([n for f in changes[0] for n in f["operators"] if isinstance(n, dict)],
                         [{"name": "卡夫卡", "elite": 1}])
        self.assertEqual(app.schedule.shifts[0].world.get_operator("卡夫卡").elite, 1)

        # 再打开：读回 E1（对象写法不能把表格搞崩）
        again = self._open(0)
        self.assertEqual(again._elite["卡夫卡"], 1)
        self.assertEqual(again._elite_vars["卡夫卡"].get(), "E1")
        again._set_all_elite(2)
        changes, _moods, _events = self._apply(again)
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
        changes, moods, _events = self._apply(dlg)
        self.assertEqual(list(changes), [0])      # 只有第 1 班被改
        self.assertEqual(self._ops_of(changes), ["泡泡"])
        self.assertEqual([[o.name for o in f.operators] for f in
                          app.schedule.shifts[1].world.facilities], shift2_ops)

    # ------------------------------------------------------------- 按时刻指定心情
    def test_换时刻心情列按轨迹重算(self):
        """指定时刻一变，心情列就是那一刻的**实际心情**（正常演化出来的，不用重输）。"""
        app = self.app
        dlg = self._open(0)
        dlg._set_view(1, Decimal("0"))
        names = list(dlg._mood_vars)
        at0 = {n: dlg._mood_vars[n].get() for n in names}
        dlg._set_view(1, Decimal("6"))
        at6 = {n: dlg._mood_vars[n].get() for n in names}
        self.assertTrue(any(at0[n] != at6[n] for n in names), "6 小时后应当有人心情变了")
        for n, text in at6.items():
            self.assertEqual(Decimal(text), app.traj.mood_at(n, Decimal("6")),
                             f"{n} 在 6:00 的心情应当＝轨迹值")
        # 干员列也跟着时刻走：6:00 落在第 1 班（12h）
        self.assertEqual(dlg._shift_index, app.schedule.index_at(Decimal("6")))
        # 回到起点视图 → 又是"周期起点心情"（老口径）
        dlg._set_view(1, Decimal("0"))
        self.assertEqual(dlg._mood_vars[names[0]].get(), at0[names[0]])

    def test_改格子写锚点且只对指定周期(self):
        """非起点视图里改格子 → 写 `(周期, 时刻, 干员)` 锚点，**不动**周期起点心情。"""
        from ui.schedule import MoodSetEvent

        app = self.app
        app.mood_events = [MoodSetEvent("锡人", Decimal("3"), Decimal("10"), 1)]
        dlg = self._open(0)
        dlg._set_view(1, Decimal("6"))
        dlg._mood_vars["森蚺"].set("9")
        changes, moods, events = self._apply(dlg)
        self.assertEqual(moods, {}, "非起点视图不该改周期起点心情")
        self.assertEqual([(e.name, float(e.t), float(e.mood), e.cycle) for e in events],
                         [("锡人", 3.0, 10.0, 1), ("森蚺", 6.0, 9.0, 1)])
        self.assertEqual(app.traj.mood_at("森蚺", Decimal("6")), Decimal("9"))
        # 第 2 周期同刻**不**被钉住（锚点只对指定周期生效）
        app.cycles_var.set("2")
        app.cycles = 2
        app.recompute()
        self.assertEqual([e.cycle for e in events], [1, 1])
        # 在"第 2 周期"视图里改 → 锚点带 cycle=2（与第 1 周期那条共存）
        dlg2 = self._open(0)
        dlg2._set_view(2, Decimal("6"))
        dlg2._mood_vars["森蚺"].set("4")
        _c, _m, ev2 = self._apply(dlg2)
        self.assertIn((2, 6.0, 4.0), [(e.cycle, float(e.t), float(e.mood)) for e in ev2])
        app.mood_events.clear()
        app.cycles_var.set("1")
        app.cycles = 1
        app.recompute()

    def test_没改过的格子不会被当成手动设定(self):
        """只被刷新过的格子不能变成锚点/起点心情（否则"改个等级"都会顺手改掉起点心情）。"""
        app = self.app
        dlg = self._open(0)
        dlg._set_view(1, Decimal("6"))
        changes, moods, events = self._apply(dlg)
        self.assertEqual((moods, events), ({}, []))
        dlg._on_level_change(1, dlg._level_vars[1])       # 随便动一下别的设置
        _c, moods2, events2 = self._apply(dlg)
        self.assertEqual((moods2, events2), ({}, []))

    def test_干员列跟着时刻切班次(self):
        """时刻挪到第 2 班 → 表格显示第 2 班的名单（两处不会各指一个时间）。"""
        app = self.app
        dlg = self._open(0)
        shift2 = [[o.name for o in f.operators]
                  for f in app.schedule.shifts[1].world.facilities]
        dlg._set_view(1, app.schedule.starts[1])
        self.assertEqual(dlg._shift_index, 1)
        self.assertEqual(dlg.shift_var.get(), app.schedule.shift_labels()[1])
        self.assertEqual([f["operators"] for f in dlg._fac_names], shift2)

    def test_时刻输入与步进(self):
        """时刻输入接受 `HH:MM`（带初始时间点），`◀/▶` 挪 15 分钟，越界按周期取模。"""
        app = self.app
        dlg = self._open(0)
        dlg.view_time_var.set("13:30")
        dlg._on_view_change()
        self.assertEqual(dlg._view_t, Decimal("13.5"))
        dlg._step_view(Decimal("0.25"))
        self.assertEqual(dlg._view_t, Decimal("13.75"))
        dlg._step_view(Decimal("-0.5"))
        self.assertEqual(dlg._view_t, Decimal("13.25"))
        dlg.view_time_var.set("不是时间")
        dlg._on_view_change()
        self.assertIn("HH:MM", dlg.err.cget("text"))
        self.assertEqual(dlg._view_t, Decimal("13.25"), "解析失败不该改时刻")
        # 初始时间点 01:00 ⇒ 输入 01:00 就是周期起点（面板拿的是新排班）
        app.apply_start_clock(Decimal("1"))
        dlg2 = self._open(0)
        self.assertEqual(dlg2.view_time_var.get(), "01:00", "缺省视图按初始时间点显示")
        dlg2.view_time_var.set("13:00")
        dlg2._on_view_change()
        self.assertEqual(dlg2._view_t, Decimal("12"))
        app.apply_start_clock(Decimal("0"))

    def test_跟随滑块(self):
        """勾了「跟随滑块」：主界面推到哪，面板就跟到哪（连周期一起算）。"""
        app = self.app
        app.cycles_var.set("2")
        app.cycles = 2
        app.recompute()
        dlg = self._open(0)                       # 周期数 2 → 面板里有第 2 周期
        dlg.follow.set(True)
        dlg._on_follow()
        dlg.on_view_change(Decimal("19"), app.traj.moods_at(Decimal("19")))
        self.assertEqual(dlg._view_cycle, 1)
        self.assertEqual(dlg._view_t, Decimal("19"))
        dlg.on_view_change(Decimal("30"), app.traj.moods_at(Decimal("30")))
        self.assertEqual(dlg._view_cycle, 2)
        self.assertEqual(dlg._view_t, Decimal("6"))       # 30 - 24
        # 取消跟随 → 主界面再动也不拽走它
        dlg.follow.set(False)
        dlg.on_view_change(Decimal("40"), app.traj.moods_at(Decimal("40")))
        self.assertEqual((dlg._view_cycle, dlg._view_t), (2, Decimal("6")))
        app.cycles_var.set("1")
        app.cycles = 1
        app.recompute()

    def test_锚点一览与清空本时刻(self):
        app = self.app
        dlg = self._open(0)
        dlg._set_view(1, Decimal("6"))
        dlg._mood_vars["森蚺"].set("9")
        dlg._collect_moods()
        self.assertIn("森蚺=9", dlg.anchor_note.cget("text"))
        self.assertIn("第1周期", dlg.anchor_note.cget("text"))
        dlg._clear_view_anchors()
        self.assertNotIn("森蚺=9", dlg.anchor_note.cget("text"))
        _c, _m, events = self._apply(dlg)
        self.assertEqual(events, [])

    def test_恢复导入值清掉全部锚点(self):
        app = self.app
        dlg = self._open(0)
        dlg._set_view(1, Decimal("6"))
        dlg._mood_vars["森蚺"].set("9")
        dlg._collect_moods()
        dlg._restore_imported()
        _c, moods, events = self._apply(dlg)
        self.assertEqual((moods, events), ({}, []))

    def test_超出周期数的锚点会被标出来(self):
        from ui.schedule import MoodSetEvent

        app = self.app
        dlg = self._open(0)                       # 周期数 = 1
        dlg._events = [MoodSetEvent("森蚺", Decimal("6"), Decimal("9"), 3)]
        dlg._sync_anchor_note()
        self.assertIn("之外", dlg.anchor_note.cget("text"))
        self.assertIn("周期数调大", dlg.anchor_note.cget("text"))

    # ------------------------------------------------------------- 卡顿回归
    def test_程序化刷新不会被当成改动(self):
        """程序化刷新不能触发"改动"。

        ⚠️ 老 bug（"设置干员与心情卡顿严重"的根源）：面板刷新会把自己那一列写回输入框，
        而 `StringVar.trace_add("write")` 对程序自己的 `var.set()` 一样触发 →
        被当成"用户改动" → 通知 → 宿主重算 → 推送回来 → 又刷新 …… 自激回路。
        实测：空转 2 秒重算 **6 次 / 2336ms**（单次 231ms），界面一直在烧 CPU。
        """
        app = self.app
        dlg = self._open(0)
        sent = []
        dlg._on_change = lambda *a: sent.append(a)      # 打桩：只看"发了几次通知"
        # ① 非起点视图（最容易漏判的那种）：换时刻 / 刷新 / 重建行 / 刷锚点一览都不该发通知
        dlg._set_view(1, Decimal("6"))
        dlg._refresh_mood_cells()
        dlg._rebuild_rows()
        dlg._sync_anchor_note()
        self._pump(app, 1.0)
        self.assertEqual(sent, [], "程序化刷新被当成了用户改动")
        # ② 真的改一格 → 恰好一次
        who = "菲亚梅塔"
        dlg._mood_vars[who].set("9")
        self._pump(app, 1.0)
        self.assertEqual(len(sent), 1, "改一格只该通知一次")
        # ③ 再刷新一遍（值已经一致）→ 不该再有通知
        dlg._refresh_mood_cells()
        self._pump(app, 1.0)
        self.assertEqual(len(sent), 1)
        # ④ 结果没变时重复通知 → 去重，也不发
        dlg._notify()
        dlg._notify()
        self.assertEqual(len(sent), 1, "同一个结果不该重复通知（每次都＝一整轮重算）")
        app.initial_moods.clear()
        app.mood_events.clear()
        app.recompute()

    def test_连续输入只落地一次(self):
        """打字走 500ms 防抖：连续输入只在停手后落地一次（每次落地＝一整轮重算）。"""
        app = self.app
        dlg = self._open(0)
        sent = []
        dlg._on_change = lambda *a: sent.append(a)
        who = "菲亚梅塔"
        for v in ("1", "12", "12.", "12.5", "12.6", "12.7"):
            dlg._mood_vars[who].set(v)
            self._pump(app, 0.05)
        self.assertEqual(sent, [], "打字途中不该落地")
        self._pump(app, 1.0)
        self.assertEqual(len(sent), 1, "停手后只落地一次")
        self.assertEqual(sent[0][1], {who: Decimal("12.7")})
        app.initial_moods.clear()
        app.mood_events.clear()
        app.recompute()

    def test_时刻没变时零副作用(self):
        """时刻没变就不该动任何东西（`<FocusOut>` 会带着同样的时刻进来）。

        老 bug：无条件走 `_set_view`（默认会取消「跟随滑块」）⇒ "什么都没改，勾选却自己掉了"。
        """
        app = self.app
        dlg = self._open(0)
        sent = []
        dlg._on_change = lambda *a: sent.append(a)
        dlg.follow.set(True)
        dlg._on_follow()
        dlg._set_view(1, Decimal("6"))
        self.assertFalse(dlg.follow.get(), "手动指定时刻＝取消跟随")
        dlg.view_time_var.set("06:00")
        dlg._on_view_change()
        self.assertEqual((dlg._view_cycle, dlg._view_t), (1, Decimal("6")))
        self._pump(app, 1.0)
        self.assertEqual(sent, [], "值没变不该有任何通知")
        # 周期下拉与时刻一起比对：改了周期才算改
        dlg.view_cycle_var.set("1")
        dlg._on_view_change()
        self.assertEqual(dlg._view_t, Decimal("6"))
        app.initial_moods.clear()
        app.mood_events.clear()
        app.recompute()

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
            self._pump(app)                       # 心情输入走 500ms 防抖
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
    def _pump(self, app, seconds: float = 0.9):
        """空转事件循环若干秒（等防抖定时器到点：默认要盖过 500ms 的防抖）。"""
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < seconds:
            app.update()
            time.sleep(0.01)

    def _apply(self, panel):
        """把面板收出来的结果真正落到排班上（等价于 `app.apply_batch`）。"""
        res = panel.value()
        if res is None:                          # 报错时面板还在，能读到提示
            self.fail(f"收结果失败：{panel.err.cget('text')}")
        changes, moods, events, detached = res
        app = self.app
        app.apply_batch(changes, moods, events, detached)
        return changes, moods, events


if __name__ == "__main__":
    unittest.main(verbosity=2)

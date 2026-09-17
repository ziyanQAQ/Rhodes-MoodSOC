"""tests/test_import_blackbox.py —— 「导入排班」的**格式自动识别 + 一键导入**黑盒测试。

盯住两件事：

1. **认得出**：本工具场景 / MAA 排班 / v3 求解输出 / v4 蓝图+干员池，四类都能自动识别
   （`mood_soc/importer.py`），认不出来时报的错要说清"看到了什么"；
2. **导得对**：房间类型/等级/人员/练度/每班时长/换心情开关都被搬到本工具的模型里，
   拿不到的东西（没 layout、没 maa 段）走**兜底推断**并且**逐条写进导入报告**。

样例都在 `data/resources/`（按 `输出JSON结构说明.md` 与
`plan_compute_example_v4_annotated.md` 的字段造的）：
`import_v3_out_3shifts.json` / `import_v3_out_no_maa.json` / `import_v3_out_36h_layout.json` /
`import_v4_input.json`；再加上现成的 `arknights-infra-schedule-maa.json` 与 `scenarios/demo.json`。

运行：.venv/Scripts/python.exe -m unittest tests.test_import_blackbox -v
"""
from __future__ import annotations

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mood_soc import build_base_layout                                  # noqa: E402
from mood_soc.importer import (GLOBAL_RESOURCE_MAP, KIND_MAP, detect_format,  # noqa: E402
                              import_data, import_file, resolve_name)
from mood_soc.ledger import Bucket                                      # noqa: E402
from mood_soc.rules import mood_ledger                                  # noqa: E402
from ui.schedule import load_schedule, load_schedule_ex                 # noqa: E402

# 样例路径一律走 `data/paths.py`（数据的唯一路径出口）。
from data.paths import (  # noqa: E402
    MAA_SAMPLE as MAA,
    RES,
    SCENARIOS,
    V3_SAMPLE_36H as V3_36H,
    V3_SAMPLE_3SHIFTS as V3_3SHIFTS,
    V3_SAMPLE_NO_MAA as V3_NO_MAA,
    V4_SAMPLE as V4_INPUT,
)
SCENARIO = SCENARIOS / "demo.json"


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _rooms(facilities) -> list:
    """facilities → [(类型, 等级, 人数)]（断言用）。"""
    return [(f["type"], f["level"], len(f.get("operators") or [])) for f in facilities]


class Test格式识别(unittest.TestCase):
    """`detect_format`：四类各自的判定特征。"""

    def test_四类示例都能认出来(self):
        self.assertEqual(detect_format(_load(SCENARIO)), "scenario")
        self.assertEqual(detect_format(_load(MAA)), "maa")
        self.assertEqual(detect_format(_load(V3_3SHIFTS)), "v3_out")     # 信封
        self.assertEqual(detect_format(_load(V3_NO_MAA)), "v3_out")      # 裸 result
        self.assertEqual(detect_format(_load(V4_INPUT)), "plan_compute_v4")

    def test_v3输出带layout时仍算v3输出(self):
        """v3 输出里常回显输入 `layout` —— 不能因为看到 layout+rooms 就误判成 v4 蓝图。

        判据：`operbox` 才是 v4 蓝图的标志（v3 输出里没有它）。
        """
        data = _load(V3_36H)
        self.assertIn("layout", data)
        self.assertNotIn("operbox", data)
        self.assertEqual(detect_format(data), "v3_out")

    def test_认不出来时报错说清看到了什么(self):
        with self.assertRaises(ValueError) as ctx:
            detect_format({"hello": 1})
        msg = str(ctx.exception)
        for token in ("facilities", "result", "layout", "operbox", "plans", "hello"):
            self.assertIn(token, msg)
        with self.assertRaises(ValueError):
            detect_format([1, 2, 3])                                     # 顶层不是对象


class Testv3输出导入(unittest.TestCase):
    """v3 求解输出（有 rotation.shifts + maa 段 + assignment）。"""

    @classmethod
    def setUpClass(cls):
        cls.imp = import_file(V3_3SHIFTS)

    def test_每班一个班次且时长取duration_hours(self):
        self.assertEqual([str(s.hours) for s in self.imp.shifts], ["12.0", "6.0", "6.0"])
        labels = [s.label for s in self.imp.shifts]
        self.assertIn("alpha/beta", labels[0])          # 在班队伍只是标签，写进班次名
        self.assertTrue(labels[0].endswith("12.0h"))

    def test_房间类型来自maa段且空宿舍也建出来(self):
        """`maa.plans[].rooms` 是按类型分组的（含 `skip:true` 的空宿舍）——空房要留下。"""
        facs = self.imp.shifts[0].facilities
        kinds = [f["type"] for f in facs]
        for want in ("贸易站", "制造站", "发电站", "控制中枢", "办公室", "宿舍"):
            self.assertIn(want, kinds)
        dorms = [f for f in facs if f["type"] == "宿舍"]
        self.assertEqual(len(dorms), 2, "两间空宿舍也要建出来（闲置入宿能用）")
        self.assertTrue(all(not f["operators"] for f in dorms))
        # 制造站 3 人 → 等级按人数反推（MAA 段不带等级）
        manu = next(f for f in facs if f["type"] == "制造站")
        self.assertEqual(len(manu["operators"]), 3)
        self.assertGreaterEqual(manu["level"], 3)

    def test_练度按干员名从assignment补(self):
        """`maa` 段只有名字；`elite/level` 来自 `assignment.rooms[].operators[]`。"""
        trade = next(f for f in self.imp.shifts[0].facilities if f["type"] == "贸易站")
        specs = {o["name"]: o for o in trade["operators"] if isinstance(o, dict)}
        self.assertEqual(specs["巫恋"]["elite"], 2)
        self.assertEqual(specs["巫恋"]["level"], 80)
        self.assertEqual(specs["龙舌兰"]["level"], 60)

    def test_训练挂件进训练室(self):
        """`assignment.training_assist`（训练室不进 MAA 段）单独补一间训练室。"""
        facs = self.imp.shifts[0].facilities
        train = [f for f in facs if f["type"] == "训练室"]
        self.assertEqual(len(train), 1)
        self.assertEqual(train[0]["operators"][0]["name"], "临光")

    def test_菲亚梅塔开关映射到换心情(self):
        """`Fiammetta.enable=false` → 本工具的换心情保持关闭。"""
        self.assertEqual(self.imp.entry_events, {"enabled": False})
        self.assertFalse(self.imp.entry_enabled)

    def test_没用到推断(self):
        self.assertEqual(self.imp.report.inferred, [])

    def test_忽略的字段写进报告(self):
        ignored = "\n".join(self.imp.report.ignored)
        for token in ("rotation.daily", "training_advice", "profile",
                      "weighted_*", "efficiencies"):
            self.assertIn(token, ignored)

    def test_端到端装配成排班(self):
        ld = load_schedule_ex([V3_3SHIFTS])
        self.assertEqual(len(ld.schedule.shifts), 3)
        self.assertEqual(str(ld.schedule.cycle_hours), "24.0")
        self.assertEqual(ld.reports[0].kind, "v3_out")
        # 排班里真的有人、且练度带上去了
        op = ld.schedule.shifts[0].world.get_operator("巫恋")
        self.assertIsNotNone(op)
        self.assertEqual(int(op.elite), 2)
        self.assertEqual(int(op.level), 80)
        self.assertIn("已识别", ld.summary())


class Testv3输出缺maa段(unittest.TestCase):
    """只有 `assignment`（没有 maa 段、没有 layout）→ 类型/等级靠**兜底推断**并记账。"""

    @classmethod
    def setUpClass(cls):
        cls.imp = import_file(V3_NO_MAA)

    def test_类型由room_lines与room_id推断(self):
        kinds = [f["type"] for f in self.imp.shifts[0].facilities]
        self.assertIn("贸易站", kinds)      # room_lines 里的 trade_* 字段组
        self.assertIn("制造站", kinds)
        self.assertIn("发电站", kinds)
        self.assertIn("宿舍", kinds)        # 没有字段组 → room_id 前缀

    def test_没有线索的房间会说明并暂按制造站(self):
        mystery = [f for f in self.imp.shifts[0].facilities if f["operators"]
                   and any(o.get("name") == "诗怀雅" if isinstance(o, dict) else o == "诗怀雅"
                           for o in f["operators"])]
        self.assertEqual(len(mystery), 1)
        self.assertEqual(mystery[0]["type"], "制造站")
        self.assertTrue(any("mystery_1" in note for note in self.imp.report.inferred))

    def test_等级按人数反推(self):
        manu = next(f for f in self.imp.shifts[0].facilities if f["type"] == "制造站"
                    and len(f["operators"]) == 2)
        self.assertGreaterEqual(manu["level"], 2)      # 2 人 → 至少 Lv2

    def test_时长来自duration_hours(self):
        self.assertEqual([str(s.hours) for s in self.imp.shifts], ["12.0", "12.0"])

    def test_推断项都在报告里(self):
        self.assertTrue(self.imp.report.inferred)
        self.assertTrue(any("没有 layout" in note for note in self.imp.report.inferred))


class Testv3输出带layout与空班(unittest.TestCase):
    """同文件带 `layout` → 房间类型/等级以它为准；36h 周期；`rooms: []` 的空班合法。"""

    @classmethod
    def setUpClass(cls):
        cls.imp = import_file(V3_36H)
        cls.ld = load_schedule_ex([V3_36H])

    def test_周期36小时也吃得下(self):
        self.assertEqual(str(self.ld.schedule.cycle_hours), "36.0")
        self.assertEqual([str(s.hours) for s in self.ld.schedule.shifts], ["12.0"] * 3)

    def test_等级用layout的(self):
        dorm = next(f for f in self.imp.shifts[0].facilities if f["type"] == "宿舍")
        self.assertEqual(dorm["level"], 4)             # layout 里就是 4（不是按人数推的 1）
        control = next(f for f in self.imp.shifts[0].facilities if f["type"] == "控制中枢")
        self.assertEqual(control["level"], 5)

    def test_layout里的空房间也建出来(self):
        kinds = _rooms(self.imp.shifts[0].facilities)
        self.assertIn(("办公室", 3, 0), kinds)          # MAA 段里有这间、没人
        self.assertIn(("会客室", 3, 0), kinds)

    def test_空班是合法的(self):
        """`assignment.rooms: []` 是契约里的合法值（人数不足/该班空缺），不是解析错误。"""
        third = self.ld.schedule.shifts[2]
        self.assertEqual([o for f in shift_facilities(third) for o in f["operators"]], [])
        ld = self.ld
        self.assertEqual(len(ld.schedule.shifts), 3)

    def test_菲亚梅塔逐班差异照搬(self):
        """plans 1/2 开着（换巫恋）、plan 3 关着 → 全局开 + 一条逐班覆盖。"""
        self.assertTrue(self.imp.entry_events["enabled"])
        self.assertEqual(self.imp.entry_events.get("swap_with"), "巫恋")
        self.assertEqual(self.imp.entry_events.get("per_shift"),
                         [{"key": 3, "enabled": False, "swap_with": ""}])


def shift_facilities(shift) -> list:
    """某个 Shift 的原始 facilities（`Shift.facilities` 就是场景格式）。"""
    return list(shift.facilities)


class Testv4蓝图与干员池(unittest.TestCase):
    """`plan_compute_v4` 输入：房间建好但**没有人**，干员池可用。"""

    @classmethod
    def setUpClass(cls):
        cls.imp = import_file(V4_INPUT)
        cls.ld = load_schedule_ex([V4_INPUT])

    def test_房间按kind与level建好且都是空的(self):
        facs = self.imp.shifts[0].facilities
        self.assertEqual([f["type"] for f in facs], ["控制中枢", "制造站", "贸易站", "宿舍"])
        self.assertEqual([f["level"] for f in facs], [5, 3, 4, 2])
        self.assertTrue(all(not f["operators"] for f in facs))

    def test_宿舍床位与氛围映射(self):
        dorm = next(f for f in self.imp.shifts[0].facilities if f["type"] == "宿舍")
        self.assertEqual(dorm["slots"], 10)             # dorm_beds
        self.assertEqual(dorm["atmosphere"], 3000)      # dorm_ambience_level=3 → 满氛围

    def test_rotation决定班次与时长(self):
        self.assertEqual([str(s.hours) for s in self.imp.shifts], ["8", "8", "4", "4"])
        self.assertEqual(str(self.ld.schedule.cycle_hours), "24")

    def test_干员池带练度且排除未拥有(self):
        pool = {p["name"]: p for p in self.imp.pool}
        self.assertIn("阿米娅", pool)                    # Amiya → 中文名
        self.assertEqual(pool["阿米娅"]["elite"], 2)
        self.assertEqual(pool["阿米娅"]["level"], 90)
        self.assertIn("摩根", pool)                      # char_154_morgan → 中文名
        self.assertNotIn("Trainee", pool)                # own=false 不进池
        self.assertTrue(any("own=false" in n for n in self.imp.report.notes))

    def test_initial_global作为变量初始值(self):
        self.assertEqual({k: str(v) for k, v in self.imp.initial_global.items()},
                         {"木天蓼": "5", "人间烟火": "30", "热情值": "40"})
        # 映射不到的键（virtual_power）要说出来，不能悄悄丢
        self.assertTrue(any("virtual_power" in n for n in self.imp.report.inferred))
        self.assertEqual(len(GLOBAL_RESOURCE_MAP), 14)

    def test_fiammetta_enable映射(self):
        self.assertEqual(self.imp.entry_events, {"enabled": True})

    def test_端到端变量初始值真的进模型(self):
        """`initial_global.热情值=40` 让丰川祥子「生活的重压」（需热情值≥40）生效。"""
        ld = load_schedule_ex([V4_INPUT])
        shift = ld.schedule.shifts[0]
        self.assertEqual({k: str(v) for k, v in shift.initial_global.items()},
                         {"木天蓼": "5", "人间烟火": "30", "热情值": "40"})


class Test干员名解析(unittest.TestCase):
    """别的工具可能写英文名或干员 id（`data/operator_names.py`）。"""

    def test_英文名与干员id都能译成中文名(self):
        self.assertEqual(resolve_name("Amiya"), "阿米娅")
        self.assertEqual(resolve_name("Exusiai"), "能天使")
        self.assertEqual(resolve_name("char_154_morgan"), "摩根")
        self.assertEqual(resolve_name("巫恋"), "巫恋")          # 本来就是中文名
        self.assertEqual(resolve_name("Nobody-XX"), "Nobody-XX")  # 认不出就原样保留

    def test_认不出的名字会进报告的未匹配清单(self):
        data = {"schema_version": 4,
                "layout": {"rooms": [{"id": "r1", "kind": "control_center", "level": 5}]},
                "operbox": [{"name": "Nobody-XX", "elite": 2, "level": 90, "own": True,
                             "potential": 1, "rarity": 6}]}
        imp = import_data(data)
        self.assertEqual(imp.report.unknown, ["Nobody-XX"])      # 名字完全不认得
        self.assertEqual(imp.report.unmatched, [])
        self.assertEqual(imp.pool[0]["name"], "Nobody-XX")       # 原样保留，按无技能参与

    def test_认得但没有心情技能的另算一档(self):
        """能天使那种：人认得（在 `operators.txt` 里），但她的基建技能是**产出类**。

        这两件事在报告里分量不同：一个是"我们不认识这个名字"，一个是"认识但帮不上心情"。
        """
        imp = import_file(V4_INPUT)
        self.assertIn("能天使", imp.report.unmatched)
        self.assertEqual(imp.report.unknown, [])
        self.assertIn("没有心情技能", imp.report.summary())


class TestMAA与场景回归(unittest.TestCase):
    """老两类文件的导入行为不许被改坏（只增不减）。"""

    def test_maa排班仍然一班次一plan(self):
        ld = load_schedule_ex([MAA])
        self.assertEqual([str(s.hours) for s in ld.schedule.shifts], ["12", "6", "6"])
        self.assertEqual(len(ld.schedule.shifts), 3)
        self.assertEqual(str(ld.schedule.cycle_hours), "24")

    def test_maa的Fiammetta也映射进来(self):
        """示例 MAA 里 plan1/2 开着（换不同的人）、plan3 关着 → 逐班照搬。"""
        imp = import_file(MAA)
        self.assertEqual(imp.kind, "maa")
        self.assertTrue(imp.entry_events["enabled"])
        self.assertIsNone(imp.entry_events.get("swap_with"))     # 各班目标不同 → 不指定
        self.assertEqual({o["key"] for o in imp.entry_events["per_shift"]}, {2, 3})

    def test_场景文件仍然一个文件一个班次(self):
        ld = load_schedule_ex([SCENARIO])
        self.assertEqual(len(ld.schedule.shifts), 1)
        self.assertEqual(ld.reports[0].kind, "scenario")

    def test_三文件装配仍然成立(self):
        """12h/6h/6h 三个文件（每个一班）→ 三班排班。"""
        one = SCENARIO
        ld = load_schedule_ex([one, one, one], hours=[Decimal("12"), Decimal("6"), Decimal("6")])
        self.assertEqual([str(s.hours) for s in ld.schedule.shifts], ["12", "6", "6"])
        self.assertEqual(str(ld.schedule.cycle_hours), "24")

    def test_load_schedule仍返回排班本体(self):
        self.assertEqual(len(load_schedule([MAA]).shifts), 3)


class Test报告(unittest.TestCase):
    """导入报告要能一眼看出"读到什么、推断什么、忽略什么"。"""

    def test_摘要与明细(self):
        imp = import_file(V4_INPUT)
        self.assertIn("v4 蓝图+干员池", imp.report.summary())
        details = imp.report.details()
        for token in ("格式：", "来源：", "读到：", "忽略（本模型不建模）："):
            self.assertIn(token, details)
        self.assertEqual(imp.report.to_dict()["kind"], "plan_compute_v4")

    def test_英文名全部译得出来(self):
        """英文名/干员 id 不该落进"名字不认得"那一档（`data/operator_names.py`）。"""
        imp = import_file(V4_INPUT)
        self.assertEqual(imp.report.unknown, [])
        self.assertEqual({p["name"] for p in imp.pool}, {"阿米娅", "能天使", "巫恋", "摩根"})


if __name__ == "__main__":
    unittest.main(verbosity=2)

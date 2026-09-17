"""tests/test_api_ops.py —— 程序接口（`api/`）黑盒：**JSON 进 / JSON 出**，且与引擎同号。

只 import `api.*` 与 `store.*`，不 import `ui`（有测试盯着"程序接口不许拉 tkinter"）。

五组断言：

1. **握手**：`capabilities` 报的协议版本与 op 表一致（调用方靠它发现版本漂移）；
2. **全流程**：载入布局 → 改设置 → 算心情 → 恢复，每步都走 op（不直接碰 Session）；
3. **数值同源**：同一布局经 `store.session.Session` 直调与经 `api` 调用，
   心情/速率**逐位相等**（防"界面算一套、API 算另一套"）；
4. **协议形状**：请求/响应字段、错误映射（`ValueError` → `bad_request`）、
   `quit` 结束、坏 JSON 不杀进程；
5. **时刻写法**：小时数（绝对）与 `"HH:MM"`（周期内）等价。

运行：.venv/Scripts/python.exe -m unittest tests.test_api_ops -v
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import PROTOCOL_VERSION                                   # noqa: E402
from api.ops import OPERATIONS, handle                             # noqa: E402
from api.protocol import make_response, parse_request              # noqa: E402
from api.server import serve                                       # noqa: E402
from data.paths import MAA_SAMPLE                                  # noqa: E402
from store.session import Session                                  # noqa: E402

LAYOUT = {"facilities": [
    {"type": "控制中枢", "level": 1,
     "operators": ["玛恩纳", "维什戴尔", "魔王", "令", "路人中枢"]},
    {"type": "制造站", "level": 3, "operators": ["泡泡", "黍", "路人甲"]},
    {"type": "贸易站", "level": 3, "operators": ["火哨", "巫恋", "路人乙"]},
    {"type": "宿舍", "level": 5, "operators": ["菲亚梅塔"]},
]}


def call(session: Session, op: str, **args):
    """走 op 表调一次（等价于线协议里的一次请求）。"""
    return handle(session, op, args)


class Test握手(unittest.TestCase):
    def test_capabilities_reports_protocol_and_ops(self):
        data = call(Session(), "capabilities")
        self.assertEqual(data["protocol"], PROTOCOL_VERSION)
        self.assertEqual(set(data["operations"]), set(OPERATIONS))
        self.assertFalse(data["loaded"])

    def test_unknown_op_is_bad_request(self):
        with self.assertRaises(ValueError) as ctx:
            call(Session(), "没有这个op")
        self.assertIn("未知操作", str(ctx.exception))

    def test_ops_requiring_session_complain_clearly(self):
        for op, args in (("list_shifts", {}), ("moods", {}), ("trajectory", {})):
            with self.assertRaises(ValueError) as ctx:
                call(Session(), op, **args)
            self.assertIn("尚未载入排班", str(ctx.exception), op)


class Test全流程(unittest.TestCase):
    """一次完整使用：载入 → 看 → 改 → 算 → 恢复（全走 op）。"""

    def setUp(self):
        self.s = Session()
        call(self.s, "load_schedule", **LAYOUT)

    def test_载入后可描述(self):
        d = call(self.s, "describe")
        self.assertTrue(d["loaded"])
        self.assertEqual(d["cycle_hours"], "24")
        self.assertEqual(len(d["shifts"]), 1)
        self.assertIn("泡泡", d["operators"])

    def test_moods_给出全员心情与瓶颈(self):
        d = call(self.s, "moods", at=[0, 8], include_trajectory=True)
        self.assertIn("泡泡", d["moods"]["0"])
        self.assertEqual(d["moods"]["0"]["泡泡"], 24)
        self.assertLess(d["moods"]["8"]["泡泡"], 24)
        # 轨迹：所有人都有等长数列，且与 times 对齐
        self.assertEqual(len(d["trajectory"]["times"]), len(d["trajectory"]["operators"]["泡泡"]))
        self.assertIn("layout_sustain_hours", d)

    def test_钟点写法与小时数等价(self):
        a = call(self.s, "moods", at=["08:30"])["moods"]
        b = call(self.s, "moods", at=[8 + 0.5])["moods"]
        self.assertEqual(a, b)
        c = call(self.s, "moods", at=["24:00"])["moods"]      # 周期末
        self.assertEqual(list(c), ["24"])

    def test_设置心情与锚点(self):
        call(self.s, "set_moods", moods={"泡泡": 12})
        self.assertEqual(call(self.s, "moods", at=[0])["moods"]["0"]["泡泡"], 12)
        # 第 2 周期 8h 的锚点：那一刻跳到你给的值（**先把周期数开到 2**，否则它超范围失效）
        call(self.s, "set_timeline", cycles=2)
        call(self.s, "set_mood_at", name="泡泡", mood=3, at=24 + 8)
        self.assertEqual(call(self.s, "moods", at=[32])["moods"]["32"]["泡泡"], 3)
        self.assertTrue(call(self.s, "get_settings")["mood_events"])
        call(self.s, "clear_mood_events")
        self.assertFalse(call(self.s, "get_settings")["mood_events"])
        call(self.s, "restore_imported_moods")
        self.assertEqual(call(self.s, "moods", at=[0])["moods"]["0"]["泡泡"], 24)

    def test_改槽位改等级(self):
        before = call(self.s, "list_shifts")["shifts"][0]["operators"]
        call(self.s, "set_slots", shift_index=1, facility_index=2, operators=["泡泡"])
        after = call(self.s, "list_shifts")["shifts"][0]["operators"]
        self.assertIn("泡泡", after)
        self.assertLess(len(after), len(before))
        d = call(self.s, "set_room_level", shift_index=1, facility_index=2, level=1)
        self.assertIn("validation", d)

    def test_练度改动只写非满练(self):
        d = call(self.s, "set_training", elite=1, names=["泡泡"])
        self.assertGreaterEqual(d["changed"], 1)
        self.assertEqual(d["badges"].get("泡泡"), "E1")
        call(self.s, "set_training", elite=2, level=30, names=["泡泡"])
        self.assertNotIn("泡泡", call(self.s, "set_training", elite=2, level=30)["badges"])

    def test_换心情与闲置入宿(self):
        d = call(self.s, "set_entry_events", enabled=True, swap_with="any", scope="anywhere",
                 when="wait")
        self.assertTrue(d["settings"]["enabled"])
        self.assertEqual(d["settings"]["when"], "wait")
        with self.assertRaises(ValueError):
            call(self.s, "set_entry_events", when="乱七八糟")
        # 单班排班里所有人整周期都在岗或满心情 ⇒ 没有"闲置未满"的人，逐次表就该是空的
        d2 = call(self.s, "set_idle_to_dorm", enabled=True,
                  per_operator={"路人乙": {"target": "宿舍01"}})
        self.assertTrue(d2["enabled"])
        self.assertEqual(d2["groups"], [])
        self.assertTrue(call(self.s, "get_settings")["idle_to_dorm"]["per_operator"])

    def test_闲置入宿逐次表(self):
        """三班轮换的示例排班 ⇒ 逐次表按"周期 × 班次"展开，且每行给得出候选与可选目标。"""
        s = Session()
        call(s, "load_file", path=str(MAA_SAMPLE))
        d = call(s, "set_idle_to_dorm", enabled=True, cycles=1)
        self.assertTrue(d["enabled"])
        self.assertIsInstance(d["groups"], list)
        again = call(s, "idle_to_dorm_groups")["groups"]
        self.assertEqual(len(again), len(d["groups"]))
        for group in again:
            self.assertIn("第 1 周期", group["title"])
            for row in group["rows"]:
                self.assertIn("name", row)
                self.assertIsInstance(row["options"], list)

    def test_时间轴改时长与周期数(self):
        d = call(self.s, "set_timeline", cycles=2)
        self.assertEqual(d["cycles"], 2)
        self.assertEqual(d["total_hours"], "48")
        with self.assertRaises(ValueError):
            call(self.s, "set_timeline", cycle_hours=12)     # 与各班长之和不符
        call(self.s, "set_timeline", start_clock=1)          # 只改显示口径
        self.assertEqual(call(self.s, "get_settings")["start_clock"], "1")

    def test_流水账与单点查询(self):
        lg = call(self.s, "mood_ledger", name="泡泡")
        self.assertEqual(lg["operator"], "泡泡")
        self.assertIn("explain", lg)
        self.assertIn("囤积者", lg["explain"])
        one = call(self.s, "time_to_mood", name="泡泡", mood=18)
        self.assertGreater(one["hours"], 0)
        detail = call(self.s, "operator_detail", name="泡泡")
        self.assertEqual(detail["facility"], "制造站")
        self.assertIn("rate", detail)

    def test_导出回场景JSON(self):
        d = call(self.s, "export_schedule")
        sc = d["shifts"][0]["scenario"]
        self.assertEqual(sc["facilities"][0]["type"], "控制中枢")
        # 导出的场景能再被载入（闭环）
        s2 = Session()
        call(s2, "load_schedule", **sc)
        self.assertEqual(set(call(s2, "describe")["operators"]),
                         set(call(self.s, "describe")["operators"]))

    def test_不在基建名单(self):
        """`set_detached` / `bench_names`：既不在工作设施、也不在宿舍的人。

        语义：轨迹里**一条平线**（心情恒定）、**不占进驻位**、不参与任何技能计数；
        `moods` / `trajectory` 里照样能看到他们。
        """
        d = call(self.s, "set_detached", names=["板凳甲"], moods={"板凳甲": 11})
        self.assertEqual(d["detached"], ["板凳甲"])
        self.assertEqual(d["detached_explicit"], ["板凳甲"])
        self.assertEqual(call(self.s, "bench_names")["detached"], ["板凳甲"])
        # 平线：三个时刻都是 11
        moods = call(self.s, "moods", at=[0, 12, 24])["moods"]
        for key in ("0", "12", "24"):
            self.assertEqual(moods[key]["板凳甲"], 11, key)
        # 曲线里也有他（一条平线），速率 0
        traj = call(self.s, "trajectory")
        self.assertIn("板凳甲", traj["operators"])
        self.assertEqual(set(traj["operators"]["板凳甲"]), {11})
        self.assertEqual(call(self.s, "operator_detail", name="板凳甲")["rate"], 0)
        # 清空**名单**：`detached_explicit` 为空、`bench_names` 也空了
        # （她本来就没占任何位置，所以清了名单之后她就跟"从没排过班的人"一样了）
        self.assertEqual(call(self.s, "set_detached", names=[])["detached_explicit"], [])
        self.assertEqual(call(self.s, "bench_names")["detached"], [])
        self.assertEqual(call(self.s, "moods", at=[12])["moods"]["12"].get("板凳甲"), None)

    def test_不在基建的人从位置摘下来(self):
        """`add` 默认连位置一起摘（否则她其实还在岗，与"不在基建"矛盾）。"""
        call(self.s, "set_detached", add=["泡泡"])
        d = call(self.s, "describe")
        stationed = [n for s in d["shifts"] for f in s["facilities"] for n in f[2]]
        self.assertNotIn("泡泡", stationed)
        self.assertIn("泡泡", d["detached"])

    def test_导出带去不在基建(self):
        call(self.s, "set_detached", names=["板凳甲"])
        ex = call(self.s, "export_schedule")
        self.assertEqual(ex["shifts"][0]["scenario"]["detached"], ["板凳甲"])
        self.assertEqual(ex["detached"], ["板凳甲"])

    def test_按文件载入四种格式(self):
        for op, args in (("load_file", {"path": str(MAA_SAMPLE)}),):
            s = Session()
            d = call(s, op, **args)
            self.assertTrue(d["loaded"])
            self.assertGreaterEqual(len(d["shifts"]), 1)
            self.assertIn("已识别", d["import"])
            self.assertTrue(call(s, "import_report")["reports"])


class Test数值同源(unittest.TestCase):
    """API 与引擎（Session 直调）必须给出**逐位相同**的数值。

    线协议上的数字按 6 位小数舍入（`store.serialize._num`，与 CLI / 文件输出同一口径），
    所以"逐位相同"= 把引擎值也量化到 6 位小数再比。
    """

    QUANT = Decimal("0.000001")

    def _quant(self, value) -> Decimal:
        return Decimal(value).quantize(self.QUANT)

    def test_moods_match_direct_session(self):
        s = Session()
        call(s, "load_schedule", **LAYOUT)
        call(s, "set_timeline", cycles=2)
        call(s, "set_moods", moods={"泡泡": 20})
        d = call(s, "moods", at=[0, 8, 24, 30])

        direct = Session()
        direct.load_layout(LAYOUT)
        direct.set_cycles(2)
        direct.set_initial_moods({"泡泡": 20})
        direct.recompute()
        for key, values in d["moods"].items():
            for name, value in values.items():
                self.assertEqual(self._quant(value),
                                 self._quant(direct.mood_at(name, Decimal(key))),
                                 f"{name}@{key}")

    def test_cli_and_engine_agree(self):
        """命令行（子进程，新解释器）与进程内 Session 结果一致。"""
        args = json.dumps(LAYOUT, ensure_ascii=False)
        r = subprocess.run(
            [sys.executable, "-m", "api.cli", "--op", "load_schedule", "--args", args,
             "--then", json.dumps({"op": "moods", "args": {"at": [8]}}, ensure_ascii=False),
             "--quiet"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        s = Session()
        s.load_layout(LAYOUT)
        for name, value in data["moods"]["8"].items():
            self.assertEqual(self._quant(value), self._quant(s.mood_at(name, Decimal(8))), name)

    def test_cli_reports_errors_on_stderr_with_exit_code(self):
        """错误绝不混进 stdout：否则 `moods = $(...)` 会把错误当数据用。"""
        r = subprocess.run([sys.executable, "-m", "api.cli", "--op", "moods"],
                           cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "")
        self.assertIn("bad_request", r.stderr)


class Test协议形状(unittest.TestCase):
    def test_parse_request(self):
        self.assertIsNone(parse_request(""))
        self.assertIsNone(parse_request("   \n"))
        self.assertEqual(parse_request('{"op":"capabilities"}'), {"op": "capabilities"})
        with self.assertRaises(ValueError):
            parse_request("{不是 json")
        with self.assertRaises(ValueError):
            parse_request("[1,2,3]")

    def test_response_shape(self):
        ok = make_response("moods", {"a": 1}, req_id=7)
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["id"], 7)
        self.assertEqual(ok["protocol"], PROTOCOL_VERSION)
        bad = make_response("moods", error={"type": "bad_request", "message": "x"})
        self.assertFalse(bad["ok"])
        self.assertNotIn("id", bad)

    def test_server_ndjson_roundtrip(self):
        """真跑一遍 NDJSON 循环：握手 → 载入 → 算心情 → quit。"""
        lines = [
            json.dumps({"id": 1, "op": "capabilities"}),
            json.dumps({"id": 2, "op": "load_schedule", "args": LAYOUT}, ensure_ascii=False),
            json.dumps({"id": 3, "op": "moods", "args": {"at": [0]}}),
            "",                                                     # 空行要忽略
            json.dumps({"id": 4, "op": "quit"}),
        ]
        stdin = io.StringIO("\n".join(lines) + "\n")
        stdout, stderr = io.StringIO(), io.StringIO()
        code = serve(stdin, stdout, stderr, quiet=True)
        self.assertEqual(code, 0)
        out = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
        self.assertEqual([r["id"] for r in out], [1, 2, 3, 4])
        self.assertTrue(all(r["ok"] for r in out))
        self.assertEqual(out[1]["data"]["loaded"], True)
        self.assertIn("泡泡", out[2]["data"]["moods"]["0"])
        self.assertTrue(out[3]["data"]["bye"])

    def test_server_survives_bad_requests(self):
        """坏 JSON / 未知 op / 参数错都不许杀进程（长连接的命根子）。"""
        lines = [
            "{坏 json",
            json.dumps({"id": 1, "op": "nope"}),
            json.dumps({"id": 2, "op": "moods"}),                  # 未载入 → bad_request
            json.dumps({"op": "capabilities"}),                    # 无 id 也要能跑
            json.dumps({"id": 3, "op": "quit"}),
        ]
        stdin = io.StringIO("\n".join(lines) + "\n")
        stdout, stderr = io.StringIO(), io.StringIO()
        self.assertEqual(serve(stdin, stdout, stderr, quiet=True), 0)
        out = [json.loads(l) for l in stdout.getvalue().splitlines()]
        self.assertEqual(len(out), 5)
        self.assertFalse(out[0]["ok"])
        self.assertEqual(out[1]["error"]["type"], "bad_request")
        self.assertEqual(out[2]["error"]["type"], "bad_request")
        self.assertTrue(out[3]["ok"])

    def test_eof_exits_cleanly(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        self.assertEqual(serve(io.StringIO(""), stdout, stderr, quiet=True), 0)
        self.assertEqual(stdout.getvalue(), "")


class Test不拉图形界面(unittest.TestCase):
    def test_api_does_not_import_tkinter(self):
        """程序接口必须能在无图形环境跑：`api` / `store` 都不许把 tkinter 拖进来。"""
        code = (
            "import sys; sys.path.insert(0, r'%s');"
            "import api.ops, api.server, api.cli, store.session, store.schedule;"
            "assert 'tkinter' not in sys.modules, sorted(m for m in sys.modules if 'tk' in m);"
            "print('ok')" % ROOT
        )
        r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok", r.stdout)


if __name__ == "__main__":
    unittest.main()

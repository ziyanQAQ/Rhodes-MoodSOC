"""tests/test_cli_blackbox.py —— 黑盒测试：通过命令行断言"输入 → 输出"。

用 subprocess 调用 main.py，把 stdout 当作 JSON 解析、验证字段，并检查结果文件与错误退出。
不 import 项目内部任何模块。

运行：.venv/Scripts/python.exe -m unittest tests.test_cli_blackbox -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# 项目根目录（tests 的上一级），main.py 所在位置
ROOT = Path(__file__).resolve().parent.parent

# 一个确定性场景：满员中枢（0.25 减免）+ L3 制造站（泡泡）+ 满级宿舍（菲亚梅塔）
SCENARIO = {
    "facilities": [
        {"type": "控制中枢", "level": 1,
         "operators": ["路人1", "路人2", "路人3", "路人4", "路人5"]},
        {"type": "制造站", "level": 3, "operators": ["泡泡", "路人甲", "路人乙"]},
        {"type": "宿舍", "level": 5, "operators": [{"name": "菲亚梅塔", "mood": "10"}]},
    ]
}


class TestCLI(unittest.TestCase):
    def _run(self, *args):
        """以项目根目录为工作目录，运行 main.py，返回 CompletedProcess。

        强制 PYTHONIOENCODING=utf-8，避免 Windows 上子进程 stdout 用 GBK 编码
        导致中文 JSON 被误读。
        """
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "main.py", *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )

    def test_single_mode_outputs_json(self):
        """single 模式：stdout 为纯 JSON；默认不生成结果文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            sc = Path(tmp) / "scenario.json"
            sc.write_text(json.dumps(SCENARIO, ensure_ascii=False), encoding="utf-8")
            r = self._run("--scenario-file", str(sc), "--target", "泡泡",
                          "--period", "8", "--out-dir", tmp)
            self.assertEqual(r.returncode, 0, r.stderr)

            data = json.loads(r.stdout)              # stdout 应为纯 JSON
            self.assertEqual(data["mode"], "single")
            self.assertEqual(data["operator"], "泡泡")
            self.assertEqual(data["period_hours"], 8)
            self.assertAlmostEqual(data["mood"], 20.8, places=6)   # 24 - 0.4*8
            self.assertEqual(data["sustain_hours"], 52)           # 20.8 / 0.4

            self.assertFalse(any(Path(tmp).glob("single_*.json")))  # 默认不生成文件

    def test_base_mode_outputs_json(self):
        """base 模式：stdout 为纯 JSON，含布局可维持时长与瓶颈。"""
        with tempfile.TemporaryDirectory() as tmp:
            sc = Path(tmp) / "scenario.json"
            sc.write_text(json.dumps(SCENARIO, ensure_ascii=False), encoding="utf-8")
            r = self._run("--mode", "base", "--scenario-file", str(sc), "--out-dir", tmp)
            self.assertEqual(r.returncode, 0, r.stderr)

            data = json.loads(r.stdout)
            self.assertEqual(data["mode"], "base")
            self.assertEqual(data["layout_sustain_hours"], 32)
            self.assertEqual(data["bottleneck"], "路人1")
            self.assertEqual(len(data["operators"]), 9)
            by_name = {o["name"]: o for o in data["operators"]}
            # 工作干员 sustain_hours 一致 = 32；宿舍干员 (24-10)/2=7 提前恢复满
            self.assertEqual(by_name["路人1"]["sustain_hours"], 32)
            self.assertEqual(by_name["泡泡"]["sustain_hours"], 32)
            self.assertEqual(by_name["菲亚梅塔"]["sustain_hours"], 7)
            # mood_at_end：瓶颈=0，泡泡=11.2，宿舍=24
            self.assertEqual(by_name["路人1"]["mood_at_end"], 0)
            self.assertAlmostEqual(by_name["泡泡"]["mood_at_end"], 11.2, places=6)
            self.assertEqual(by_name["菲亚梅塔"]["mood_at_end"], 24)
            self.assertFalse(any(Path(tmp).glob("base_*.json")))  # 默认不生成文件

    def test_json_file_generation(self):
        """--json-file 时额外生成结果 JSON 文件，并在 stderr 提示路径。"""
        with tempfile.TemporaryDirectory() as tmp:
            sc = Path(tmp) / "scenario.json"
            sc.write_text(json.dumps(SCENARIO, ensure_ascii=False), encoding="utf-8")
            r = self._run("--scenario-file", str(sc), "--target", "泡泡",
                          "--period", "8", "--out-dir", tmp, "--json-file")
            self.assertEqual(r.returncode, 0, r.stderr)

            data = json.loads(r.stdout)
            self.assertEqual(data["mode"], "single")
            self.assertTrue(any(Path(tmp).glob("single_*.json")))
            self.assertIn("已生成结果文件", r.stderr)

    def test_unknown_target_exits_nonzero(self):
        """目标干员不存在 → 非零退出码，stderr 含错误提示。"""
        with tempfile.TemporaryDirectory() as tmp:
            sc = Path(tmp) / "scenario.json"
            sc.write_text(json.dumps(SCENARIO, ensure_ascii=False), encoding="utf-8")
            r = self._run("--scenario-file", str(sc), "--target", "不存在的干员")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("错误", r.stderr)

    def test_entry_events_from_scenario_json(self):
        """场景 JSON 顶层写 `entry_events` 即可开启结算，**不必**加 `--entry-events`；
        `swap_with` 指定与谁互换。"""
        scenario = {
            "entry_events": {"enabled": True, "swap_with": "乙"},
            "facilities": [
                {"type": "宿舍", "level": 5, "operators": [
                    {"name": "甲", "mood": "6"},
                    {"name": "乙", "mood": "9"},
                    {"name": "菲亚梅塔", "mood": "24"},
                ]},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            sc = Path(tmp) / "dorm.json"
            sc.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
            r = self._run("--scenario-file", str(sc), "--target", "菲亚梅塔")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("进驻事件", r.stderr)              # 事件流水账走 stderr
            self.assertIn("乙", r.stderr)
            data = json.loads(r.stdout)
            self.assertEqual(data["mood"], 9)               # 与乙互换：24 → 9

            # 同一份 JSON 改成不开 → 心情保持 24
            scenario["entry_events"] = {"enabled": False, "swap_with": "乙"}
            sc.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
            r2 = self._run("--scenario-file", str(sc), "--target", "菲亚梅塔")
            self.assertEqual(json.loads(r2.stdout)["mood"], 24)

            # 显式 --entry-events 优先于 JSON 的 false
            r3 = self._run("--scenario-file", str(sc), "--target", "菲亚梅塔", "--entry-events")
            self.assertEqual(json.loads(r3.stdout)["mood"], 9)

if __name__ == "__main__":
    unittest.main(verbosity=2)

"""main.py —— 命令行入口。

两种模式（--mode）：
  single（默认）只计算一个干员：
      输入「当前干员信息 + 基建布局 + 目标时段」，输出该干员时段后的剩余心情，
      以及"其余干员心情无限"时从剩余心情起算还能工作多久。
  base   计算整个基建内所有干员：
      基于当前布局，计算每个干员的心情，以及"没有一个干员红脸"的条件下，
      该布局还能维持工作多久（= 所有干员到红脸时长的最小值）。

输出：JSON 打印到标准输出；加 `--json-file` 才额外写入 results/ 目录下的文件（默认不写）。

用法示例（single 模式**必须**给 `--target`；`--period` 两种场景来源都缺省 0）：
  python main.py --demo --target 泡泡                       # 演示场景，single 模式，目标泡泡
  python main.py --demo --target 泡泡 --period 8            # 推进 8 小时
  python main.py --scenario-file scenarios/demo.json --target 泡泡 --period 8
  python main.py --mode base --scenario-file scenarios/demo.json
  python main.py --mode base --demo --period 12             # 先推进 12h 再评估布局可持续性
  python main.py --demo --target 泡泡 --json-file           # 额外把结果写入 JSON 文件
  python main.py --demo --target 泡泡 --explain             # 打印心情流水账（为什么是这个速率）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from decimal import Decimal

from mood_soc import (apply_entry_events, apply_idle_to_dorm, build_base_layout, evaluate,
                      evaluate_base)
from mood_soc.battery import to_decimal
from mood_soc.output import base_result_to_dict, dump_json, mood_result_to_dict, to_json_string

# 内置演示场景：中枢满员 + 2 个制造站 / 1 个贸易站 / 办公室 / 满级宿舍
# （与 scenarios/demo.json 只差办公室干员：这里用「遥」，demo.json 用「斥罪」）
DEMO_SCENARIO = {
    "facilities": [
        # 中枢满员：玛恩纳提供回复 + 维什戴尔提供减免（含魔王联动）+ 令（消除岁）
        {"type": "控制中枢", "level": 1,
         "operators": ["玛恩纳", "维什戴尔", "魔王", "令", "路人中枢"]},
        # 制造站 1（满 3 人）：泡泡自身减耗 + 黍设施减耗
        {"type": "制造站", "level": 3,
         "operators": ["泡泡", "黍", "路人甲"]},
        # 制造站 2（满 3 人）：槐琥消除阿罗玛/火神的自身心情影响
        {"type": "制造站", "level": 3,
         "operators": ["槐琥", "阿罗玛", "火神"]},
        # 三级贸易站，满 3 人（火哨减耗 + 巫恋加耗）
        {"type": "贸易站", "level": 3,
         "operators": ["火哨", "巫恋", "路人乙"]},
        # 办公室（斥罪有心情加耗）
        {"type": "办公室", "level": 3, "operators": ["遥"]},
        # 宿舍（满级，菲亚梅塔特殊回复）
        {"type": "宿舍", "level": 5, "operators": ["菲亚梅塔"]},
    ]
}


def load_scenario(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _timestamp() -> str:
    """生成文件名时间戳，避免多次运行互相覆盖。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def main() -> int:
    parser = argparse.ArgumentParser(description="心情电池（BMS-SOC）测算工具")
    parser.add_argument("--mode", choices=["single", "base"], default="single",
                        help="single=只算一个干员；base=算整个基建布局")
    parser.add_argument("--demo", action="store_true", help="运行内置演示场景")
    parser.add_argument("--scenario-file", help="场景 JSON 文件路径")
    parser.add_argument("--target", help="目标干员名（仅 single 模式）")
    parser.add_argument("--period", type=float, default=0.0, help="目标时段（小时）")
    parser.add_argument("--out-dir", default="results", help="结果 JSON 输出目录（仅在 --json-file 时使用）")
    parser.add_argument("--json-file", action="store_true", default=False,
                        help="是否额外把结果写入 JSON 文件（默认 False，只打印到标准输出）")
    parser.add_argument("--trace", action="store_true",
                        help="仅 single 模式：附加心情轨迹（时间步进模拟，文本）")
    parser.add_argument("--explain", action="store_true",
                        help="仅 single 模式：打印心情流水账（逐条来源 + 轴 F 叠加规则，文本）")
    parser.add_argument("--entry-events", action="store_true", default=False,
                        help="结算**进驻瞬间的一次性事件**（M15a 患难之交：菲亚梅塔进驻宿舍时"
                             "与同宿舍某人互换心情）后再测算；不指定则按布局给出的心情原样测算。"
                             "场景 JSON 顶层也可写 \"entry_events\": {\"enabled\": true, "
                             "\"swap_with\": \"某人\"} 来开启并指定与谁互换")
    parser.add_argument("--idle-to-dorm", action="store_true", default=False,
                        help="结算闲置入宿：把「没在上班、也不在宿舍、心情还没满」的干员"
                             "安排进宿舍（有空位就放进去，没空位就与宿舍里心情已满的那位"
                             "互换）；场景 JSON 顶层也可写 \"idle_to_dorm\": {\"enabled\": true, "
                             "\"per_operator\": {\"某人\": \"换谁\"}}")
    args = parser.parse_args()

    # 1) 场景来源
    if args.demo:
        data = DEMO_SCENARIO
    elif args.scenario_file:
        data = load_scenario(args.scenario_file)
    else:
        parser.print_help()
        return 0

    world = build_base_layout(data)

    # 1.5) 进驻事件（可选）：M15a 患难之交等"进驻瞬间的一次性结算"，会**就地**改心情。
    #      开关来源：命令行 `--entry-events`，或场景 JSON 顶层的 "entry_events": {"enabled": true}
    #      （命令行显式开关优先）；"换谁"由 JSON 的 swap_with 决定，缺省是"前一位进驻"。
    #      做成开关而不是默认行为：它是布局初始化语义，而非每小时速率。
    if args.entry_events or world.entry_events.enabled:
        for ev in apply_entry_events(world, enabled=True):
            print(f"[进驻事件] {ev.source()}　{ev.detail}", file=sys.stderr)

    # 1.6) 闲置入宿（可选）：把"没在上班、也不在宿舍、心情还没满"的干员安排进宿舍——
    #      宿舍有空位就直接放进去，没空位就与宿舍里心情已满的那位互换（那位换出来闲置）。
    #      开关来源：命令行 `--idle-to-dorm` 或 JSON 顶层的 "idle_to_dorm": {"enabled": true}。
    #      ⚠️ 这里只在"当前这一份布局"上结算一次（多班轮换要逐班结算，见 ui.schedule）。
    if args.idle_to_dorm or getattr(world.idle_to_dorm, "enabled", False):
        for ev in apply_idle_to_dorm(world, enabled=True):
            print(f"[闲置入宿] {ev.source()}　{ev.detail}", file=sys.stderr)

    # 2) 按模式测算并组装 JSON
    if args.mode == "base":
        period = to_decimal(args.period if args.period > 0 else 0.0)
        result = evaluate_base(world, period)
        payload = base_result_to_dict(result, period)
        fname = f"base_{_timestamp()}.json"
    else:  # single
        if not args.target:
            print("错误：--mode single 需要 --target 指定目标干员", file=sys.stderr)
            return 1
        if world.get_operator(args.target) is None:
            print(f"错误：布局中不存在干员「{args.target}」", file=sys.stderr)
            return 1
        # --period 缺省 0（即仅看当前状态）；演示场景与自定义场景一致，
        # 需要推进时间时显式给 --period（如 --demo --target 泡泡 --period 8）。
        period = to_decimal(args.period if args.period > 0 else 0.0)
        result = evaluate(world, args.target, period)
        payload = mood_result_to_dict(result, period)
        fname = f"single_{args.target}_{_timestamp()}.json"

    # 3) 输出 JSON 到标准输出
    print(to_json_string(payload))

    # 4) 可选：写入 JSON 文件（默认不写，需 --json-file）
    if args.json_file:
        path = os.path.join(args.out_dir, fname)
        written = dump_json(payload, path)
        print(f"# 已生成结果文件：{written}", file=sys.stderr)

    # 5) 可选：single 模式打印心情流水账（why 这个速率）
    if args.explain and args.mode == "single":
        print(result.ledger.explain(), file=sys.stderr)

    # 6) 可选：single 模式附轨迹
    if args.trace and args.mode == "single":
        from mood_soc import simulate
        from mood_soc.report import render_trajectory
        print(render_trajectory(simulate(world, args.target, period, step=Decimal("0.1"))), file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

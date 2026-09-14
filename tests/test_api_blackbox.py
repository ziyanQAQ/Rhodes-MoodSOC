"""tests/test_api_blackbox.py —— 黑盒测试：通过公开 API 断言"输入 → 输出"。

只 import `mood_soc` 顶层公开 API 与 `mood_soc.output`，
不 import 任何内部模块（battery / rules / config / skills / models / report），
不测试内部结构。输入 = 场景 JSON + 目标 + 时段，输出 = 结果 JSON（dict）。

运行：.venv/Scripts/python.exe -m unittest tests.test_api_blackbox -v
"""
from __future__ import annotations

import unittest
from decimal import Decimal

from mood_soc import (apply_entry_events, build_base_layout, evaluate, evaluate_base,
                       simulate, time_to_mood)
from mood_soc.config import (FacilityType, WORK_FACILITIES, facility_max_count,
                             facility_slots)
from mood_soc.output import base_result_to_dict, mood_result_to_dict
from mood_soc.ledger import Bucket
from mood_soc.rules import (compute_consumption, compute_net_rate, compute_recovery,
                            mood_ledger, remaining_work_hours)
from mood_soc.variables import collect_variables, mood_drop

FULL_CC = ["路人1", "路人2", "路人3", "路人4", "路人5"]


def scenario(*facilities):
    """把若干设施 dict 组装成场景 JSON 结构。"""
    return {"facilities": list(facilities)}


class TestSingleMode(unittest.TestCase):
    def test_working_basic(self):
        """办公室单人、时段 0：心情 24，sustain_hours = 24（还能工作 24h）。"""
        world = build_base_layout(scenario(
            {"type": "办公室", "level": 3, "operators": ["某人"]},
        ))
        d = mood_result_to_dict(evaluate(world, "某人", Decimal("0")), Decimal("0"))
        self.assertEqual(d["mode"], "single")
        self.assertEqual(d["operator"], "某人")
        self.assertEqual(d["facility"], "办公室")
        self.assertEqual(d["mood"], 24)
        self.assertEqual(d["sustain_hours"], 24)

    def test_working_period_reduction(self):
        """泡泡（满中枢 + L3 制造 3 人）净 0.4/h：8h 后剩 20.8，sustain_hours=52。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 1, "operators": FULL_CC},
            {"type": "制造站", "level": 3, "operators": ["泡泡", "路人甲", "路人乙"]},
        ))
        d = mood_result_to_dict(evaluate(world, "泡泡", Decimal("8")), Decimal("8"))
        self.assertAlmostEqual(d["mood"], 20.8, places=6)      # 24 - 0.4*8
        self.assertEqual(d["sustain_hours"], 52)              # 20.8 / 0.4

    def test_working_period_affects_sustain(self):
        """同一工作干员，时段不同 → 时段结束后还能继续工作的时间不同。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 1, "operators": FULL_CC},
        ))
        d16 = mood_result_to_dict(evaluate(world, "路人1", Decimal("16")), Decimal("16"))
        d20 = mood_result_to_dict(evaluate(world, "路人1", Decimal("20")), Decimal("20"))
        self.assertEqual(d16["mood"], 12)        # 24 - 0.75*16
        self.assertEqual(d16["sustain_hours"], 16)
        self.assertEqual(d20["mood"], 9)         # 24 - 0.75*20
        self.assertEqual(d20["sustain_hours"], 12)

    def test_dorm_recovery(self):
        """宿舍干员：sustain_hours = 恢复到满心情（24）所需时长（其余干员心情无限）。

        菲亚梅塔在宿舍，心情 10，恢复速率 2/h → (24-10)/2 = 7。
        """
        world = build_base_layout(scenario(
            {"type": "宿舍", "level": 5, "operators": [{"name": "菲亚梅塔", "mood": "10"}]},
        ))
        d = mood_result_to_dict(evaluate(world, "菲亚梅塔", Decimal("0")), Decimal("0"))
        self.assertEqual(d["mood"], 10)
        self.assertEqual(d["sustain_hours"], 7)   # (24 - 10) / 2


class TestBaseMode(unittest.TestCase):
    def test_sustainability(self):
        """布局可维持时长 = 所有干员到红脸时长的最小值；瓶颈 = 最先红脸者。

        工作干员 sustain_hours 一致 = layout_sustain_hours；
        宿舍干员若能在临界时间前恢复满，则 sustain_hours = 恢复满所需时间。
        """
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 1, "operators": FULL_CC},
            {"type": "制造站", "level": 3, "operators": ["泡泡", "路人甲", "路人乙"]},
            {"type": "宿舍", "level": 5, "operators": [{"name": "菲亚梅塔", "mood": "10"}]},
        ))
        d = base_result_to_dict(evaluate_base(world))
        self.assertEqual(d["mode"], "base")
        self.assertEqual(d["layout_sustain_hours"], 32)
        self.assertEqual(d["bottleneck"], "路人1")
        self.assertEqual(len(d["operators"]), 9)

        by_name = {o["name"]: o for o in d["operators"]}
        # 工作干员 sustain_hours 一致 = 32；宿舍干员 (24-10)/2=7 提前恢复满
        self.assertEqual(by_name["路人1"]["sustain_hours"], 32)
        self.assertEqual(by_name["泡泡"]["sustain_hours"], 32)
        self.assertEqual(by_name["菲亚梅塔"]["sustain_hours"], 7)
        # mood_at_end（到达 layout_sustain_hours 时）：瓶颈=0，泡泡=11.2，宿舍=24
        self.assertEqual(by_name["路人1"]["mood_at_end"], 0)
        self.assertAlmostEqual(by_name["泡泡"]["mood_at_end"], 11.2, places=6)
        self.assertEqual(by_name["菲亚梅塔"]["mood_at_end"], 24)

    def test_dorm_cannot_recover_in_time(self):
        """宿舍干员若在临界时间前恢复不满，则 sustain_hours 维持 = layout_sustain_hours。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 1, "operators": FULL_CC},
            {"type": "办公室", "level": 3, "operators": [{"name": "斥罪", "mood": "5"}]},
            {"type": "宿舍", "level": 5, "operators": [{"name": "菲亚梅塔", "mood": "10"}]},
        ))
        d = base_result_to_dict(evaluate_base(world))
        self.assertEqual(d["layout_sustain_hours"], 4)   # 斥罪 5/1.25=4 最先红脸
        self.assertEqual(d["bottleneck"], "斥罪")
        by_name = {o["name"]: o for o in d["operators"]}
        self.assertEqual(by_name["斥罪"]["sustain_hours"], 4)
        self.assertEqual(by_name["菲亚梅塔"]["sustain_hours"], 4)   # 恢复满需 7h > 4h，维持 4
        self.assertEqual(by_name["菲亚梅塔"]["mood_at_end"], 18)    # 10 + 2*4

    def test_already_red(self):
        """已有干员红脸（mood=0）→ 布局可维持时长为 0，瓶颈即该干员。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 1, "operators": FULL_CC},
            {"type": "办公室", "level": 3, "operators": [{"name": "红脸者", "mood": "0"}]},
        ))
        d = base_result_to_dict(evaluate_base(world))
        self.assertEqual(d["layout_sustain_hours"], 0)
        self.assertEqual(d["bottleneck"], "红脸者")
        by_name = {o["name"]: o for o in d["operators"]}
        self.assertEqual(by_name["红脸者"]["mood_at_end"], 0)   # 已红脸
        self.assertEqual(by_name["路人1"]["mood_at_end"], 24)   # 0 时刻，未变化

    def test_all_resting(self):
        """全员在宿舍 → 布局可一直维持：layout_sustain_hours / sustain_hours / mood_at_end 均为 null。"""
        world = build_base_layout(scenario(
            {"type": "宿舍", "level": 5, "operators": ["菲亚梅塔"]},
        ))
        d = base_result_to_dict(evaluate_base(world))
        self.assertIsNone(d["layout_sustain_hours"])
        self.assertIsNone(d["bottleneck"])
        self.assertIsNone(d["operators"][0]["sustain_hours"])
        self.assertIsNone(d["operators"][0]["mood_at_end"])


class TestTimeToMood(unittest.TestCase):
    def test_time_to_mood(self):
        """给定干员 + 目标心情，直接算出所需时间（工作下降 / 宿舍上升）。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 1, "operators": FULL_CC},
            {"type": "制造站", "level": 3, "operators": ["泡泡", "路人甲", "路人乙"]},
            {"type": "宿舍", "level": 5, "operators": [{"name": "菲亚梅塔", "mood": "10"}]},
        ))
        # 工作：泡泡 24 → 18 需 15h（(24-18)/0.4）
        self.assertEqual(time_to_mood(world, "泡泡", Decimal("18")), Decimal("15"))
        # 工作：路人甲 24 → 11 需 20h（13/0.65）
        self.assertEqual(time_to_mood(world, "路人甲", Decimal("11")), Decimal("20"))
        # 宿舍：菲亚梅塔 10 → 18 需 4h（(18-10)/2）
        self.assertEqual(time_to_mood(world, "菲亚梅塔", Decimal("18")), Decimal("4"))


class Test巫恋裁缝(unittest.TestCase):
    def test_caifeng_alpha_self_only(self):
        """巫恋·裁缝·α（self_consume）只对自己减耗 0.25；低语（facility_consume）对**全体含自身**加耗 0.25。

        口径依据：官方原文「…同时**全体**心情每小时消耗+0.25」→ 含自身（用户已拍板，
        见 documents/05-技能分类大纲.md；原 room_others「不含自身」口径作废）。

        贸易站 3 人基准：1 - 0.1(设施) - 0.25(中枢) = 0.65。
        - 火哨"暖场"（self -0.1）→ 0.65 - 0.1 = 0.55；再受低语 +0.25 → 0.80。
        - 路人乙：0.65 + 0.25 = 0.90。
        - 巫恋自身：低语 +0.25（含自身）、裁缝α self -0.25 → 0.65 + 0.25 - 0.25 = 0.65。
        """
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 1, "operators": FULL_CC},
            {"type": "贸易站", "level": 3, "operators": ["巫恋", "火哨", "路人乙"]},
        ))
        d_wl = mood_result_to_dict(evaluate(world, "巫恋", Decimal("0")), Decimal("0"))
        d_hs = mood_result_to_dict(evaluate(world, "火哨", Decimal("0")), Decimal("0"))
        d_lr = mood_result_to_dict(evaluate(world, "路人乙", Decimal("0")), Decimal("0"))
        self.assertAlmostEqual(d_wl["net_rate"], 0.65, places=6)  # 低语含自身 +0.25，裁缝α自身 -0.25
        self.assertAlmostEqual(d_hs["net_rate"], 0.8, places=6)   # 暖场自身 -0.1 + 低语 +0.25
        self.assertAlmostEqual(d_lr["net_rate"], 0.9, places=6)   # 低语 +0.25


class Test跨设施回复规则(unittest.TestCase):
    """轴 F3「跨干员取最高」与 M02c「玛恩纳扩散」。

    两条规则都来自官方术语表 gamedata_const.json → termDescriptionDict：
      · cc.c.sui2_1「特殊比较规则」：公事公办/孤光共照/巴别塔之帜 提供的 room2 恢复值取最高生效；
      · cc.c.skill「部分技能」：玛恩纳在中枢时，这 15 条中枢回复技能扩散到 room2。
    """

    @staticmethod
    def _recovery(cc_ops, facility_type, target="路人M"):
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 1, "operators": cc_ops},
            {"type": facility_type, "level": 3, "operators": [target]},
        ))
        fac = world.facility_of(target)
        return compute_recovery(world, world.get_operator(target), fac)

    def test_room2_take_max_not_sum(self):
        """三条 room2 回复技能同在中枢 → 取最高 0.1，而不是求和 0.25。"""
        self.assertEqual(self._recovery(["重岳"], "制造站"), Decimal("0.05"))
        self.assertEqual(self._recovery(["维什戴尔"], "制造站"), Decimal("0.1"))
        # 玛恩纳单独：公事公办只覆盖 room1，制造站(room2 但非 room1)不生效
        self.assertEqual(self._recovery(["玛恩纳"], "制造站"), Decimal("0.05"))
        # 三者同在中枢：取最高 0.1（维什戴尔）+ 玛恩纳扩散来的独善其身 0.05
        self.assertEqual(self._recovery(["玛恩纳", "重岳", "维什戴尔"], "制造站"), Decimal("0.15"))
        # 发电站属 room1：公事公办 0.1 参与取最高 → 仍 0.1 + 扩散 0.05
        self.assertEqual(self._recovery(["玛恩纳", "重岳", "维什戴尔"], "发电站"), Decimal("0.15"))

    def test_mlynar_spread_to_room2(self):
        """玛恩纳「公事公办」把白名单中枢回复技能扩散到 room2（其他设施）。"""
        # 无玛恩纳：陈的德才兼备只回中枢内干员，制造站拿不到
        self.assertEqual(self._recovery(["陈"], "制造站"), Decimal("0"))
        # 有玛恩纳：独善其身（玛恩纳自身的中枢技能，白名单内）扩散到制造站
        self.assertEqual(self._recovery(["玛恩纳", "陈"], "制造站"), Decimal("0.05"))
        # 发电站（room1）：公事公办 0.1 + 扩散来的独善其身 0.05
        self.assertEqual(self._recovery(["玛恩纳", "陈"], "发电站"), Decimal("0.15"))

    def test_spread_requires_provider_not_red_face(self):
        """扩散提供者红脸（心情<=0）时不再扩散。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 1,
             "operators": [{"name": "玛恩纳", "mood": 0}, "陈"]},
            {"type": "制造站", "level": 3, "operators": ["路人M"]},
        ))
        fac = world.facility_of("路人M")
        self.assertEqual(compute_recovery(world, world.get_operator("路人M"), fac), Decimal("0"))


class Test精英化判断(unittest.TestCase):
    """精英化门槛 + β 替换 α：技能是否生效取决于干员 elite/level。"""

    def test_elite_gating_unlock(self):
        """巫恋"低语"精英2解锁：elite<2 时不对他人加耗，elite=2 时才生效。"""
        def net_of_other(poly_elite):
            world = build_base_layout(scenario(
                {"type": "控制中枢", "level": 1, "operators": FULL_CC},
                {"type": "贸易站", "level": 3,
                 "operators": [{"name": "巫恋", "elite": poly_elite}, "火哨", "路人乙"]},
            ))
            return mood_result_to_dict(evaluate(world, "路人乙", Decimal("0")), Decimal("0"))["net_rate"]
        # 路人乙基准 0.65；巫恋低语未解锁时不变，精英2解锁后 +0.25
        self.assertEqual(net_of_other(0), 0.65)
        self.assertEqual(net_of_other(1), 0.65)
        self.assertAlmostEqual(net_of_other(2), 0.9, places=6)

    def test_elite_enhance_replaces_alpha(self):
        """火神"工匠精神"：α(-0.15 初始) → β(-0.25 精英2提升)，精英2后 β 替换 α 不叠加。"""
        def net_of_huoshen(elite):
            world = build_base_layout(scenario(
                {"type": "控制中枢", "level": 1, "operators": FULL_CC},
                {"type": "制造站", "level": 3,
                 "operators": [{"name": "火神", "elite": elite}, "路人甲", "路人乙"]},
            ))
            return mood_result_to_dict(evaluate(world, "火神", Decimal("0")), Decimal("0"))["net_rate"]
        # 制造站 3 人基准 0.65；火神 α -0.15 → 0.5；精英2后 β -0.25（替换 α，非叠加）→ 0.4
        self.assertAlmostEqual(net_of_huoshen(0), 0.5, places=6)
        self.assertAlmostEqual(net_of_huoshen(1), 0.5, places=6)
        self.assertAlmostEqual(net_of_huoshen(2), 0.4, places=6)

    def test_level_gating(self):
        """等级30解锁（三星机械）：杜林"嗜睡"需等级30，level<30 不生效。"""
        def net_of_dulin(level):
            world = build_base_layout(scenario(
                {"type": "控制中枢", "level": 1, "operators": FULL_CC},
                {"type": "制造站", "level": 3,
                 "operators": [{"name": "杜林", "level": level}, "路人甲", "路人乙"]},
            ))
            return mood_result_to_dict(evaluate(world, "杜林", Decimal("0")), Decimal("0"))["net_rate"]
        base = net_of_dulin(1)   # 杜林心情技能需等级30解锁，level=1 时无自身增减
        self.assertEqual(base, 0.65)
        # level=30 时解锁"嗜睡"（dorm 类，制造站内不生效），故制造站内 net 仍为基准
        self.assertEqual(net_of_dulin(30), 0.65)


class Test阵营联动(unittest.TestCase):
    """因其他阵营/干员存在而改变心情消耗/回复的技能。"""

    def test_faction_count_recovery(self):
        """陈"德才兼备"：中枢内每有1个龙门近卫局干员，中枢全体回复+0.05。"""
        def net_chen(cc_ops):
            world = build_base_layout(scenario(
                {"type": "控制中枢", "level": 1, "operators": cc_ops},
            ))
            return mood_result_to_dict(evaluate(world, "陈", Decimal("0")), Decimal("0"))["net_rate"]
        # 中枢 5 人基准 1-0.25=0.75；陈+星熊+诗怀雅 3 龙门 → 回复 0.05*3=0.15 → 0.60
        self.assertAlmostEqual(net_chen(["陈", "星熊", "诗怀雅", "路人1", "路人2"]), 0.6, places=6)
        # 只有陈 1 个龙门 → 回复 0.05 → 0.70
        self.assertAlmostEqual(net_chen(["陈", "路人1", "路人2", "路人3", "路人4"]), 0.7, places=6)

    def test_coop_with_operator_facility(self):
        """德克萨斯"恩怨"：与拉普兰德同驻贸易站时自身 +0.3；不同驻则无增减。"""
        def net_dex(with_la):
            ops = ["德克萨斯", "拉普兰德", "路人乙"] if with_la else ["德克萨斯", "路人甲", "路人乙"]
            world = build_base_layout(scenario(
                {"type": "控制中枢", "level": 1, "operators": FULL_CC},
                {"type": "贸易站", "level": 3, "operators": ops},
            ))
            return mood_result_to_dict(evaluate(world, "德克萨斯", Decimal("0")), Decimal("0"))["net_rate"]
        self.assertAlmostEqual(net_dex(True), 0.95, places=6)   # 0.65 + 0.3
        self.assertAlmostEqual(net_dex(False), 0.65, places=6)

    def test_coop_with_faction_cc(self):
        """摆渡人"英雄的骄傲"：与**萨尔贡**干员同驻中枢时自身 +0.02。

        ⚠️ 口径修正（2026-09 重构）：阵营表改由上游 `cc.g.*` 自动生成。
        旧手工表把「米诺斯」6 人（埃拉托/帕拉斯/铸铁/断罪者/火神/摆渡人）误标为「萨尔贡」，
        本用例原先拿**火神**当"萨尔贡"同驻者，等于把错误固化。
        上游：`cc.g.sargon` 萨尔贡 22 人（泡泡、燧石、蜜蜡、异客、狮蝎…），
              `cc.g.minos` 米诺斯 6 人（含 火神 与 摆渡人自己）。
        """
        def net_baiduren(companion):
            cc = ["摆渡人", companion, "路人1", "路人2", "路人3"]
            world = build_base_layout(scenario(
                {"type": "控制中枢", "level": 1, "operators": cc},
            ))
            return mood_result_to_dict(evaluate(world, "摆渡人", Decimal("0")), Decimal("0"))["net_rate"]

        # 中枢基准 0.75；同驻者是萨尔贡（蜜蜡）→ 自身 +0.02
        self.assertAlmostEqual(net_baiduren("蜜蜡"), 0.77, places=6)
        # 同驻者是米诺斯（火神）→ 不触发（这正是旧口径的错误所在）
        self.assertAlmostEqual(net_baiduren("火神"), 0.75, places=6)
        # 同驻者无阵营（路人）→ 不触发
        self.assertAlmostEqual(net_baiduren("路人1"), 0.75, places=6)


class TestTrace(unittest.TestCase):
    def test_simulate_trajectory(self):
        """simulate：输入场景+目标+时长 → 输出 (时间, 心情) 轨迹；红脸后钳位在 0。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 1, "operators": FULL_CC},
            {"type": "办公室", "level": 3, "operators": [{"name": "斥罪", "mood": "1"}]},
        ))
        traj = simulate(world, "斥罪", Decimal("4"), step=Decimal("0.05"))
        self.assertEqual(traj[0][0], Decimal("0"))
        self.assertEqual(traj[0][1], Decimal("1"))
        self.assertEqual(traj[-1][1], Decimal("0"))


class Test布局模型(unittest.TestCase):
    """P1 重构：多房间 / 容量 / 副手 / 活动室 / 按类型聚合 / 各设施基础消耗。

    上游依据（documents/06-数据来源.md）：
      building_data.json → rooms[roomType].maxCount 与 rooms[roomType].phases[lv].maxStationedNum
    """

    def test_multi_room_same_type(self):
        """同类型可以有多个房间，逐干员速率仍分别正确。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 5, "operators": FULL_CC},
            {"type": "制造站", "level": 3, "name": "制造站#1", "operators": ["泡泡"]},
            {"type": "制造站", "level": 3, "name": "制造站#2", "operators": ["火神"]},
        ))
        self.assertEqual(len(world.of_type(FacilityType.MANUFACTURING)), 2)
        self.assertEqual(world.count_of_type(FacilityType.MANUFACTURING), 2)
        # facility_of 能区分两个制造站里的人
        self.assertEqual(world.facility_of("泡泡").name, "制造站#1")
        self.assertEqual(world.facility_of("火神").name, "制造站#2")
        # 中枢满员减免 0.25 → 基准 0.75；
        # 泡泡「囤积者」-0.25 → 0.50；火神精英2 时 β(-0.25) 替换 α(-0.15) → 0.50
        self.assertEqual(compute_net_rate(world, "泡泡"), Decimal("0.50"))
        self.assertEqual(compute_net_rate(world, "火神"), Decimal("0.50"))

    def test_count_of_type_and_all_dormitories(self):
        """「每有 1 间发电站」与「所有宿舍」所需的多房间聚合能力。"""
        world = build_base_layout(scenario(
            {"type": "发电站", "level": 3, "operators": ["路人1"]},
            {"type": "发电站", "level": 3, "operators": ["路人2"]},
            {"type": "发电站", "level": 3, "operators": ["路人3"], "enabled": False},
            {"type": "宿舍", "level": 5, "operators": ["刺玫"]},
            {"type": "宿舍", "level": 5, "operators": ["车尔尼"]},
        ))
        # 三间发电站但有一间未启用 → 只计 2 间
        self.assertEqual(world.count_of_type(FacilityType.POWER), 2)
        self.assertEqual(world.count_of_type(FacilityType.POWER, only_enabled=False), 3)
        self.assertEqual(len(world.all_dormitories()), 2)
        self.assertEqual(len(world.working_operators(WORK_FACILITIES)), 2)

    def test_all_dormitories_buff_applies_to_every_dorm(self):
        """「所有宿舍内所有干员」类技能要覆盖每一个宿舍（焰影苇草「领袖」）。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 5, "operators": ["焰影苇草", "路人1", "路人2", "路人3", "路人4"]},
            {"type": "宿舍", "level": 5, "operators": ["路人甲"]},
            {"type": "宿舍", "level": 5, "operators": ["路人乙"]},
        ))
        for name in ("路人甲", "路人乙"):
            rec = compute_recovery(world, world.get_operator(name), world.facility_of(name))
            # 两个宿舍都要吃到领袖：宿舍 Lv5 满氛围基础 4.0 + 领袖 0.05
            self.assertEqual(rec, Decimal("4.05"))

    def test_capacity_and_validate(self):
        """容量按上游 `maxStationedNum` 推导；validate() 报房间数/容量越界（不抛异常）。"""
        self.assertEqual(facility_slots(FacilityType.CONTROL_CENTER, 1), 1)
        self.assertEqual(facility_slots(FacilityType.CONTROL_CENTER, 5), 5)
        self.assertEqual(facility_slots(FacilityType.MANUFACTURING, 3), 3)
        self.assertEqual(facility_slots(FacilityType.DORMITORY, 5), 5)
        self.assertEqual(facility_slots(FacilityType.PRIVATE, 1), 0)
        self.assertEqual(facility_max_count(FacilityType.POWER), 3)

        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 1, "operators": FULL_CC},          # Lv1 容量 1，塞了 5 人
            *[{"type": "发电站", "level": 3, "operators": [f"路人{i}"]} for i in range(4)],  # 上限 3 间
        ))
        issues = world.validate()
        self.assertTrue(any("控制中枢" in i and "容量" in i for i in issues), issues)
        self.assertTrue(any("发电站" in i and "上限" in i for i in issues), issues)
        with self.assertRaises(ValueError):
            build_base_layout({"facilities": [
                {"type": "发电站", "level": 3, "operators": ["路人1"]},
                {"type": "发电站", "level": 3, "operators": ["路人2"]},
                {"type": "发电站", "level": 3, "operators": ["路人3"]},
                {"type": "发电站", "level": 3, "operators": ["路人4"]},
            ]}, validate=True)

    def test_deputy_not_counted_and_not_draining(self):
        """副手：不占进驻位、不参与心情消耗，但可被查到；默认不进"基建内每有 1 名干员"。"""
        world = build_base_layout(scenario(
            {"type": "制造站", "level": 3, "operators": ["泡泡"], "deputies": ["火神"]},
        ))
        self.assertEqual([o.name for o in world.all_operators()], ["泡泡"])
        self.assertEqual([o.name for o in world.all_deputies()], ["火神"])
        self.assertIsNotNone(world.get_operator("火神"))          # 查得到
        # 未命名设施用类型标签作为显示名
        self.assertEqual(world.facility_of("火神").display_name, "制造站")
        self.assertEqual([o.name for o in world.base_operators()], ["泡泡"])
        self.assertEqual([o.name for o in world.base_operators(include_deputies=True)], ["泡泡", "火神"])

    def test_activity_room_excluded_by_default(self):
        """活动室：FacilityType.PRIVATE 存在，其使用者默认被「基建内每有 1 名干员」排除。"""
        world = build_base_layout(scenario(
            {"type": "制造站", "level": 3, "operators": ["泡泡"]},
            {"type": "活动室", "level": 1, "operators": ["路人A"]},
        ))
        self.assertEqual([o.name for o in world.base_operators()], ["泡泡"])
        self.assertIn("路人A", [o.name for o in world.base_operators(include_activity_room=True)])

    def test_facility_base_consumption(self):
        """各设施基础消耗：加工站 0（按次消耗）、训练室 1.0（待确认口径）、活动室 0、宿舍 0。"""
        def consumption(facility_type, name="路人X"):
            world = build_base_layout(scenario(
                {"type": "控制中枢", "level": 5, "operators": FULL_CC},
                {"type": facility_type, "level": 3, "operators": [name]},
            ))
            fac = world.facility_of(name)
            return compute_consumption(world, world.get_operator(name), fac)

        self.assertEqual(consumption("制造站"), Decimal("0.75"))
        self.assertEqual(consumption("加工站"), Decimal("0"))     # 旧实现错误地给 0.75
        self.assertEqual(consumption("宿舍"), Decimal("0"))
        self.assertEqual(consumption("活动室"), Decimal("0"))
        # 训练室：基础 1.0 - 中枢满员减免 0.25 = 0.75（口径待确认，见 config 注释）
        self.assertEqual(consumption("训练室"), Decimal("0.75"))


class Test心情流水账(unittest.TestCase):
    """P2 记录系统：mood_ledger() 逐条给出"谁 → 哪条技能 → 作用于谁 → 值 → 按哪条 F 轴规则合成"。

    这是"让内部完全理解干员心情机制"的可验证落点：
    以前只能断言一个 net_rate 数字，现在可以断言**构成**。
    """

    def test_ledger_matches_net_rate(self):
        """流水账算出来的净速率必须与 compute_net_rate 完全一致（唯一计算路径）。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 5, "operators": FULL_CC},
            {"type": "制造站", "level": 3, "operators": ["泡泡", "黍", "路人甲"]},
            {"type": "宿舍", "level": 5, "operators": ["刺玫"]},
        ))
        for name in ("泡泡", "黍", "路人甲", "刺玫"):
            self.assertEqual(mood_ledger(world, name).net_rate(),
                             compute_net_rate(world, name), name)

    def test_ledger_records_source_of_every_contribution(self):
        """每条贡献都要能回答"谁、哪条技能、哪个模板"。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 5, "operators": FULL_CC},
            {"type": "制造站", "level": 3, "operators": ["泡泡"]},
        ))
        lg = mood_ledger(world, "泡泡")
        consumes = lg.of(Bucket.CONSUME)
        # 基础消耗 + 中枢减免 + 泡泡自身「囤积者」
        self.assertTrue(any(c.template == "BASE" and c.value == Decimal("1") for c in consumes))
        self.assertTrue(any(c.label == "控制中枢全局减免" for c in consumes))
        own = [c for c in consumes if c.owner == "泡泡" and c.skill_name == "囤积者"]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0].template, "M07a")
        self.assertEqual(own[0].value, Decimal("-0.25"))
        self.assertEqual(lg.total(Bucket.CONSUME), Decimal("0.50"))
        # explain() 文本要含关键来源
        text = lg.explain()
        self.assertIn("囤积者", text)
        self.assertIn("净速率", text)

    def test_ledger_marks_cross_owner_max_loser(self):
        """轴 F3 跨干员取最高：三条 room2 回复同在中枢，解释里要标出"被更高者覆盖"。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 5,
             "operators": ["玛恩纳", "重岳", "维什戴尔", "路人1", "路人2"]},
            {"type": "制造站", "level": 3, "operators": ["路人M"]},
        ))
        lg = mood_ledger(world, "路人M")
        rec = lg.of(Bucket.RECOVER)
        grouped = [c for c in rec if c.max_group == "room2_recover"]
        self.assertTrue(len(grouped) >= 2, [c.skill_name for c in grouped])
        self.assertIn("被更高者覆盖", lg.explain())
        # 重岳 0.05 被 维什戴尔 0.1 覆盖；另加玛恩纳扩散来的「独善其身」0.05
        self.assertEqual(lg.total(Bucket.RECOVER), Decimal("0.15"))

    def test_ledger_eliminator_zeroes_self_consume(self):
        """消除类（槐琥「团队精神」）：把目标干员「自身技能」整组归零，解释里标注。"""
        def consume(with_waaifu):
            ops = ["槐琥", "阿罗玛", "路人甲"] if with_waaifu else ["阿罗玛", "路人甲", "路人乙"]
            world = build_base_layout(scenario(
                {"type": "控制中枢", "level": 5, "operators": FULL_CC},
                {"type": "制造站", "level": 3, "operators": ops},
            ))
            return mood_ledger(world, "阿罗玛")

        lg = consume(True)
        self.assertEqual(lg.total(Bucket.CONSUME), Decimal("0.65"))   # 自身 +0.25 被消除
        self.assertIn("整组归零", lg.explain())
        # 无槐琥时阿罗玛自身 +0.25 生效
        self.assertEqual(consume(False).total(Bucket.CONSUME), Decimal("0.90"))

    def test_ledger_exclusive_short_circuit(self):
        """独占（菲亚梅塔「自律」）：流水账里只剩它自己，宿舍基础回复被清空。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 5, "operators": ["焰影苇草", "路人1", "路人2", "路人3", "路人4"]},
            {"type": "宿舍", "level": 5, "operators": ["菲亚梅塔"]},
        ))
        lg = mood_ledger(world, "菲亚梅塔")
        rec = lg.of(Bucket.RECOVER)
        self.assertEqual(len(rec), 1)
        self.assertTrue(rec[0].exclusive)
        self.assertEqual(lg.total(Bucket.RECOVER), Decimal("2.0"))
        # 基础回复这一组必须不存在（"宿舍基础回复"字样只出现在独占条目的说明文字里）
        self.assertFalse([c for c in rec if c.group == "dorm_base"])

    def test_ledger_pool_allocation(self):
        """池分配（冰酿「小酌怡情」）：0.8 总额平摊给心情未满成员，流水账标出人数。"""
        world = build_base_layout(scenario(
            {"type": "宿舍", "level": 5,
             "operators": [{"name": "冰酿", "mood": "10"},
                           {"name": "路人甲", "mood": "10"},
                           {"name": "路人乙", "mood": "24"}]},   # 满心情者不参与分配
        ))
        lg = mood_ledger(world, "路人甲")
        pool = [c for c in lg.of(Bucket.RECOVER) if c.pool_share]
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0].pool_share, 2)          # 冰酿 + 路人甲（路人乙满心情）
        self.assertEqual(pool[0].value, Decimal("0.4"))
        self.assertIn("池分配", lg.explain())

    def test_ledger_serializable(self):
        """流水账可转 dict（供工具链消费）。"""
        world = build_base_layout(scenario(
            {"type": "制造站", "level": 3, "operators": ["泡泡"]},
        ))
        d = mood_ledger(world, "泡泡").to_dict()
        self.assertEqual(d["operator"], "泡泡")
        self.assertIn("consume", d)
        self.assertIn("items", d["consume"])
        self.assertTrue(all("value" in i and "bucket" in i for i in d["consume"]["items"]))
        self.assertEqual(d["net_rate"], str(mood_ledger(world, "泡泡").net_rate()))


class Test变量账本(unittest.TestCase):
    """P3 变量：技能之间的"中间货币"（人间烟火 / 热情值 / 无声共鸣）。

    这三个是**真正会影响心情**的变量（官方术语表 `cc.bd*` 共 26 种，其余只影响产出）。
    重构前它们完全没有载体，6 条消费子句只能 `partial_mode=hold`（不生效）。
    """

    def test_jianhuo_feeds_chongyue(self):
        """人间烟火：重岳「知我为我」按岁干员产出 → 「孤光共照」每 20 点额外 +0.05。"""
        def recovery(cc_ops):
            world = build_base_layout(scenario(
                {"type": "控制中枢", "level": 5, "operators": cc_ops},
                {"type": "制造站", "level": 3, "operators": ["路人M"]},
            ))
            return collect_variables(world).get("人间烟火"), \
                mood_ledger(world, "路人M").total(Bucket.RECOVER)

        # 仅重岳：宿舍/活动室以外的岁干员 1 名 → 人间烟火 5，不足 20 → 只有基础 0.05
        self.assertEqual(recovery(["重岳"]), (Decimal("5"), Decimal("0.05")))
        # 岁 ×3 → 15，仍不足 20
        self.assertEqual(recovery(["重岳", "年", "黍"])[1], Decimal("0.05"))
        # 夕（心情 10 < 12）+15、令（心情 20 > 12，山河远阔）+15、岁 ×4 → +20，共 50
        # → 孤光共照「每 20 点」2 份 × 0.05 = 0.10，叠加基础 0.05 → 0.15
        jh, rec = recovery([{"name": "重岳"}, {"name": "夕", "mood": "10"},
                            {"name": "令", "mood": "20"}, {"name": "年"}])
        self.assertEqual(jh, Decimal("50"))
        self.assertEqual(rec, Decimal("0.15"))

    def test_jianhuo_producer_requires_mood_condition(self):
        """人间烟火产出者带条件：夕「不以物喜」只在自身心情 < 12 时产出 +15。"""
        def jianhuo(xi_mood):
            world = build_base_layout(scenario(
                {"type": "控制中枢", "level": 5,
                 "operators": [{"name": "夕", "mood": xi_mood}, "路人1", "路人2", "路人3", "路人4"]},
            ))
            return collect_variables(world).get("人间烟火")

        self.assertEqual(jianhuo("11"), Decimal("15"))   # < 12 → 产出
        self.assertEqual(jianhuo("12"), Decimal("0"))    # 等于 12 → 不产出（「处于12以下」）
        self.assertEqual(jianhuo("20"), Decimal("0"))

    def test_passion_gate_for_fengchuan(self):
        """热情值：丰川祥子「生活的重压」在热情值 ≥ 40 时自身 +0.05。"""
        def net(cc_ops):
            world = build_base_layout(scenario(
                {"type": "控制中枢", "level": 5, "operators": cc_ops},
            ))
            return collect_variables(world).get("热情值"), compute_net_rate(world, "丰川祥子")

        # 无产出者 → 热情值 0，不触发
        self.assertEqual(net(["丰川祥子"])[0], Decimal("0"))
        # 若叶睦 +20 不足 40 → 不触发
        self.assertEqual(net(["丰川祥子", "若叶睦"]), (Decimal("20"), Decimal("0.900")))
        # +祐天寺若麦 +10 +八幡海铃 +10 = 40 → 触发 +0.05
        v, r = net(["丰川祥子", "若叶睦", "祐天寺若麦", "八幡海铃"])
        self.assertEqual(v, Decimal("40"))
        self.assertEqual(r, Decimal("0.850"))

    def test_passion_per_unit_for_yeyemu(self):
        """热情值「每有 8 点」倍率：若叶睦「演技的怪物」自身消耗随热情值增加。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 5,
             "operators": ["若叶睦", "祐天寺若麦", "八幡海铃", "路人1", "路人2"]},
        ))
        v = collect_variables(world)
        self.assertEqual(v.get("热情值"), Decimal("40"))
        consume = mood_ledger(world, "若叶睦").total(Bucket.CONSUME)
        # 基准 1 − 0.25(中枢满员) + 0.01×(40/8 = 5 份) = 0.80
        self.assertEqual(consume, Decimal("0.80"))

    def test_wusheng_gongming_from_dorm(self):
        """无声共鸣：塑心「无声共鸣」按宿舍人数产出，「无词颂歌」每 5 点 +0.01。"""
        def wsg(dorm_ops):
            world = build_base_layout(scenario(
                {"type": "宿舍", "level": 5, "operators": dorm_ops},
            ))
            return collect_variables(world).get("无声共鸣")

        self.assertEqual(wsg(["塑心"]), Decimal("1"))
        self.assertEqual(wsg(["塑心", "路人甲", "路人乙", "路人丙", "路人丁"]) , Decimal("5"))
        # 5 点 → 1 份 × 0.01，叠加在「无词颂歌」基础 0.2 之上（同种取最高 → 取 0.2）
        world = build_base_layout(scenario(
            {"type": "宿舍", "level": 5,
             "operators": ["塑心", "路人甲", "路人乙", "路人丙", "路人丁"]},
        ))
        lg = mood_ledger(world, "塑心")
        bonus = [c for c in lg.of(Bucket.RECOVER) if c.value == Decimal("0.01")]
        self.assertEqual(len(bonus), 1)
        self.assertIn("无声共鸣 = 5", bonus[0].detail)

    def test_mood_drop_variable(self):
        """心情落差 = 心情上限 − 当前心情（上游术语 cc.bd.costdrop）。"""
        world = build_base_layout(scenario(
            {"type": "制造站", "level": 3, "operators": [{"name": "铅踝", "mood": "9"}]},
        ))
        self.assertEqual(mood_drop(world.get_operator("铅踝")), Decimal("15"))

    def test_ledger_exposes_variables(self):
        """流水账要能打印变量快照（产出者 + 数量）。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 5,
             "operators": ["重岳", "年", "黍", "路人1", "路人2"]},
            {"type": "制造站", "level": 3, "operators": ["路人M"]},
        ))
        text = mood_ledger(world, "路人M").explain()
        self.assertIn("变量", text)
        self.assertIn("人间烟火", text)
        self.assertIn("知我为我", text)


class Test可数条件与替换链(unittest.TestCase):
    """P4a：把「每有 N 个什么」这类**可以从布局数出来**的条件纳入模型。

    这批子句（12 条）此前一律 `partial_mode=hold`（不生效）。启用它们时发现两个真问题：

    1. **「同种效果取最高」的粒度错了**。上游原文写的是
       「…额外 +N 恢复效果（**叠加后的最终值**同种效果取最高）」
       → 同一技能的「基础 + 每有 N 额外」两个分句应**先求和**，再与其他技能取最高。
       旧实现按**单个分句**取 max，会把基础分句丢掉（死前必做清单 Lv5 → 0.15 而非 0.25）。

    2. **β 替换链把「同一技能的分句」当成了链上环节**，导致额外分句"替换"掉自己的基础分句
       （实测 6 例：响石 0.15 / 铎铃 万里传书 -0.1 / 刺玫 0.15 / 波卜 0.2 / 流明 0.1 / 隐德来希 0.1 全丢）。
       修法：替换链的单位是 **skill_id**，不是 `skill_id#clause`。
    """

    @staticmethod
    def _recover(dorm_ops, extra=None, target=None):
        facs = [{"type": "宿舍", "level": 5, "operators": dorm_ops}]
        facs.extend(extra or [])
        world = build_base_layout(scenario(*facs))
        first = dorm_ops[0]
        name = target or (first["name"] if isinstance(first, dict) else first)
        return mood_ledger(world, name).total(Bucket.RECOVER)

    def test_base_plus_per_unit_bonus_sums_then_takes_max(self):
        """死前必做清单 = max( 0.15 + 0.02×宿舍等级, 其它群体回复 )，不是 max(0.15, 0.02×L)。"""
        # 宿舍 Lv5：基础 4.0 + (0.15 + 0.02×5) = 4.25
        self.assertEqual(self._recover(["响石"]), Decimal("4.25"))
        # 宿舍 Lv3：基础 3.0 + (0.15 + 0.02×3) = 3.21
        world = build_base_layout(scenario(
            {"type": "宿舍", "level": 3, "operators": ["响石"]}))
        self.assertEqual(mood_ledger(world, "响石").total(Bucket.RECOVER), Decimal("3.21"))

    def test_dorm_unfull_basis(self):
        """倾谈者 = 0.2 + 0.01×该宿舍心情未满人数。"""
        self.assertEqual(self._recover([{"name": "波卜", "mood": "10"},
                                        {"name": "路人甲", "mood": "10"}]), Decimal("4.22"))
        # 全员满心情 → 未满人数 0
        self.assertEqual(self._recover([{"name": "波卜", "mood": "24"},
                                        {"name": "路人甲", "mood": "24"}]), Decimal("4.20"))

    def test_dorm_others_basis(self):
        """「独处」= 0.7 + 0.05×该宿舍内其他干员数（不含自身）。"""
        self.assertEqual(self._recover(["芳汀"]), Decimal("4.70"))          # 0 名其他
        self.assertEqual(self._recover([{"name": "芳汀", "mood": "10"},
                                        {"name": "路人甲", "mood": "10"},
                                        {"name": "路人乙", "mood": "10"}]), Decimal("4.80"))

    def test_recruit_slot_basis(self):
        """招募位 = 人力办公室等级：寻同路人 0.15 + 0.05×等级。"""
        office = {"type": "办公室", "level": 3, "operators": ["路人O"]}
        self.assertEqual(self._recover(["斥罪"], [office]), Decimal("4.30"))
        # 无办公室 → 招募位 0 → 只剩基础 0.15
        self.assertEqual(self._recover(["斥罪"]), Decimal("4.15"))

    def test_power_count_basis_and_beta_replaces_alpha(self):
        """柔和微光：β(0.15) 替换 α(0.1)，再 + 0.05×发电站数。"""
        powers = [{"type": "发电站", "level": 3, "operators": ["路人P"]},
                  {"type": "发电站", "level": 3, "operators": ["路人Q"]}]
        # 流明默认精英2 → 用 β：4.0 + 0.15 + 0.05×2 = 4.25
        self.assertEqual(self._recover(["流明"], powers), Decimal("4.25"))
        # 未精英（elite=0）→ 用 α：4.0 + 0.1 + 0.05×2 = 4.20
        self.assertEqual(self._recover([{"name": "流明", "elite": 0}], powers), Decimal("4.20"))
        # 无发电站 → 只剩基础项
        self.assertEqual(self._recover(["流明"]), Decimal("4.15"))

    def test_abyssal_non_dorm_basis(self):
        """潮汐守望：每有 1 个进驻在宿舍以外设施的**其他**深海猎人，自身消耗 +0.5。

        ⚠️ 口径：「其他」= 排除歌蕾蒂娅自身。上游原文两条分支
        「每有 1 个…宿舍以外的设施，则自身心情每小时消耗 +0.5；**反之**则自身心情每小时恢复 +0.5」
        ——若把她自己也算进去，则「每有」恒 ≥1、「反之」永不触发（游戏不会写死分支），
        故取「其他深海猎人」口径；该分支行为在本用例中一并断言。
        """
        def ledger(extra_facs):
            world = build_base_layout(scenario(
                {"type": "控制中枢", "level": 5,
                 "operators": ["歌蕾蒂娅", "路人2", "路人3", "路人4", "路人5"]},
                *extra_facs))
            return mood_ledger(world, "歌蕾蒂娅")

        # 只有歌蕾蒂娅自己 → 无其他深海猎人在宿舍外 → 「反之」自身 +0.5 恢复
        lg = ledger([])
        self.assertEqual(lg.total(Bucket.CONSUME), Decimal("0.75"))
        self.assertEqual(lg.total(Bucket.RECOVER), Decimal("0.55"))   # 0.5 反之 + 0.05 集群狩猎

        # 幽灵鲨在制造站 → ×1 → 消耗 +0.5（「反之」分支不再生效）
        lg = ledger([{"type": "制造站", "level": 3, "operators": ["幽灵鲨"]}])
        self.assertEqual(lg.total(Bucket.CONSUME), Decimal("1.25"))
        self.assertEqual(lg.total(Bucket.RECOVER), Decimal("0.05"))

        # 幽灵鲨在宿舍且满心情 → 反之 +0.5，且「为满心情」额外 +0.5
        lg = ledger([{"type": "宿舍", "level": 5, "operators": [{"name": "幽灵鲨", "mood": "24"}]}])
        self.assertEqual(lg.total(Bucket.CONSUME), Decimal("0.75"))
        self.assertEqual(lg.total(Bucket.RECOVER), Decimal("1.05"))

    def test_unconditional_mood_clause_with_truncated_condition(self):
        """挑大梁：上游原文里「每有 1 名黑钢国际干员」修饰的是**生产力**，心情 -0.15 是无条件的。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 5, "operators": FULL_CC},
            {"type": "制造站", "level": 3, "operators": ["杏仁"]},
        ))
        lg = mood_ledger(world, "杏仁")
        rows = [c for c in lg.of(Bucket.CONSUME) if c.skill_name == "挑大梁"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].value, Decimal("-0.15"))
        self.assertEqual(rows[0].detail, "")      # 无变量、无基准 → 无条件

    def test_recruit_slot_on_self_consume(self):
        """救援队·保证体力：每个招募位使自身心情消耗 -0.1（办公室 Lv3 → -0.3）。"""
        world = build_base_layout(scenario(
            {"type": "办公室", "level": 3, "operators": ["雪绒"]},
        ))
        lg = mood_ledger(world, "雪绒")
        self.assertEqual(lg.total(Bucket.CONSUME), Decimal("0.70"))
        row = [c for c in lg.of(Bucket.CONSUME) if c.skill_name == "救援队·保证体力"][0]
        self.assertIn("招募位", row.detail)
        self.assertIn("× 3", row.detail)

    def test_replaces_chain_no_longer_eats_base_clause(self):
        """回归：β 替换链按 skill_id 计算，不再把同一技能的基础分句"替换"掉。

        铎铃：跋山涉水(α) → 精英2 万里传书(β)。万里传书的
        「基础 -0.1 + 每 10 点人间烟火 -0.02」两个分句都必须保留。
        """
        world = build_base_layout(scenario(
            {"type": "贸易站", "level": 3, "operators": ["铎铃", "路人甲", "路人乙"]},
        ))
        lg = mood_ledger(world, "铎铃")
        rows = [c for c in lg.of(Bucket.CONSUME) if c.skill_name == "万里传书"]
        self.assertEqual(len(rows), 2, [c.detail for c in rows])
        # 设施减免 0.1 + 万里传书基础 0.1 → 1 - 0.1 - 0.1 = 0.80
        self.assertEqual(lg.total(Bucket.CONSUME), Decimal("0.80"))
        # α 应已被替换，不再出现
        self.assertFalse([c for c in lg.of(Bucket.CONSUME) if c.skill_name == "跋山涉水"])


class Test分支条件P4b(unittest.TestCase):
    """P4b：把上游**完整原文**里可建模的分支条件纳入模型。

    这批子句的本地 condition 列是被截断的（`如果` / `反之` / `多心情子句`），
    按 documents/06-数据来源.md 回上游取全文后才看出真实语义。
    过程中还暴露了 3 个「条件从未被求值」的循环（见各用例注释）。
    """

    def test_reception_alone_condition(self):
        """会客室「只有自身处于工作状态时」→ 心情消耗 +N（双面间谍 +2）。

        上游原文：「进驻会客室时，如果会客室内只有自身处于工作状态时，
        线索搜集速度提升 50%，**心情每小时消耗 +2**」——心情这一半只在独自一人时生效。
        """
        def consume(ops):
            world = build_base_layout(scenario(
                {"type": "会客室", "level": 3, "operators": ops}))
            return mood_ledger(world, "和弦").total(Bucket.CONSUME)

        self.assertEqual(consume(["和弦"]), Decimal("3"))            # 1 + 2
        self.assertEqual(consume(["和弦", "路人甲"]), Decimal("1"))   # 不触发

    def test_mutual_half_self_only_eliminate(self):
        """若叶睦「互为半身」：与丰川祥子同中枢时消除**自身**心情消耗影响。

        ⚠️ 与槐琥「团队精神」/ 令「杯莫停」不同——那两条消除的是**同设施所有干员**，
        互为半身只消除自己（上游原文「消除**自身**心情消耗的影响」）。
        消除类循环原先既不求值条件、又对 self_only 无区分，本用例一并锁死。
        """
        def consume(target):
            world = build_base_layout(scenario(
                {"type": "控制中枢", "level": 5,
                 "operators": ["若叶睦", "丰川祥子", "祐天寺若麦", "八幡海铃"]}))
            return mood_ledger(world, target)

        # 热情值 40 → 若叶睦「演技的怪物」自身消耗 = 0.01×(40/8=5) = 0.05，被互为半身消除
        lg = consume("若叶睦")
        self.assertEqual(lg.total(Bucket.CONSUME), Decimal("0.800"))   # 1 - 0.20 + 0
        self.assertTrue(any(c.zeroes_group == "self_consume" for c in lg.of(Bucket.CONSUME)))
        # 丰川祥子的「生活的重压」不受影响（互为半身不是"消除所有人"）
        lg = consume("丰川祥子")
        self.assertEqual(lg.total(Bucket.CONSUME), Decimal("0.850"))   # 1 - 0.20 + 0.05

    def test_mutual_half_requires_xiangzi(self):
        """互为半身是共事条件：没有丰川祥子时不触发（条件必须被求值）。"""
        world = build_base_layout(scenario(
            {"type": "控制中枢", "level": 5,
             "operators": ["若叶睦", "祐天寺若麦", "八幡海铃", "路人1"]}))
        lg = mood_ledger(world, "若叶睦")
        self.assertFalse(any(c.zeroes_group for c in lg.of(Bucket.CONSUME)))
        self.assertEqual(lg.total(Bucket.CONSUME), Decimal("0.850"))   # 1 - 0.20 + 0.05

    def test_laoixiao_faction_bonus(self):
        """资深料理人（森西）：基础 0.15 + 「如果目标是莱欧斯小队」额外 0.15。

        这是一个**按目标筛选的群体回复条件**，此前 `DORM_GROUP` 循环从不求值条件，
        会对所有人无条件生效。
        """
        facs = [{"type": "宿舍", "level": 5, "operators": ["森西", "玛露西尔", "路人甲"]}]
        world = build_base_layout(scenario(*facs))
        # 玛露西尔 属莱欧斯小队 → 0.15 + 0.15
        self.assertEqual(mood_ledger(world, "玛露西尔").total(Bucket.RECOVER), Decimal("4.30"))
        # 路人甲 不属该小队 → 只有基础 0.15
        self.assertEqual(mood_ledger(world, "路人甲").total(Bucket.RECOVER), Decimal("4.15"))


class Test定向加成M09(unittest.TestCase):
    """M09 单体回复的「如果目标是 X，则恢复效果额外 +0.45」。

    上游原文（`building_data.json` → `buffs["dorm_rec_single_P[000]"]` 等 6 条）：
        「进驻宿舍时，使该宿舍内除自身以外心情未满的某个干员每小时恢复 +0.55
          （同种效果取最高），**如果目标是 <点名干员 / 阵营>，则恢复效果额外 +0.45**」
    即：基础 0.55 与定向加成 0.45 属于**同一条技能的两个分句**，
    要先求和（1.00）再与其它单体回复技能取最高。
    """

    @staticmethod
    def _dorm(members, level=5):
        return build_base_layout(scenario(
            {"type": "宿舍", "level": level,
             "operators": [{"name": m, "mood": 10} for m in members]}))

    @staticmethod
    def _single(world, who):
        return sum((c.value for c in mood_ledger(world, who).of(Bucket.RECOVER)
                    if c.group == "dorm_single"), Decimal("0"))

    def test_target_named_operator(self):
        """点名具体干员：毒剂师之友（深靛）→ 蓝毒 +0.45；沏茶（黑）→ 锡兰 +0.45。"""
        self.assertEqual(self._single(self._dorm(["深靛", "蓝毒"]), "蓝毒"), Decimal("1.00"))
        self.assertEqual(self._single(self._dorm(["深靛", "路人"]), "路人"), Decimal("0.55"))
        self.assertEqual(self._single(self._dorm(["黑", "锡兰"]), "锡兰"), Decimal("1.00"))
        self.assertEqual(self._single(self._dorm(["黑", "路人"]), "路人"), Decimal("0.55"))

    def test_target_faction(self):
        """点名阵营：降生于冰寒（寒檀）→ 萨米 +0.45；圣城趣事通（新约能天使）→ 拉特兰 +0.45。"""
        self.assertEqual(self._single(self._dorm(["寒檀", "提丰"]), "提丰"), Decimal("1.00"))
        self.assertEqual(self._single(self._dorm(["寒檀", "路人"]), "路人"), Decimal("0.55"))
        self.assertEqual(self._single(self._dorm(["新约能天使", "蕾缪安"]), "蕾缪安"), Decimal("1.00"))

    def test_target_multi_tag_union(self):
        """狩猎好帮手（罗德岛隐秘队）同时认 `cc.tag.mh` 与 `cc.tag.mh2`（并集）。

        上游原文：「如果目标是 <怪物猎人小队>成员**和<泡影国狩猎小队>**，则额外 +0.45」
        ——两个标签任一命中即可。该干员自己就是泡影国成员，但「除自身以外」故不能自指。
        """
        self.assertEqual(self._single(self._dorm(["罗德岛隐秘队", "焰狐龙梓兰"]), "焰狐龙梓兰"),
                         Decimal("1.00"))
        self.assertEqual(self._single(self._dorm(["罗德岛隐秘队", "火龙S黑角"]), "火龙S黑角"),
                         Decimal("1.00"))
        self.assertEqual(self._single(self._dorm(["罗德岛隐秘队", "路人"]), "路人"),
                         Decimal("0.55"))

    def test_same_kind_max_compares_skills_not_clauses(self):
        """「同种效果取最高」的比较单位是**技能**：0.55+0.45 要先求和，再赢过 0.50。

        陪跑：临光「使徒」= 0.50（`dorm_rec_single&oneself_030`）。
        若按单条分句取 max，深靛会只剩 0.55 > 0.50 仍然赢——看不出差别；
        故再加一条**只让加成生效**的对照：命中时合计 1.00、未命中时 0.55，
        两者都必须在与 0.50 的比较中胜出，且合计值随目标身份变化。
        """
        hit = self._dorm(["深靛", "临光", "蓝毒"])
        miss = self._dorm(["深靛", "临光", "路人"])
        self.assertEqual(self._single(hit, "蓝毒"), Decimal("1.00"))     # max(0.55+0.45, 0.50)
        self.assertEqual(self._single(miss, "路人"), Decimal("0.55"))    # max(0.55, 0.50)

    def test_bonus_provider_must_be_active(self):
        """红脸（mood≤0）的提供者技能失效——定向加成同样不生效。"""
        world = build_base_layout(scenario(
            {"type": "宿舍", "level": 5,
             "operators": [{"name": "深靛", "mood": 0}, {"name": "蓝毒", "mood": 10}]}))
        self.assertEqual(self._single(world, "蓝毒"), Decimal("0"))


class Test元修正M17(unittest.TestCase):
    """M17 元修正：**一名干员强化另一名干员的效果**（摩根「头号陪练」）。

    上游原文（`building_data.json` → `buffs["dorm_rec_toone[000]"]`）：
        「进驻宿舍时，**推进之王**对该宿舍中**格拉斯哥帮**干员恢复效果额外 **+0.3**」
    ——摩根自己不提供回复，而是把**推进之王已经算出的那条贡献**顶上去。
    实现上：把增量补成**同组同技能**的额外贡献，交给 `SAME_KIND_MAX` 先并入该技能小计、
    再与其他技能取最高（若记成独立技能，会被"同种取最高"当成竞争者而整个丢掉）。

    推进之王「狮心王」= 宿舍群体 +0.2；格拉斯哥帮 = 推进之王 / 摩根 / 达格达 / 因陀罗。
    """

    @staticmethod
    def _dorm(members, level=5):
        return build_base_layout(scenario(
            {"type": "宿舍", "level": level,
             "operators": [{"name": m, "mood": 10} for m in members]}))

    @staticmethod
    def _recover(world, who):
        """宿舍回复合计（走 MoodLedger 的轴 F 合成，L5 满氛围基础回复 = 4.0）。"""
        return mood_ledger(world, who).total(Bucket.RECOVER)

    def test_boost_same_faction(self):
        """同帮成员拿到 0.2 + 0.3 = 0.5（含推进之王自身——她也在格拉斯哥帮里）。"""
        world = self._dorm(["摩根", "推进之王", "达格达"])
        for who in ("达格达", "摩根", "推进之王"):
            self.assertEqual(self._recover(world, who), Decimal("4.50"), who)

    def test_boost_only_for_target_faction(self):
        """非同帮成员只拿基础 0.2。"""
        world = self._dorm(["摩根", "推进之王", "路人"])
        self.assertEqual(self._recover(world, "路人"), Decimal("4.20"))

    def test_boost_requires_modifier_holder(self):
        """摩根不在宿舍时没有强化。"""
        world = self._dorm(["推进之王", "达格达"])
        self.assertEqual(self._recover(world, "达格达"), Decimal("4.20"))

    def test_boost_requires_provider(self):
        """被点名强化的持有者（推进之王）不在宿舍时，强化无处附着。"""
        world = self._dorm(["摩根", "达格达"])
        self.assertEqual(self._recover(world, "达格达"), Decimal("4.00"))

    def test_boost_joins_provider_skill_subtotal(self):
        """强化必须**并入推进之王那条技能**参与"同种取最高"，而不是单列一条竞争项。

        陪跑：森西「资深料理人」0.15。达格达身上应是 4.0 + max(0.2+0.3, 0.15) = 4.5；
        若强化被当成独立技能，就会变成 4.0 + max(0.2, 0.15, 0.3) = 4.3。
        """
        world = self._dorm(["摩根", "推进之王", "森西", "达格达"])
        self.assertEqual(self._recover(world, "达格达"), Decimal("4.50"))


class Test进驻事件M15a(unittest.TestCase):
    """M15a 进驻事件：**患难之交**（菲亚梅塔）。

    上游原文（`building_data.json` → `buffs["dorm_exchangeAp[000]"]`）：
        「进驻宿舍时，**如果自身为满心情**，则与当前宿舍**前一位进驻**的干员互换心情」

    这类技能不是"每小时 ±N 点"，而是**进驻那一刻的状态跳变**，所以既不进速率流水账、
    也不进时间积分，而是由显式 API `apply_entry_events(world)`（CLI：`--entry-events`）结算。
    「前一位进驻」= `Facility.operators` 里排在触发者之前的那一位（该列表本来就是进驻顺序）。
    """

    @staticmethod
    def _world(pairs, ftype="宿舍"):
        return build_base_layout(scenario(
            {"type": ftype, "level": 5,
             "operators": [{"name": n, "mood": m} for n, m in pairs]}))

    @staticmethod
    def _moods(world):
        return [o.mood for o in world.facilities[0].operators]

    def test_swap_when_self_full(self):
        """满心情 → 与前一位互换（菲亚梅塔 24 换走对方的 6）。"""
        world = self._world([("路人", 6), ("菲亚梅塔", 24)])
        events = apply_entry_events(world)
        self.assertEqual(len(events), 1)
        self.assertEqual(self._moods(world), [Decimal("24"), Decimal("6")])

    def test_no_swap_when_not_full(self):
        """自身不满心情 → 不触发。"""
        world = self._world([("路人", 6), ("菲亚梅塔", 20)])
        self.assertEqual(apply_entry_events(world), [])
        self.assertEqual(self._moods(world), [Decimal("6"), Decimal("20")])

    def test_no_swap_without_previous_occupant(self):
        """她是宿舍里第一个进驻的（没有"前一位"）→ 不触发。"""
        world = self._world([("菲亚梅塔", 24), ("路人", 6)])
        self.assertEqual(apply_entry_events(world), [])
        self.assertEqual(self._moods(world), [Decimal("24"), Decimal("6")])

    def test_order_matters(self):
        """「前一位进驻」由布局的**顺序**决定——调换顺序换的是另一个人。"""
        world = self._world([("达格达", 3), ("路人", 6), ("菲亚梅塔", 24)])
        apply_entry_events(world)
        self.assertEqual(self._moods(world), [Decimal("3"), Decimal("24"), Decimal("6")])

    def test_idempotent(self):
        """互换后她不再是满心情 → 重复调用不会换回来。"""
        world = self._world([("路人", 6), ("菲亚梅塔", 24)])
        apply_entry_events(world)
        self.assertEqual(apply_entry_events(world), [])
        self.assertEqual(self._moods(world), [Decimal("24"), Decimal("6")])

    def test_only_in_dormitory(self):
        """技能限定宿舍：其它设施不结算。"""
        world = self._world([("路人", 6), ("菲亚梅塔", 24)], ftype="制造站")
        self.assertEqual(apply_entry_events(world), [])
        self.assertEqual(self._moods(world), [Decimal("6"), Decimal("24")])

    def test_not_applied_by_default(self):
        """`evaluate` **不**自动结算进驻事件——它是显式开关（CLI `--entry-events`）。"""
        world = self._world([("路人", 6), ("菲亚梅塔", 24)])
        self.assertEqual(evaluate(world, "菲亚梅塔", Decimal("0")).initial_mood, Decimal("24"))
        apply_entry_events(world)
        self.assertEqual(evaluate(world, "菲亚梅塔", Decimal("0")).initial_mood, Decimal("6"))


class Test训练室(unittest.TestCase):
    """训练室：设施级基础消耗 1.0/h + 9 条「心情每小时消耗+1」的自身消耗技能。

    口径来源（两处独立佐证）：
      - 需求文档 `心情消耗回复和工休时间.docx` 第 4 段：「干员工作时，在无额外心情加减的情况下，
        每小时的**基础消耗速率为 1 点心情**」——不区分设施，训练室同样适用；
      - 上游 `building_data.json` → `buffs`：9 条训练室 buff 原文均为「…时，心情每小时消耗 +1」
        （`train_cost&profession[140/320/340/350/360/380]`、`train_spd_bd[000]`、
        `train_spd_doubleProf3[100]`、`train_spd_power_down[000]`）。
    → 持有这些技能的干员在训练室里净消耗 **2.0/h**，没持有的仍是 **1.0/h**。
    """

    @staticmethod
    def _train(members):
        return build_base_layout(scenario(
            {"type": "训练室", "level": 3,
             "operators": [{"name": m} if isinstance(m, str) else m for m in members]}))

    def test_base_consumption_only(self):
        """没持有该类技能 → 只有 1.0/h 的设施基础消耗。"""
        world = self._train(["路人"])
        self.assertEqual(mood_ledger(world, "路人").total(Bucket.CONSUME), Decimal("1"))

    def test_self_consume_plus_one(self):
        """W「索然无味」= 设施 1.0 + 自身 +1.0 = 2.0/h；同设施路人不受影响。"""
        world = self._train(["W", "路人"])
        self.assertEqual(mood_ledger(world, "W").total(Bucket.CONSUME), Decimal("2"))
        self.assertEqual(mood_ledger(world, "路人").total(Bucket.CONSUME), Decimal("1"))

    def test_net_rate_and_work_hours(self):
        """净速率 2.0/h → 一管 24 点心情只能连续协助 12h。"""
        world = self._train(["W"])
        self.assertEqual(compute_net_rate(world, "W"), Decimal("2"))
        self.assertEqual(remaining_work_hours(world, "W"), Decimal("12"))

    def test_elite_gate(self):
        """「索然无味」是精英 2 解锁：未精英化时只有基础 1.0/h。"""
        world = self._train([{"name": "W", "elite": 1}])
        self.assertEqual(mood_ledger(world, "W").total(Bucket.CONSUME), Decimal("1"))

    def test_beta_replaces_alpha(self):
        """雷狼龙S空爆：「兴之所至·α」无心情副作用，精英 2 的 β 才 +1。"""
        alpha = self._train([{"name": "雷狼龙S空爆", "elite": 0}])
        beta = self._train([{"name": "雷狼龙S空爆", "elite": 2}])
        self.assertEqual(mood_ledger(alpha, "雷狼龙S空爆").total(Bucket.CONSUME), Decimal("1"))
        self.assertEqual(mood_ledger(beta, "雷狼龙S空爆").total(Bucket.CONSUME), Decimal("2"))

    def test_variable_skill_still_costs(self):
        """余「与人乐」：心情 +1 是无条件的（人间烟火只影响训练速度，不影响心情）。"""
        world = self._train(["余"])
        self.assertEqual(mood_ledger(world, "余").total(Bucket.CONSUME), Decimal("2"))

    def test_workshop_has_no_hourly_base(self):
        """对照：加工站是**按配方**消耗心情，没有每小时基础消耗（`base_consumption = 0`）。"""
        world = build_base_layout(scenario(
            {"type": "加工站", "level": 3, "operators": ["路人"]}))
        self.assertEqual(mood_ledger(world, "路人").total(Bucket.CONSUME), Decimal("0"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

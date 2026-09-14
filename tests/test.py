from __future__ import annotations

import unittest
from decimal import Decimal

from mood_soc import build_base_layout, evaluate, evaluate_base, simulate, time_to_mood
from mood_soc.output import base_result_to_dict, mood_result_to_dict

class Test(unittest.TestCase):
    def test(self):
        world = build_base_layout({
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
        })
        a = time_to_mood(world, "黍", 23)
        print(a)
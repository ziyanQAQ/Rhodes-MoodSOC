"""mood_soc/skill_templates.py —— 基建技能分类「六轴」与模板注册表。

本模块是**分类字典**（人读 + 机读都靠它），不含任何计算逻辑。

## 为什么要分类

游戏里 755 条基建 buff 的官方 `buffCategory` 只按「房间 × 产出维度」切
（`RECOVERY`/`OUTPUT`/`FUNCTION`），回答不了本项目最关心的问题：
**「这条技能算不算心情类、要不要进 `I = 消耗 - 回复`」**。
因此本项目另立一套正交分类：**六条轴**，任意一条技能 = 六轴上的一个点。

## 六条轴

| 轴 | 含义 | 取值 |
|---|---|---|
| A 建模地位 | 要不要算 | `ModelTier`：A1 心情速率 / A2 心情事件 / A3 心情当条件 / A4 非心情 |
| B 作用域   | 作用到哪个空间 | `Domain`：自身设施 / 同设施 / 跨设施 / 宿舍 / 全局规则 |
| C 作用对象 | 作用到谁 | `Target`：自身 / 同设施全体 / 同设施其他 / 宿舍全体·单体 / 条件筛选 / 全基建 |
| D 效果     | 改哪个量 | `Effect`：心情 12 族 / 产出 8 族 / 容量 / 概率 / 变量 / 规则修饰 |
| E 数值形态 | 参数长什么样 | `ValueShape`：定值 / ×等级 / ×设施数 / ×人数 / 阈值 / 池 / 集 / 倍率 |
| F 叠加规则 | 多来源怎么合 | `Stacking`：求和 / 同种取最高 / 跨干员取最高 / 互斥 / 替换 / 池 / 归零优先 |

**模板 = 真实数据里用到的 (B, C, D, E, F) 组合**，给稳定 ID（`M01`~`M16`、`X01`~`X11`）。
标签页里的「规则修饰」不单独成族（按用户决定拆进 **F 轴**）。

## 一条铁律：模板挂在 clause（子句）上

游戏一个 buff 常含多个子句，子句可以属于不同族。例：
`trade_cost&bd2[000]`（跋山涉水）= ①贸易站全体消耗 -0.1（`M05`）+ ②每 10 点人间烟火再 -0.01（`M05`+变量条件）。
因此 `resources/moods_skills.txt` 是 **clause 级**、每行带 `template_id`；
`resources/skills_registry.txt` 是 **buff 级**的覆盖台账（755 行，保证不漏）。

## 数据流

```
ArknightsGameData building_data.json ─┐
                                      ├─ scripts/classify_skills.py ─┬─→ resources/moods_skills.txt  (+template_id, params)
resources/moods_skills.txt ───────────┘                              └─→ resources/skills_registry.txt (755 行台账)
                                                                          │
                                            scripts/generate_skills_data.py ←┘（读 template_id/params，不再靠猜）
                                                                          ↓
                                                            mood_soc/skills_data.py → rules.py
```

新增干员/技能时：跑 `scripts/classify_skills.py`，任何**未命中模板**的 buff 会落进
`unclassified` 报表（校验断言为 0）——把「漏技能」从静默失败变成必报清单。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Tuple


# ----------------------------------------------------------------------------
# 轴 A：建模地位
# ----------------------------------------------------------------------------
class ModelTier(str, Enum):
    """这条技能要不要进心情模型。"""
    A1_RATE = "A1"      # 心情速率：每小时 ±心情，进 I = 消耗 - 回复
    A2_EVENT = "A2"     # 心情事件：影响心情但不是速率（消除/独占/互换/恢复一次）
    A3_COND = "A3"      # 心情当条件：读心情或心情落差，但效果不是心情
    A4_NONE = "A4"      # 非心情：产出/容量/概率/变量/特殊


# ----------------------------------------------------------------------------
# 轴 B：作用域
# ----------------------------------------------------------------------------
class Domain(str, Enum):
    SELF_FACILITY = "B0"      # 自身所在设施（控制中枢/制造站/贸易站/发电站/办公室/会客室/宿舍/加工站/训练室）
    SAME_FACILITY = "B1"      # 同设施内
    CROSS_FACILITY = "B2"     # 跨设施（room1 部分 / room2 其他 / room3 工作场所 / 全基建 / 按设施类型）
    DORM = "B3"               # 宿舍
    GLOBAL_RULE = "B4"        # 全局规则（消除/归零/取最高集）


# ----------------------------------------------------------------------------
# 轴 C：作用对象
# ----------------------------------------------------------------------------
class Target(str, Enum):
    SELF = "C1"                 # 自身
    SAME_ALL = "C2"             # 同设施全体（含自身）
    SAME_OTHERS = "C3"          # 同设施其他（不含自身）
    DORM_ALL = "C4a"            # 宿舍全体（可 exclude_self）
    DORM_ONE = "C4b"            # 宿舍单体（锁定一名）
    COND_FILTERED = "C5"        # 按条件筛选个体（阵营/标签/职业/指定干员/心情阈值）
    BASE_WIDE = "C6"            # 全基建干员


# ----------------------------------------------------------------------------
# 轴 D：效果
# ----------------------------------------------------------------------------
class Effect(str, Enum):
    # —— 心情（A1/A2）——
    MOOD_CONSUME = "D1"         # 心情消耗增减
    MOOD_RECOVER = "D2"         # 心情回复
    MOOD_ELIMINATE = "D3"       # 消除他人「自身心情消耗」的影响
    MOOD_EXCLUSIVE = "D4"       # 独占心情回复（拒绝其它来源）
    MOOD_EVENT = "D5"           # 心情事件（互换/恢复一次）
    # —— 产出（A4）——
    PRODUCE_MANU = "D20"        # 制造生产力
    CAPACITY = "D21"            # 仓库容量
    TRADE_ORDER = "D22"         # 贸易订单效率/上限/高品质概率
    POWER_DRONE = "D23"         # 发电效率 / 无人机充能
    CLUE = "D24"                # 会客室线索速度/倾向/必定
    HIRE = "D25"                # 人力办公室招募位/联络
    TRAIN = "D26"               # 训练室专精速度
    WORKSHOP = "D27"            # 加工站配方心情消耗/副产品概率
    # —— 变量与特殊 ——
    VARIABLE = "D30"            # 变量生产/消耗（人间烟火、感知信息、热情值… 见 VARIABLES）
    SPECIAL_ORDER = "D31"       # 特殊订单
    FACILITY_COUNT = "D32"      # 仅影响设施数量等元数据


# ----------------------------------------------------------------------------
# 轴 E：数值形态
# ----------------------------------------------------------------------------
class ValueShape(str, Enum):
    SCALAR = "E1"               # 标量定值（+0.05）
    PER_FACILITY_LEVEL = "E2"   # ×设施等级
    PER_FACILITY_COUNT = "E3"   # ×设施数量（每有 1 间发电站）
    PER_OPERATOR_COUNT = "E4"   # ×干员数量（每名阵营/职业干员）
    THRESHOLD = "E5"            # 阈值阶梯（心情 >12、落差 >12、变量 >=40）
    POOL = "E6"                 # 池：总额分配给若干成员
    SET_MAX = "E7"              # 集：一组来源内取最高
    RATIO = "E8"                # 倍率/百分比
    UNLOCK_CHAIN = "E9"         # 解锁阶梯（精英/等级，α→β 替换）


# ----------------------------------------------------------------------------
# 轴 F：叠加规则（原「规则修饰」族并入此轴）
# ----------------------------------------------------------------------------
class Stacking(str, Enum):
    SUM = "F1"                  # 求和（不同来源相加）
    SAME_KIND_MAX = "F2"        # 同种效果取最高（官方文本明写）
    CROSS_OWNER_MAX = "F3"      # 跨干员/跨来源取最高（中枢全局减免、room2 取最高集）
    MUTEX = "F4"                # 互斥并优先生效（自动化 vs 配合意识、天道酬勤）
    REPLACE = "F5"              # 替换（α→β、解锁覆盖）
    POOL = "F6"                 # 池分配
    ZERO_PRIORITY = "F7"        # 归零/清零优先（低语、自动化）


# ----------------------------------------------------------------------------
# 模板
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Template:
    """一个技能模板 = 六轴上的一个点 + 判定关键词 + 参数槽。"""
    id: str
    tier: ModelTier
    name: str
    domain: Domain
    target: Target
    effect: Effect
    value_shape: ValueShape
    stacking: Stacking
    modeled: bool = True                     # False = 只登记不建模
    params: Tuple[str, ...] = ()             # 该模板需要的参数名（填表时对着填）
    notes: str = ""


def _t(*args, **kw) -> Template:
    return Template(*args, **kw)


# —— M 族：心情类（已覆盖 242/242 个 clause，零未归类）——
M_TEMPLATES: Tuple[Template, ...] = (
    _t("M01", ModelTier.A1_RATE, "中枢→宿舍 群体回复", Domain.CROSS_FACILITY, Target.DORM_ALL,
       Effect.MOOD_RECOVER, ValueShape.SCALAR, Stacking.SAME_KIND_MAX,
       params=("value",), notes="领袖/战纹/巡心/羁绊相生/无言的慈爱（后者 per-count 精英干员）"),
    _t("M02a", ModelTier.A1_RATE, "中枢→room1 回复", Domain.CROSS_FACILITY, Target.BASE_WIDE,
       Effect.MOOD_RECOVER, ValueShape.SCALAR, Stacking.CROSS_OWNER_MAX,
       params=("value", "scope=room1"), notes="只作用「部分设施」= 发电/办公/会客"),
    _t("M02b", ModelTier.A1_RATE, "中枢→room2 回复", Domain.CROSS_FACILITY, Target.BASE_WIDE,
       Effect.MOOD_RECOVER, ValueShape.SCALAR, Stacking.CROSS_OWNER_MAX,
       params=("value", "scope=room2", "max_group=room2_recover"),
       notes="孤光共照/巴别塔之帜；官方术语 cc.c.sui2_1 规定三条之间取最高，不求和"),
    _t("M02c", ModelTier.A1_RATE, "中枢→room1 + 扩散白名单", Domain.CROSS_FACILITY, Target.BASE_WIDE,
       Effect.MOOD_RECOVER, ValueShape.SCALAR, Stacking.CROSS_OWNER_MAX,
       params=("value", "scope=room1", "spread=cc.c.skill"),
       notes="玛恩纳公事公办：把官方术语 cc.c.skill 的 15 条中枢回复技能扩散到 room2"),
    _t("M03", ModelTier.A1_RATE, "中枢内 全体回复", Domain.SELF_FACILITY, Target.SAME_ALL,
       Effect.MOOD_RECOVER, ValueShape.SCALAR, Stacking.SUM,
       params=("value", "per_count_faction?"), notes="最常见的中枢技能族，per-count 用 count_faction"),
    _t("M04", ModelTier.A1_RATE, "中枢内 指定干员共事", Domain.SELF_FACILITY, Target.COND_FILTERED,
       Effect.MOOD_RECOVER, ValueShape.SCALAR, Stacking.SUM,
       params=("value", "with_operator"), notes="魔王传承/未完的故事（与阿米娅同中枢）"),
    _t("M05", ModelTier.A1_RATE, "同设施 全体消耗增减", Domain.SAME_FACILITY, Target.SAME_ALL,
       Effect.MOOD_CONSUME, ValueShape.SCALAR, Stacking.SUM,
       params=("value", "include_self=true"),
       notes="含巫恋「低语」：官方原文「全体心情每小时消耗+0.25」= 含自身（用户确认）"),
    _t("M06", ModelTier.A1_RATE, "同设施 其他消耗增减", Domain.SAME_FACILITY, Target.SAME_OTHERS,
       Effect.MOOD_CONSUME, ValueShape.SCALAR, Stacking.SUM,
       params=("value", "include_self=false"),
       notes="⚠️ 现代数据已无使用者（低语改判 M05）；保留以便将来出现「其他干员」类技能"),
    _t("M07a", ModelTier.A1_RATE, "自身消耗增减", Domain.SELF_FACILITY, Target.SELF,
       Effect.MOOD_CONSUME, ValueShape.SCALAR, Stacking.SUM,
       params=("value",), notes="最大族（84 clause）；负=减耗"),
    _t("M07b", ModelTier.A1_RATE, "自身回复（非宿舍/加工站）", Domain.SELF_FACILITY, Target.SELF,
       Effect.MOOD_RECOVER, ValueShape.SCALAR, Stacking.SUM,
       params=("value", "condition?"), notes="潮汐守望：深海猎人在非宿舍设施则自身 +0.5"),
    _t("M08", ModelTier.A1_RATE, "宿舍 群体回复", Domain.DORM, Target.DORM_ALL,
       Effect.MOOD_RECOVER, ValueShape.SCALAR, Stacking.SAME_KIND_MAX,
       params=("value", "exclude_self=false"), notes="exclude_self=true 用于「除自身以外所有干员」"),
    _t("M09", ModelTier.A1_RATE, "宿舍 单体回复（锁一人）", Domain.DORM, Target.DORM_ONE,
       Effect.MOOD_RECOVER, ValueShape.SCALAR, Stacking.SAME_KIND_MAX,
       params=("value", "target_policy=minimum_unfull_exclude_provider", "per_target_bonus?"),
       notes="现已实现简化锁定策略；沏茶/烤肉大师等的 +0.45 是 per_target_bonus"),
    _t("M10", ModelTier.A1_RATE, "宿舍 自身回复", Domain.DORM, Target.SELF,
       Effect.MOOD_RECOVER, ValueShape.SCALAR, Stacking.SAME_KIND_MAX,
       params=("value",), notes="独处/幼狼情性等（+0.7 起）"),
    _t("M11", ModelTier.A1_RATE, "宿舍 池分配", Domain.DORM, Target.DORM_ALL,
       Effect.MOOD_RECOVER, ValueShape.POOL, Stacking.POOL,
       params=("total", "recipients=unfull"), notes="冰酿小酌怡情：0.8 总额平分给心情未满成员"),
    _t("M12", ModelTier.A1_RATE, "宿舍 定向/条件回复", Domain.DORM, Target.COND_FILTERED,
       Effect.MOOD_RECOVER, ValueShape.SCALAR, Stacking.SUM,
       params=("value", "condition"), notes="刺玫（心情<18）/净化呼吸（心情<20）/头号陪练（格拉斯哥帮）"),
    _t("M13", ModelTier.A2_EVENT, "消除自身消耗影响", Domain.GLOBAL_RULE, Target.SAME_ALL,
       Effect.MOOD_ELIMINATE, ValueShape.SCALAR, Stacking.ZERO_PRIORITY,
       params=("filter?"), notes="槐琥团队精神 / 令杯莫停（限岁）/ 若叶睦互为半身"),
    _t("M14", ModelTier.A2_EVENT, "独占回复", Domain.DORM, Target.SELF,
       Effect.MOOD_EXCLUSIVE, ValueShape.SCALAR, Stacking.ZERO_PRIORITY,
       params=("value",), notes="菲亚梅塔自律：+2 且拒绝其它一切来源（含宿舍基础回复）"),
    _t("M15a", ModelTier.A2_EVENT, "心情互换/顺序", Domain.DORM, Target.COND_FILTERED,
       Effect.MOOD_EVENT, ValueShape.POOL, Stacking.ZERO_PRIORITY,
       params=("swap_with=previous_occupant",), notes="菲亚梅塔患难之交（未实现）"),
    _t("M15b", ModelTier.A2_EVENT, "触发式恢复一次心情", Domain.SELF_FACILITY, Target.SELF,
       Effect.MOOD_EVENT, ValueShape.SCALAR, Stacking.ZERO_PRIORITY,
       params=("trigger", "amount=recipe_cost"), notes="棘刺爆炸艺术（加工站，未实现）"),
    _t("M16", ModelTier.A3_COND, "心情当条件（效果非心情）", Domain.SELF_FACILITY, Target.SELF,
       Effect.VARIABLE, ValueShape.THRESHOLD, Stacking.SUM, modeled=False,
       params=("mood_condition", "real_effect"), notes="令山河远阔/铅踝心情落差/絮雨追忆等 6 条"),
    _t("M17", ModelTier.A1_RATE, "强化他人恢复效果（元修正）", Domain.DORM, Target.COND_FILTERED,
       Effect.MOOD_RECOVER, ValueShape.SCALAR, Stacking.SUM, modeled=False,
       params=("provider", "target_filter", "value"),
       notes="摩根「头号陪练」：描述不含「心情」两字，但把推进之王对格拉斯哥帮的宿舍恢复效果 +0.3。"
             "这类「改别人的技能」的元修正技能是描述关键词检索的盲区，靠台账兜住"),
)

# —— X 族：非心情（登记不建模）——
X_TEMPLATES: Tuple[Template, ...] = (
    _t("X01", ModelTier.A4_NONE, "制造产出", Domain.SELF_FACILITY, Target.SAME_ALL,
       Effect.PRODUCE_MANU, ValueShape.RATIO, Stacking.SUM, modeled=False, params=("value",)),
    _t("X02", ModelTier.A4_NONE, "仓库容量", Domain.SELF_FACILITY, Target.SAME_ALL,
       Effect.CAPACITY, ValueShape.SCALAR, Stacking.SUM, modeled=False, params=("value",)),
    _t("X03", ModelTier.A4_NONE, "贸易订单", Domain.SELF_FACILITY, Target.SAME_ALL,
       Effect.TRADE_ORDER, ValueShape.RATIO, Stacking.SUM, modeled=False, params=("value",)),
    _t("X04", ModelTier.A4_NONE, "发电与无人机", Domain.SELF_FACILITY, Target.SAME_ALL,
       Effect.POWER_DRONE, ValueShape.SCALAR, Stacking.SUM, modeled=False, params=("value",)),
    _t("X05", ModelTier.A4_NONE, "会客室线索", Domain.SELF_FACILITY, Target.SAME_ALL,
       Effect.CLUE, ValueShape.RATIO, Stacking.SUM, modeled=False, params=("value",)),
    _t("X06", ModelTier.A4_NONE, "人力办公室招募", Domain.SELF_FACILITY, Target.SAME_ALL,
       Effect.HIRE, ValueShape.SCALAR, Stacking.SUM, modeled=False, params=("value",)),
    _t("X07", ModelTier.A4_NONE, "训练室专精", Domain.SELF_FACILITY, Target.SAME_ALL,
       Effect.TRAIN, ValueShape.RATIO, Stacking.SUM, modeled=False, params=("value",)),
    _t("X08", ModelTier.A4_NONE, "加工站配方与副产品", Domain.SELF_FACILITY, Target.SELF,
       Effect.WORKSHOP, ValueShape.SCALAR, Stacking.SUM, modeled=False,
       params=("value",), notes="配方心情消耗是「按次」机制，不进每小时积分"),
    _t("X09", ModelTier.A4_NONE, "变量生产/消耗", Domain.CROSS_FACILITY, Target.BASE_WIDE,
       Effect.VARIABLE, ValueShape.SCALAR, Stacking.SUM, modeled=False,
       params=("variable",), notes="技能间的中间货币，见 VARIABLES"),
    _t("X10", ModelTier.A4_NONE, "跨设施/设施计数条件", Domain.CROSS_FACILITY, Target.BASE_WIDE,
       Effect.FACILITY_COUNT, ValueShape.PER_FACILITY_COUNT, Stacking.SUM, modeled=False,
       params=("facility",)),
    _t("X11", ModelTier.A4_NONE, "特殊订单", Domain.SELF_FACILITY, Target.SAME_ALL,
       Effect.SPECIAL_ORDER, ValueShape.SCALAR, Stacking.ZERO_PRIORITY, modeled=False, params=()),
)

TEMPLATES: Dict[str, Template] = {t.id: t for t in M_TEMPLATES + X_TEMPLATES}

# 轴 F 的「规则修饰」取值来源：这些词出现在描述里时，只影响叠加方式，不影响效果族。
STACKING_KEYWORDS = {
    "同种效果取最高": Stacking.SAME_KIND_MAX,
    "取最高": Stacking.SAME_KIND_MAX,
    "特殊比较规则": Stacking.CROSS_OWNER_MAX,
    "归零": Stacking.ZERO_PRIORITY,
    "清零": Stacking.ZERO_PRIORITY,
    "优先生效": Stacking.MUTEX,
    "恒定": Stacking.REPLACE,
    "除以": Stacking.REPLACE,
}

# 官方术语 `cc.c.skill`「部分技能」——玛恩纳「公事公办」的扩散白名单（15 条）。
# 来源：zh_CN/gamedata/excel/gamedata_const.json → termDescriptionDict.cc.c.skill
SPREAD_SKILL_IDS: Tuple[str, ...] = (
    "control_mp_cost_000",   # 左膀右臂
    "control_mp_cost_001",   # S.W.E.E.P.
    "control_mp_cost_002",   # 零食网络
    "control_mp_cost_003",   # 清理协议
    "control_mp_cost_004",   # 替身
    "control_mp_cost_005",   # 必要责任
    "control_mp_cost_006",   # 护卫
    "control_mp_cost_007",   # 小小的领袖
    "control_mp_cost_008",   # 独善其身（玛恩纳自身）
    "control_mp_cost_009",   # 笑靥如春（冰酿）
    "control_mp_cost_010",   # 金盏花诗会
    "control_mp_cost_011",   # 捍卫之道
    "control_mp_cost_012",   # 博识生手
    "control_mp_cost_013",   # 点滴关照
    "control_mp_cost_014",   # 总工程师
)

# 官方术语 `cc.c.sui2_1`「特殊比较规则」涉及的三个技能（room2 回复取最高集）。
ROOM2_MAX_GROUP_SKILL_IDS: Tuple[str, ...] = (
    "control_mp_lonely_000",         # 玛恩纳 公事公办
    "control_mp_bd_cost_expand_000",  # 重岳 孤光共照
    "control_mp_expand_double_000",   # 维什戴尔 巴别塔之帜
)
ROOM2_MAX_GROUP = "room2_recover"

# 技能间的中间货币（官方术语表 cc.bd* / cc.bd_*），共 26 种。
VARIABLES: Tuple[str, ...] = (
    "念力", "意识实体", "徘徊旋律", "怅惘和声", "无词颂歌", "思维链环", "无声共鸣", "巫术结晶",
    "感知信息", "人间烟火", "情报储备", "乌萨斯特饮", "工程机器人", "记忆碎片", "梦境", "小节",
    "心情落差", "木天蓼", "可爱的艾露猫", "可靠的随从们", "魔物料理", "热情值",
    "丰富工作经验", "演技的怪物", "外势", "实地",
)

# 心情落差 = 心情上限 - 当前心情（轴 A3 的唯一来源）。
MOOD_DROP_VARIABLE = "心情落差"

# 默认 partial 处置（C 轴决策：模板确定 + 条件槽空 → 保留效果骨架）：
#   apply = 主效果无条件成立，缺的只是「额外加成/倍率」→ 按骨架生效
#   hold  = 主效果本身以缺失条件为前提 → 保留模板与骨架，但暂不生效
PARTIAL_APPLY = "apply"
PARTIAL_HOLD = "hold"

__all__ = [
    "ModelTier", "Domain", "Target", "Effect", "ValueShape", "Stacking",
    "Template", "TEMPLATES", "M_TEMPLATES", "X_TEMPLATES",
    "STACKING_KEYWORDS", "SPREAD_SKILL_IDS", "ROOM2_MAX_GROUP_SKILL_IDS", "ROOM2_MAX_GROUP",
    "VARIABLES", "MOOD_DROP_VARIABLE", "PARTIAL_APPLY", "PARTIAL_HOLD",
]

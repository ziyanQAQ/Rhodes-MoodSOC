"""mood_soc/rules.py —— 心情消耗 / 回复规则引擎（业务逻辑层）。

职责：给定"基建布局快照 + 目标干员"，算出该干员的：
    1. 心情消耗速率 consumption（点 / 时）
    2. 心情回复速率 recovery（点 / 时）
    3. 净速率 net = consumption - recovery（>0 心情下降，<0 心情上升）

以及工休比等衍生指标、以及"一段时间后剩余心情 / 还能工作多久"等查询。

本模块只负责"算速率"，不推进时间；时间推进交给 battery / simulator。
所有数值运算使用 decimal.Decimal，十进制精确、可复现。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .battery import INF, ZERO, ampere_hour_integration, to_decimal
from .config import (
    BASE_CONSUMPTION,
    FACILITY_LABELS,
    MOOD_MAX,
    FacilityType,
    cc_reduction,
    dormitory_recovery,
    facility_mood_reduction,
)
from .models import BaseLayout, BaseResult, Facility, MoodResult, Operator, OperatorResult
from .skills import SKILLS, SkillKind


@dataclass
class SkillContext:
    """技能判定所需的上下文快照。"""
    world: BaseLayout
    owner: Operator        # 技能持有者
    target: Operator       # 技能作用对象
    facility: Facility     # 目标所在设施


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------
def _skills_of(op: Operator, kind: SkillKind):
    """取干员持有的某类技能列表。"""
    return [SKILLS[sid] for sid in op.skill_ids if sid in SKILLS and SKILLS[sid].kind == kind]


def _active(op: Operator) -> bool:
    """干员是否处于"有技能"状态：红脸（心情<=0）时技能失效。"""
    return op.mood > ZERO


def _scope_ok(skill, facility: Facility, target: Operator) -> bool:
    """判断技能的设施范围与阵营条件是否命中。"""
    if skill.facility_types and facility.ftype not in skill.facility_types:
        return False
    if skill.target_trait and target.trait != skill.target_trait:
        return False
    return True


# ----------------------------------------------------------------------------
# 心情消耗
# ----------------------------------------------------------------------------
def compute_consumption(world: BaseLayout, op: Operator, facility: Facility) -> Decimal:
    """计算干员在工作设施内的心情消耗速率（点 / 时，结果 >= 0）。

    组成（与文档一致）：
      基础消耗 1
      - 设施基础减免 X（制造 / 贸易，按进驻人数）
      - 控制中枢全局减免（满员 0.25）
      ± 干员自身技能（self_consume）
      ± 同设施干员的设施级技能（facility_consume，含自身）
      - 中枢全局减免技能（cc_reduce，取最高）
      以及"消除类技能"会移除目标干员自身技能的正负影响。
    """
    if facility.ftype == FacilityType.DORMITORY:
        return ZERO   # 宿舍内不消耗心情

    total = BASE_CONSUMPTION

    # 1) 设施基础减免 X
    total -= facility_mood_reduction(facility.ftype, len(facility.operators))

    # 2) 控制中枢全局减免
    cc = world.control_center()
    total -= cc_reduction(len(cc.operators)) if cc else ZERO

    # 3) 干员自身技能（self_consume，正=加耗 / 负=减耗）
    self_delta = ZERO
    for skill in _skills_of(op, SkillKind.SELF_CONSUME):
        if _active(op) and _scope_ok(skill, facility, op):
            ctx = SkillContext(world, op, op, facility)
            if skill.condition is None or skill.condition(ctx):
                self_delta += skill.value

    # 4) 设施级技能（facility_consume）：同设施所有干员（含自身）对全体生效
    for other in facility.operators:
        for skill in _skills_of(other, SkillKind.FACILITY_CONSUME):
            if _active(other) and _scope_ok(skill, facility, op):
                ctx = SkillContext(world, other, op, facility)
                if skill.condition is None or skill.condition(ctx):
                    total += skill.value

    # 5) 中枢全局减免技能（cc_reduce，按干员求和后取最高）
    total -= _max_cc_reduction(world, op, facility)

    # 6) 消除类：移除目标干员"自身技能"的影响（正负均移除，设施 / 中枢影响不变）
    if _has_eliminator(world, op, facility):
        self_delta = ZERO

    total += self_delta
    return max(ZERO, total)   # 消耗不为负（负值由"回复"侧表达）


def _cc_reduction_from(world, owner: Operator, target: Operator, facility: Facility) -> Decimal:
    """某中枢干员提供的全局减免（对其持有的 cc_reduce 技能求和）。"""
    if not _active(owner):
        return ZERO
    s = ZERO
    for skill in _skills_of(owner, SkillKind.CC_REDUCE):
        if skill.facility_types and facility.ftype not in skill.facility_types:
            continue
        ctx = SkillContext(world, owner, target, facility)
        if skill.condition is not None and not skill.condition(ctx):
            continue
        s += skill.value
    return s


def _max_cc_reduction(world, target: Operator, facility: Facility) -> Decimal:
    """中枢全局减免技能：不同干员之间"取最高"。"""
    cc = world.control_center()
    if cc is None:
        return ZERO
    best = ZERO
    for owner in cc.operators:
        best = max(best, _cc_reduction_from(world, owner, target, facility))
    return best


def _has_eliminator(world, target: Operator, facility: Facility) -> bool:
    """是否存在能消除目标干员"自身心情消耗"影响的干员（槐琥 / 令等）。"""
    for other in facility.operators:
        if other is target:
            continue
        for skill in _skills_of(other, SkillKind.ELIMINATE_SELF):
            if skill.facility_types and facility.ftype not in skill.facility_types:
                continue
            if skill.target_trait and target.trait != skill.target_trait:
                continue
            if not _active(other):
                continue
            return True
    return False


# ----------------------------------------------------------------------------
# 心情回复
# ----------------------------------------------------------------------------
def compute_recovery(world: BaseLayout, op: Operator, facility: Facility) -> Decimal:
    """计算干员的心情回复速率（点 / 时）。"""
    if facility.ftype == FacilityType.DORMITORY:
        return _dorm_recovery(world, op, facility)
    return _work_recovery(world, op, facility)


def _work_recovery(world, op: Operator, facility: Facility) -> Decimal:
    """工作设施内的回复：来自中枢技能的回复（如玛恩纳）。"""
    cc = world.control_center()
    if cc is None:
        return ZERO
    total = ZERO
    for owner in cc.operators:
        if not _active(owner):
            continue
        for skill in _skills_of(owner, SkillKind.CC_RECOVER):
            if skill.facility_types and facility.ftype not in skill.facility_types:
                continue
            ctx = SkillContext(world, owner, op, facility)
            if skill.condition is not None and not skill.condition(ctx):
                continue
            total += skill.value
    return total


def _dorm_recovery(world, op: Operator, facility: Facility) -> Decimal:
    """宿舍内的回复：基础回复 + 各类干员回复技能。

    规则：不同类型（自身 / 群体 / 单体 / 定向）可叠加；同种类型取最高。
    菲亚梅塔与冰酿为特殊干员，单独处理。
    """
    # --- 特殊：菲亚梅塔，自身 +2 且不接受其它来源（含宿舍基础回复）---
    if any(sid == "菲亚梅塔-自身回复" for sid in op.skill_ids) and _active(op):
        return Decimal("2")

    # 基础回复（白字 + 绿字氛围部分）
    recovery = dormitory_recovery(facility.level, facility.atmosphere)

    # 自身回复（dorm_self，同种取最高）
    self_r = max(
        (s.value for s in _skills_of(op, SkillKind.DORM_SELF) if _active(op)),
        default=ZERO,
    )

    # 群体回复（dorm_group，同种取最高）；冰酿的"0.8 总额分配"单独算
    group_r = ZERO
    icebrew_total = ZERO
    for other in facility.operators:
        for s in _skills_of(other, SkillKind.DORM_GROUP):
            if not _active(other):
                continue
            if s.id == "冰酿-分配回复":
                icebrew_total = max(icebrew_total, s.value)   # 冰酿：0.8 总额
            else:
                group_r = max(group_r, s.value)

    # 单体回复（dorm_single，同种取最高，且仅一名干员受益：心情最低且未满者）
    single_r = _single_recovery(world, op, facility)

    # 定向回复（dorm_targeted，对满足条件的干员加成，可叠加）
    targeted_r = _targeted_recovery(world, op, facility)

    # 冰酿：0.8 总额平均分配给"心情未满"的宿舍成员
    icebrew_r = _icebrew_recovery(world, op, facility, icebrew_total)

    return recovery + self_r + group_r + single_r + targeted_r + icebrew_r


def _non_full_operators(facility: Facility):
    """宿舍内心情未满（mood < 24）的干员。"""
    return [o for o in facility.operators if o.mood < MOOD_MAX]


def _icebrew_recovery(world, op: Operator, facility: Facility, total: Decimal) -> Decimal:
    """冰酿：总额 total 平均分配给心情未满的宿舍成员。"""
    if total <= ZERO:
        return ZERO
    recipients = _non_full_operators(facility)
    if op not in recipients:
        return ZERO
    return total / len(recipients)


def _targeted_recovery(world, op: Operator, facility: Facility) -> Decimal:
    """定向回复（dorm_targeted）：对满足条件的目标干员进行加成，可叠加。"""
    total = ZERO
    for provider in facility.operators:
        for s in _skills_of(provider, SkillKind.DORM_TARGETED):
            if not _active(provider):
                continue
            ctx = SkillContext(world, provider, op, facility)
            if s.condition is not None and not s.condition(ctx):
                continue
            total += s.value
    return total


def _single_recovery(world, op: Operator, facility: Facility) -> Decimal:
    """单体回复：取同种最高值，作用于"心情最低且未满"的一名干员。

    说明：文档中的单体回复存在"进驻顺序 / 快照锁定"等复杂机制，
    此处采用可实现的简化：锁定心情最低、未满且不持有单体回复技能的干员。
    """
    providers = [o for o in facility.operators
                 if _skills_of(o, SkillKind.DORM_SINGLE) and _active(o)]
    if not providers:
        return ZERO

    # 选出受益人：心情最低、未满、且不是单体回复提供者（宿管优先服务他人）
    candidates = [o for o in _non_full_operators(facility) if o not in providers]
    if not candidates:
        return ZERO
    beneficiary = min(candidates, key=lambda o: o.mood)
    if op is not beneficiary:
        return ZERO

    # 取所有提供者（含条件命中者）中的最高单体回复值
    best = ZERO
    for provider in providers:
        for s in _skills_of(provider, SkillKind.DORM_SINGLE):
            ctx = SkillContext(world, provider, beneficiary, facility)
            if s.condition is not None and not s.condition(ctx):
                continue
            best = max(best, s.value)
    return best


# ----------------------------------------------------------------------------
# 顶层查询：净速率 / 剩余心情 / 剩余工作时间 / 工休比
# ----------------------------------------------------------------------------
def compute_net_rate(world: BaseLayout, operator_name: str) -> Decimal:
    """干员的净消耗速率（点 / 时）。>0 心情下降，<0 心情上升。"""
    op = world.get_operator(operator_name)
    if op is None:
        raise KeyError(f"基建布局中不存在干员：{operator_name}")
    facility = world.facility_of(operator_name)
    if facility is None:
        return ZERO   # 不在任何设施内，视为既不消耗也不回复
    return compute_consumption(world, op, facility) - compute_recovery(world, op, facility)


def remaining_mood_after(world: BaseLayout, operator_name: str, hours) -> Decimal:
    """目标时段（hours 小时）结束后，该干员的剩余心情（安时积分，钳位到 [0,24]）。

    注：假设时段内基建布局不变、且其余干员心情无限（即速率恒定）。
    若时段内该干员心情归零，其后仍为 0（红脸继续工作只是效率下降，心情不再减少）。
    """
    op = world.get_operator(operator_name)
    if op is None:
        raise KeyError(f"基建布局中不存在干员：{operator_name}")
    net = compute_net_rate(world, operator_name)
    return ampere_hour_integration(op.mood, net, hours, MOOD_MAX)


def _work_hours_from(mood, net_rate) -> Decimal:
    """以恒定净消耗速率 net_rate 从心情 mood 放电到 0 所需时间。

    net_rate <= 0（回复不小于消耗）时返回 Infinity（永续）。
    这是"其余干员心情无限、速率恒定"假设下的解析结果。
    """
    if net_rate <= ZERO:
        return INF
    return mood / net_rate


def _time_to_red_face(mood, net_rate) -> Decimal:
    """以恒定净速率 net_rate 从心情 mood 到红脸（mood<=0）所需时间。

    与 _work_hours_from 的区别：心情已经 <=0 时（当前已红脸），返回 0（布局已无法维持），
    而不是交给后面的净速率判断。
    """
    if mood <= ZERO:
        return ZERO
    return _work_hours_from(mood, net_rate)


def _time_to_full(mood, net_rate) -> Decimal:
    """以恒定净回复速率（net_rate<0）从心情 mood 恢复到满（24）所需时间。

    net_rate>=0（非回复）时返回 Infinity。
    """
    if net_rate >= ZERO:
        return INF
    return (MOOD_MAX - mood) / (-net_rate)


def _sustain_hours(mood, net_rate) -> Decimal:
    """single 模式：目标干员在此布局下还能维持/恢复多久（其余干员心情无限、速率恒定）。

    - 工作（net>0）→ 到红脸（mood<=0）所需时长 = mood / net；
    - 宿舍（net<0）→ 恢复到满心情（24）所需时长 = (24 - mood) / (-net)；
    - 不变（net==0）→ Infinity。
    """
    if net_rate > ZERO:
        return mood / net_rate
    if net_rate < ZERO:
        return _time_to_full(mood, net_rate)
    return INF


def remaining_work_hours(world: BaseLayout, operator_name: str) -> Decimal:
    """其余干员心情无限时，该干员"从当前心情起算"还能工作多久（心情耗到 0）。

    注意：这是从"当前（初始）心情"起算；若想求"目标时段结束后还能继续工作多久"，
    请用 evaluate(...) 返回的 sustain_hours（基于时段结束后的剩余心情重算）。
    返回 Infinity 表示"永续"（回复不小于消耗，心情不会耗尽，即"休息中/空闲中"）。
    """
    op = world.get_operator(operator_name)
    if op is None:
        raise KeyError(f"基建布局中不存在干员：{operator_name}")
    net = compute_net_rate(world, operator_name)
    return _work_hours_from(op.mood, net)


def time_to_mood(world: BaseLayout, operator_name: str, target_mood) -> Decimal:
    """其余干员心情无限（速率恒定）时，目标干员达到指定心情所需时间。

    工作（net>0，心情下降）→ t = (当前心情 - 目标心情) / net；
    宿舍（net<0，心情上升）→ t = (目标心情 - 当前心情) / (-net)；
    不变（net==0）→ 目标 == 当前时为 0，否则 Infinity。
    若目标心情已越过（无需时间即可到达），返回 0。

    目标心情应在 [0, 24] 内。
    """
    op = world.get_operator(operator_name)
    if op is None:
        raise KeyError(f"基建布局中不存在干员：{operator_name}")
    net = compute_net_rate(world, operator_name)
    target = to_decimal(target_mood)

    if net == ZERO:
        return ZERO if target == op.mood else INF
    return max((op.mood - target) / net, ZERO)


def evaluate(world: BaseLayout, operator_name: str, period_hours=Decimal("0")) -> MoodResult:
    """single 模式：一次性测算目标干员在指定时段结束后的状态汇总。"""
    op = world.get_operator(operator_name)
    if op is None:
        raise KeyError(f"基建布局中不存在干员：{operator_name}")
    facility = world.facility_of(operator_name)

    net = compute_net_rate(world, operator_name)
    remaining = remaining_mood_after(world, operator_name, period_hours)
    # 需求口径：先按目标时段推进心情，再以"时段结束后的剩余心情"为起点，
    # 在"其余干员心情无限（速率恒定）"假设下计算还能维持/恢复多久。
    # 工作（net>0）→ 到红脸时长；宿舍（net<0）→ 恢复满心情时长。
    sustain = _sustain_hours(remaining, net)

    facility_label = "（不在基建内）"
    state = "未进驻"
    if facility is not None:
        facility_label = FACILITY_LABELS.get(facility.ftype, facility.ftype.value)
        if facility.ftype == FacilityType.DORMITORY:
            state = "宿舍休息"
        elif op.mood <= ZERO:
            state = "红脸（技能失效）"
        elif net < ZERO:
            state = "休息中 / 空闲中（回复大于消耗）"
        elif net == ZERO:
            state = "心情不变"
        else:
            state = "工作中"

    return MoodResult(
        operator_name=operator_name,
        facility_label=facility_label,
        initial_mood=op.mood,
        net_rate=net,
        remaining_mood=remaining,
        sustain_hours=sustain,
        state=state,
    )


# ----------------------------------------------------------------------------
# 整个基建布局的测算（base 模式）
# ----------------------------------------------------------------------------
def evaluate_base(world: BaseLayout, period_hours=Decimal("0")) -> BaseResult:
    """base 模式：测算整个基建布局——所有干员的心情 + 布局可维持时长。

    计算口径：
      1. （可选）先按 period_hours 推进每个干员的心情（速率恒定假设，同 single 模式）。
      2. 布局可维持时长 layout_sustain_hours = 所有干员"到红脸（mood<=0）的剩余时长"的最小值
         （mood/net，net>0；宿舍/回复 net<=0 为 Infinity；已红脸 mood<=0 为 0），
         并标出瓶颈干员（最先生红脸者）。
      3. 每个干员的 sustain_hours：
         - 工作（net>0）/ 不变（net=0）→ 统一 = layout_sustain_hours；
         - 宿舍（net<0）→ 若能在临界时间前恢复满，则 = 恢复满所需时间；否则 = layout_sustain_hours。
         每个干员 mood_at_end = 到达 layout_sustain_hours 时的心情 = clamp(mood - net × layout_sustain_hours, 0, 24)。
         若 layout_sustain_hours 为 Infinity（无人会红脸），则二者均为 None（JSON null）。

    注：这是解析解（速率恒定）。若需要"红脸后技能失效引发联动"的精确轨迹，用 simulator.simulate。
    """
    entries = []   # (name, facility_label, mood, net, 个体到红脸时长)
    for op in world.all_operators():
        facility = world.facility_of(op.name)
        net = compute_net_rate(world, op.name)
        mood = ampere_hour_integration(op.mood, net, period_hours, MOOD_MAX)
        facility_label = FACILITY_LABELS.get(facility.ftype, "（不在基建内）") if facility else "（不在基建内）"
        entries.append((op.name, facility_label, mood, net, _time_to_red_face(mood, net)))

    if not entries:
        return BaseResult(operators=[], layout_sustain_hours=INF, bottleneck=None)

    # 布局可维持时长 = 最先生红脸者的个体时长
    layout_sustain = min(e[4] for e in entries)
    bottleneck = min(entries, key=lambda e: e[4])[0] if layout_sustain != INF else None

    operators = []
    for name, flabel, mood, net, _ in entries:
        if layout_sustain == INF:
            sustain = INF
            mood_at_end = None
        else:
            # 宿舍干员（net<0）：若能在临界时间前恢复满，则记录恢复满所需时间；否则维持布局可维持时长
            if net < ZERO:
                sustain = min(_time_to_full(mood, net), layout_sustain)
            else:
                sustain = layout_sustain
            # 到达布局可维持时长那一刻的心情（工作=继续消耗、宿舍=继续回复，均钳位到 [0,24]）
            mood_at_end = ampere_hour_integration(mood, net, layout_sustain, MOOD_MAX)
        operators.append(OperatorResult(
            name=name,
            facility_label=flabel,
            mood=mood,
            sustain_hours=sustain,
            mood_at_end=mood_at_end,
        ))

    return BaseResult(operators=operators, layout_sustain_hours=layout_sustain, bottleneck=bottleneck)


# ----------------------------------------------------------------------------
# 工休比（文档第四部分）
# ----------------------------------------------------------------------------
@dataclass
class WorkRestRatio:
    consumption: Decimal          # 工作时心情消耗 x
    recovery: Decimal             # 休息时心情回复 y
    work_time_full: Decimal       # 满心情可连续工作：24 / x
    recover_time_full: Decimal    # 从零回满：24 / y
    ratio: Decimal                # 最大工休比：y / x
    work_share: Decimal           # 最大工作时长占比：y / (x + y)
    max_work_per_day: Decimal     # 24 小时内最长可工作时间：24y / (x + y)


def work_rest_ratio(consumption, recovery) -> WorkRestRatio:
    """由工作时消耗 x 与休息时回复 y，计算工休比相关指标。"""
    consumption = to_decimal(consumption)
    recovery = to_decimal(recovery)
    work_time_full = MOOD_MAX / consumption if consumption > ZERO else INF
    recover_time_full = MOOD_MAX / recovery if recovery > ZERO else INF
    ratio = recovery / consumption if consumption > ZERO else INF
    work_share = recovery / (consumption + recovery) if (consumption + recovery) > ZERO else Decimal("1")
    return WorkRestRatio(
        consumption=consumption,
        recovery=recovery,
        work_time_full=work_time_full,
        recover_time_full=recover_time_full,
        ratio=ratio,
        work_share=work_share,
        max_work_per_day=MOOD_MAX * work_share,
    )

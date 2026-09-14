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
    FACILITY_LABELS,
    MOOD_MAX,
    WORK_FACILITIES,
    FacilityType,
    base_consumption,
    cc_reduction,
    dormitory_recovery,
    facility_mood_reduction,
)
from .config import DORM_LEVEL_TABLE
from .ledger import Bucket, Contribution, MoodLedger
from .variables import BASIS_DOC, VariableLedger, basis_count, collect_variables
from .models import BaseLayout, BaseResult, Facility, MoodResult, Operator, OperatorResult
from .skill_templates import Stacking
from .skills import (
    SKILLS,
    SKILL_EQUIPS,
    SPREAD_SKILL_IDS,
    SkillEquip,
    SkillKind,
    _factions_of,
    base_skill_id,
)


@dataclass
class SkillContext:
    """技能判定所需的上下文快照。"""
    world: BaseLayout
    owner: Operator        # 技能持有者
    target: Operator       # 技能作用对象
    facility: Facility     # 目标所在设施
    variables: Optional[VariableLedger] = None   # 基建级变量快照（人间烟火/热情值/无声共鸣…）


def _scaled_amount(skill, variables=None, world=None, facility=None, op=None):
    """折算 skill.value：先按**变量**（`var_*`），再按**计数基准**（`basis`）。

    返回 (是否成立, 折算后的值, 说明文本)：
      - `var_min`（「<变量> 处于 N 点及以上」）：门槛，值不缩放，不满足则**不成立**；
      - `var_per`（「每有 N 点 <变量>」）：值 × floor(变量 / N)；
      - `basis`（「每有 N 间发电站 / 宿舍每级 / 每有 1 名其他干员…」）：值 × 计数；
      - 两者都没有：原值。
    """
    ok, value, detail = True, skill.value, ""
    # ① 变量（中间货币）
    if getattr(skill, "var_name", None) and variables is not None:
        name = skill.var_name
        cur = variables.get(name)
        if skill.var_min is not None:
            ok = variables.at_least(name, skill.var_min)
            detail += f"（{name} = {cur}，需 ≥ {skill.var_min}）"
        elif skill.var_per is not None:
            units = variables.units(name, skill.var_per)
            value = value * units
            detail += f"（{name} = {cur}，每 {skill.var_per} 点 → {units} 份）"
        else:
            detail += f"（{name} = {cur}）"
    # ② 计数基准（可数条件）
    basis = getattr(skill, "basis", None)
    if basis and world is not None:
        n = basis_count(world, basis, facility, op)
        value = value * n
        detail += f"（{BASIS_DOC.get(basis, basis)} × {n}）"
    return ok, value, detail


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------
def _skills_of(op: Operator, kind: SkillKind):
    """取干员持有的、当前已解锁且未失效的某类技能列表。

    精英化判断：
      - 解锁：op.elite >= SkillEquip.unlock_elite 且 op.level >= SkillEquip.unlock_level；
      - β 替换 α：同一干员同一 family 内，若"提升"技能（enhanced=True）已解锁，
        则其 replaces 指向的低版本被替换（不叠加）；
      - 待译技能（untranslated=True）暂不生效。
    """
    return [SKILLS[sid] for sid in _active_skill_ids(op) if SKILLS[sid].kind == kind]


def _template_skills(op: Operator, template_id: str):
    """按**模板**取已生效技能（用于 M07b / M15a 这类「模板即机制」的技能）。

    这类技能的 `kind` 只是把它落到某个相近的族里，真正的机制由模板决定，
    所以调度按 `template_id` 而不是按 `kind`（与 documents/04-特殊机制.md 第 25 条 的 M07b 同一约定）。
    """
    return [SKILLS[sid] for sid in _active_skill_ids(op) if SKILLS[sid].template_id == template_id]


def _equip_of(op: Operator, sid: str) -> Optional[SkillEquip]:
    """干员↔技能的装备绑定；无绑定（自定义 skill_id）时返回 None。"""
    return SKILL_EQUIPS.get((op.name, sid))


def _unlocked(op: Operator, sid: str) -> bool:
    """技能是否已解锁（精英化/等级门槛）。无绑定的自定义技能默认视为已解锁。"""
    equip = _equip_of(op, sid)
    if equip is None:
        return True
    return op.elite >= equip.unlock_elite and op.level >= equip.unlock_level


def _active_skill_ids(op: Operator):
    """干员当前生效的技能 id 集合（已解锁 + 未被 β 替换 + 非待译）。"""
    unlocked = {
        sid for sid in op.skill_ids
        if sid in SKILLS and not SKILLS[sid].untranslated and _unlocked(op, sid)
    }
    # β 替换 α：某技能若被"已解锁的提升技能"replaces 指向，则**整条技能**（含全部分句）被替换。
    # 注意 `replaces` 存的是 skill_id（不带 #clause），因此按 base_skill_id 剔除，
    # 否则同一技能的其它分句会漏剔（历史 bug，见 generate_skills_data.load_operators 注释）。
    replaced = {base_skill_id(e.replaces) for sid in unlocked
                if (e := _equip_of(op, sid)) is not None and e.replaces}
    return {sid for sid in unlocked if base_skill_id(sid) not in replaced}


def _active(op: Operator) -> bool:
    """干员是否处于"有技能"状态：红脸（心情<=0）时技能失效。"""
    return op.mood > ZERO


def _count_faction(facility: Facility, faction: str) -> Decimal:
    """统计某设施内属于某阵营的干员数（含未进驻的干员不在内）。"""
    return Decimal(sum(1 for o in facility.operators if faction in _factions_of(o)))


def _scope_ok(skill, facility: Facility, target: Operator) -> bool:
    """判断技能的设施范围与阵营条件是否命中。"""
    if skill.facility_types and facility.ftype not in skill.facility_types:
        return False
    if skill.target_faction and skill.target_faction not in _factions_of(target):
        return False
    return True


# ============================================================================
# 心情消耗 / 回复：**流水账驱动**
#
# 唯一计算路径：先把每一条来源记成 Contribution，再交给 MoodLedger 按轴 F 合成。
# 好处（对比重构前）：
#   · 没有"两份逻辑"（带/不带解释各算一遍）——算出来的值天然就是解释里的值；
#   · 新增叠加规则 = 加一个 Stacking 分支，不再往这里塞 max()/短路；
#   · 每条来源都带 owner / skill / template，可回答"为什么是这个速率"。
# ============================================================================
def consume_ledger(world: BaseLayout, op: Operator, facility: Facility,
                   variables: Optional[VariableLedger] = None) -> MoodLedger:
    """构建**消耗侧**流水账。

    组成（与文档一致）：
      基础消耗 base_consumption(设施类型)   ← 加工站/训练室是「挂件位」= 0（见 config）
      - 设施基础减免 X（制造 / 贸易，按进驻人数）
      - 控制中枢全局减免（满员 0.25）
      ± 干员自身技能（self_consume）
      ± 同设施干员的设施级技能（facility_consume，含自身）
      - 中枢全局减免技能（cc_reduce，同干员内求和后跨干员取最高）
      以及"消除类技能"把「自身技能」这一组整组归零。
    """
    variables = variables if variables is not None else collect_variables(world)
    lg = MoodLedger(op.name, facility.display_name, variables=variables)
    if facility.ftype == FacilityType.DORMITORY:
        lg.add(Contribution(Bucket.CONSUME, "宿舍内不消耗心情", ZERO, group="base",
                            template="BASE", target=op.name))
        return lg

    lg.add(Contribution(Bucket.CONSUME, "基础消耗", base_consumption(facility.ftype),
                        group="base", template="BASE", target=op.name,
                        detail=f"（{facility.display_name} Lv{facility.level}）"))

    reduction = facility_mood_reduction(facility.ftype, len(facility.operators))
    if reduction:
        lg.add(Contribution(Bucket.CONSUME, "设施基础减免", -reduction,
                            group="facility_reduction", template="BASE", target=op.name,
                            detail=f"（{facility.display_name} {len(facility.operators)} 人："
                                   f"每多 1 人 -0.05，上限 0.1）"))

    cc = world.control_center()
    if cc is not None:
        cred = cc_reduction(len(cc.operators))
        if cred:
            lg.add(Contribution(Bucket.CONSUME, "控制中枢全局减免", -cred,
                                group="cc_reduction", template="BASE", target=op.name,
                                detail=f"（中枢 {len(cc.operators)} 人，每人 -0.05）"))

    # 自身技能（正=加耗 / 负=减耗）
    if _active(op):
        for skill in _skills_of(op, SkillKind.SELF_CONSUME):
            if not _scope_ok(skill, facility, op):
                continue
            ctx = SkillContext(world, op, op, facility, variables)
            if skill.condition is not None and not skill.condition(ctx):
                continue
            ok, amount, vtxt = _scaled_amount(skill, variables, world, facility, op)
            if not ok:
                continue
            lg.add(Contribution(Bucket.CONSUME, "自身消耗", amount,
                                group="self_consume", owner=op.name, target=op.name,
                                skill_id=skill.id, skill_name=skill.name,
                                template=skill.template_id, detail=vtxt))

    # 设施级技能：同设施所有干员（含自身）对全体生效
    for other in facility.operators:
        if not _active(other):
            continue
        for skill in _skills_of(other, SkillKind.FACILITY_CONSUME):
            if not _scope_ok(skill, facility, op):
                continue
            ctx = SkillContext(world, other, op, facility, variables)
            if skill.condition is not None and not skill.condition(ctx):
                continue
            ok, amount, vtxt = _scaled_amount(skill, variables, world, facility, op)
            if not ok:
                continue
            lg.add(Contribution(Bucket.CONSUME, "同设施全体消耗", amount,
                                group="facility_consume", owner=other.name, target=op.name,
                                skill_id=skill.id, skill_name=skill.name,
                                template=skill.template_id, detail=vtxt))

    # 同设施**其他**干员（ROOM_OTHERS_CONSUME：当前数据无使用者，低语已改判含自身）
    for other in facility.operators:
        if other is op or not _active(other):
            continue
        for skill in _skills_of(other, SkillKind.ROOM_OTHERS_CONSUME):
            if not _scope_ok(skill, facility, op):
                continue
            ctx = SkillContext(world, other, op, facility)
            if skill.condition is not None and not skill.condition(ctx):
                continue
            lg.add(Contribution(Bucket.CONSUME, "同设施他人消耗", skill.value,
                                group="facility_consume", owner=other.name, target=op.name,
                                skill_id=skill.id, skill_name=skill.name,
                                template=skill.template_id))

    # 中枢全局减免技能：同干员内求和 → 跨干员取最高，故聚合成一条带"获胜者"的记录
    best_value, best_owner, best_skill = ZERO, "", ""
    if cc is not None:
        for owner in cc.operators:
            if not _active(owner):
                continue
            per_owner = ZERO
            for skill in _skills_of(owner, SkillKind.CC_REDUCE):
                if skill.facility_types and facility.ftype not in skill.facility_types:
                    continue
                ctx = SkillContext(world, owner, op, facility)
                if skill.condition is not None and not skill.condition(ctx):
                    continue
                per_owner += skill.value
                if per_owner > best_value:
                    best_owner, best_skill = owner.name, skill.name
            best_value = max(best_value, per_owner)
    if best_value:
        lg.add(Contribution(Bucket.CONSUME, "中枢减免技能", -best_value,
                            group="cc_reduce", template="CC_REDUCE",
                            owner=best_owner, target=op.name, skill_name=best_skill,
                            detail="（跨干员取最高：同干员内求和后取最大减免）"))

    # 消除类：把「自身技能」整组归零（正负影响都移除；设施/中枢减免不受影响）
    # 注意：**包含目标自身**——上游「团队精神：消除当前制造站内**所有**干员自身心情消耗的影响」
    # 与「杯莫停：消除当前控制中枢内所有岁干员自身心情消耗的影响」都含持有者自己；
    # 若叶睦「互为半身」更是只消除**自身**（与他人同驻中枢时）。
    for other in facility.operators:
        if not _active(other):
            continue
        for skill in _skills_of(other, SkillKind.ELIMINATE_SELF):
            # 语义区分（上游原文）：
            #   · 槐琥「团队精神」/ 令「杯莫停」：消除**同设施所有干员**（含他人）的自身消耗影响
            #   · 若叶睦「互为半身」：消除**自身**的（self_only=True），与他人无关
            if skill.self_only and other is not op:
                continue
            if skill.facility_types and facility.ftype not in skill.facility_types:
                continue
            if skill.target_faction and skill.target_faction not in _factions_of(op):
                continue
            # ⚠️ 条件必须求值：若叶睦「互为半身」是「当与丰川祥子一起进驻控制中枢时…」，
            # 不求值就会变成无条件消除（实测踩过）。
            ctx = SkillContext(world, other, op, facility, variables)
            if skill.condition is not None and not skill.condition(ctx):
                continue
            lg.add(Contribution(Bucket.CONSUME, "消除自身消耗影响", ZERO,
                                group="eliminate", stacking=Stacking.ZERO_PRIORITY,
                                zeroes_group="self_consume", owner=other.name, target=op.name,
                                skill_id=skill.id, skill_name=skill.name,
                                template=skill.template_id,
                                detail="→ 该干员「自身技能」影响整组归零"))
            return lg          # 原逻辑为布尔（存在即消除），一条足够
    return lg


def recovery_ledger(world: BaseLayout, op: Operator, facility: Facility,
                    variables: Optional[VariableLedger] = None) -> MoodLedger:
    """构建**回复侧**流水账（宿舍走 `_dorm_ledger`，其余走 `_work_ledger`）。"""
    variables = variables if variables is not None else collect_variables(world)
    if facility.ftype == FacilityType.DORMITORY:
        return _dorm_ledger(world, op, facility, variables)
    return _work_ledger(world, op, facility, variables)


def _work_ledger(world: BaseLayout, op: Operator, facility: Facility,
                 variables: Optional[VariableLedger] = None) -> MoodLedger:
    """工作设施内的回复：来自中枢内干员的 `CC_RECOVER` 技能。

    三条叠加规则（轴 F，全部来自官方术语表）：
      1. **求和（F1）**：不同来源默认相加。
      2. **跨干员取最高（F3，`max_group`）**：官方术语 `cc.c.sui2_1` 规定
         公事公办 / 孤光共照 / 巴别塔之帜 的 room2 恢复值取最高——
         实现为「同干员各 clause 先求和，再跨干员取 max」（由 MoodLedger 完成）。
      3. **扩散（M02c）**：玛恩纳「公事公办」——官方术语 `cc.c.skill` 的 15 条白名单
         中枢回复技能，额外作用到 room2（其他设施）内工作状态的干员。
    """
    variables = variables if variables is not None else collect_variables(world)
    lg = MoodLedger(op.name, facility.display_name, variables=variables)
    cc = world.control_center()
    if cc is None:
        return lg
    # 自身回复（非宿舍）：模板 M07b —— 如歌蕾蒂娅「潮汐守望」的「反之」分支
    # （进驻控制中枢时，若宿舍以外没有深海猎人，则自身心情每小时恢复 +0.5）。
    # ⚠️ 该分支按「含持有者自身」口径（§4.23）后**恒不成立**：她自己就在宿舍以外。
    # 入口本身仍然保留——M07b 是通用模板，将来若有别的非宿舍自身回复技能会走这里。
    # ⚠️ 此前 `_work_ledger` 完全不处理 DORM_SELF 类技能，M07b 从未被求值（结构缺口）。
    # 用 template_id 区分：M10 = 宿舍自身回复（只在 `_dorm_ledger` 处理），M07b = 非宿舍自身回复。
    if _active(op):
        for skill in _skills_of(op, SkillKind.DORM_SELF):
            if skill.template_id != "M07b":
                continue
            ctx = SkillContext(world, op, op, facility, variables)
            if skill.condition is not None and not skill.condition(ctx):
                continue
            _ok, _amount, _vtxt = _scaled_amount(skill, variables, world, facility, op)
            if not _ok or not _amount:
                continue
            lg.add(Contribution(Bucket.RECOVER, "自身回复", _amount, group="self_recover",
                                owner=op.name, target=op.name, skill_id=skill.id,
                                skill_name=skill.name, template=skill.template_id,
                                detail=_vtxt))

    spread_on = _spread_active(world)
    for owner in cc.operators:
        if not _active(owner):
            continue
        for skill in _skills_of(owner, SkillKind.CC_RECOVER):
            if not _reaches(skill, facility.ftype, spread_on):
                continue
            ctx = SkillContext(world, owner, op, facility, variables)
            if skill.condition is not None and not skill.condition(ctx):
                continue
            ok, scaled, vtxt = _scaled_amount(skill, variables, world, facility, op)
            if not ok:
                continue
            detail = vtxt
            if skill.count_faction:
                amount = scaled * _count_faction(facility, skill.count_faction)
                detail += f"（每个「{skill.count_faction}」干员 {skill.value}）"
            else:
                amount = scaled
            if spread_on and base_skill_id(skill.id) in SPREAD_SKILL_IDS \
                    and facility.ftype in WORK_FACILITIES \
                    and (not skill.facility_types or facility.ftype not in skill.facility_types):
                detail += "（玛恩纳「公事公办」扩散）"
            if skill.max_group:
                detail += "（跨干员取最高，不叠加）"
            lg.add(Contribution(
                Bucket.RECOVER, "中枢回复", amount, group="cc_recover",
                stacking=Stacking.CROSS_OWNER_MAX if skill.max_group else Stacking.SUM,
                max_group=skill.max_group or "", owner=owner.name, target=op.name,
                skill_id=skill.id, skill_name=skill.name, template=skill.template_id,
                detail=detail))
    return lg


def _reaches(skill, ftype, spread_on: bool) -> bool:
    """技能是否作用到目标设施（含玛恩纳「扩散」的额外路径）。"""
    if not skill.facility_types:
        return True
    if ftype in skill.facility_types:
        return True
    # 扩散：白名单技能（官方 cc.c.skill）额外作用到 room2「其他设施」
    return (spread_on
            and base_skill_id(skill.id) in SPREAD_SKILL_IDS
            and ftype in WORK_FACILITIES)


def _spread_active(world) -> bool:
    """中枢内是否存在「扩散」提供者（玛恩纳公事公办）且其未红脸。"""
    cc = world.control_center()
    if cc is None:
        return False
    for owner in cc.operators:
        if not _active(owner):
            continue
        for skill in _skills_of(owner, SkillKind.CC_RECOVER):
            if skill.spread_whitelist:
                return True
    return False


def _dorm_ledger(world: BaseLayout, op: Operator, facility: Facility,
                 variables: Optional[VariableLedger] = None) -> MoodLedger:
    """宿舍内的回复流水账：基础回复 + 各类干员回复技能。

    叠加规则：不同类型（基础 / 自身 / 群体 / 单体 / 定向 / 池）之间相加；
    同种类型内部**取最高**（由 MoodLedger 的 `SAME_KIND_MAX` 完成）。
    菲亚梅塔为独占（清空一切其它来源），冰酿为池分配。
    """
    variables = variables if variables is not None else collect_variables(world)
    lg = MoodLedger(op.name, facility.display_name, variables=variables)

    # --- 独占：菲亚梅塔「自律」：自身 +2 且不接受其它任何来源（含宿舍基础回复）---
    if _active(op):
        exclusives = [s for s in _skills_of(op, SkillKind.DORM_SELF) if s.exclusive]
        if exclusives:
            best = max(exclusives, key=lambda s: s.value)
            lg.add(Contribution(Bucket.RECOVER, "独占回复", best.value, group="dorm_self",
                                stacking=Stacking.SAME_KIND_MAX, exclusive=True,
                                owner=op.name, target=op.name, skill_id=best.id,
                                skill_name=best.name, template=best.template_id,
                                detail="不接受宿舍基础回复与其它任何来源"))
            return lg

    # --- 基础回复（白字 + 绿字氛围）---
    level = facility.level if facility.level in DORM_LEVEL_TABLE else 1
    atmo_used = facility.atmosphere if facility.atmosphere is not None \
        else DORM_LEVEL_TABLE[level]["atmosphere_max"]
    lg.add(Contribution(Bucket.RECOVER, "宿舍基础回复",
                        dormitory_recovery(facility.level, facility.atmosphere),
                        group="dorm_base", template="BASE", target=op.name,
                        detail=f"（1.5 + 0.1×{facility.level} + 0.0004×{atmo_used}）"))

    # --- 中枢干员对宿舍的回复（领袖/战纹/巡心/羁绊相生/无言的慈爱…）---
    cc = world.control_center()
    if cc is not None:
        for owner in cc.operators:
            if not _active(owner):
                continue
            for skill in _skills_of(owner, SkillKind.CC_RECOVER):
                if facility.ftype not in skill.facility_types:
                    continue
                ctx = SkillContext(world, owner, op, facility)
                if skill.condition is not None and not skill.condition(ctx):
                    continue
                _ok, amount, vtxt = _scaled_amount(skill, variables, world, facility, op)
                if not _ok:
                    continue
                if skill.count_faction:
                    amount = amount * _count_faction(facility, skill.count_faction)
                    detail = f"（宿舍内每个「{skill.count_faction}」干员 {skill.value}）"
                else:
                    detail = "（同种效果取最高）"
                lg.add(Contribution(Bucket.RECOVER, "中枢→宿舍回复", amount,
                                    group="cc_dorm", owner=owner.name, target=op.name,
                                    skill_id=skill.id, skill_name=skill.name,
                                    template=skill.template_id, detail=detail + vtxt))

    # --- 自身回复（dorm_self，同种取最高）---
    if _active(op):
        for skill in _skills_of(op, SkillKind.DORM_SELF):
            if skill.exclusive:
                continue
            ctx = SkillContext(world, op, op, facility, variables)
            if skill.condition is not None and not skill.condition(ctx):
                continue
            _ok, _amount, _vtxt = _scaled_amount(skill, variables, world, facility, op)
            if not _ok:
                continue
            lg.add(Contribution(Bucket.RECOVER, "宿舍自身回复", _amount,
                                group="dorm_self", stacking=Stacking.SAME_KIND_MAX,
                                owner=op.name, target=op.name, skill_id=skill.id,
                                skill_name=skill.name, template=skill.template_id,
                                detail="（同种效果取最高）" + _vtxt))

    # --- 群体回复（dorm_group，同种取最高）；冰酿的池分配单独处理 ---
    pool_total = ZERO
    pool_skill = None
    for other in facility.operators:
        if not _active(other):
            continue
        for skill in _skills_of(other, SkillKind.DORM_GROUP):
            if skill.pool:
                if skill.value > pool_total:
                    pool_total, pool_skill = skill.value, skill
                continue
            # ⚠️ 条件必须求值：资深料理人「如果目标是莱欧斯小队干员，则恢复效果额外 +0.15」
            # 就是一个**按目标筛选**的群体回复条件；不求值会对所有人无条件生效（实测踩过）。
            ctx = SkillContext(world, other, op, facility, variables)
            if skill.condition is not None and not skill.condition(ctx):
                continue
            _ok, _amount, _vtxt = _scaled_amount(skill, variables, world, facility, op)
            lg.add(Contribution(Bucket.RECOVER, "宿舍群体回复", _amount,
                                group="dorm_group", stacking=Stacking.SAME_KIND_MAX,
                                owner=other.name, target=op.name, skill_id=skill.id,
                                skill_name=skill.name, template=skill.template_id,
                                detail="（同种效果取最高）" + _vtxt))

    # --- 单体回复（dorm_single，同种取最高，仅一名受益者）---
    single, s_owner, s_skill = _single_recovery(world, op, facility, variables)
    if single:
        lg.add(Contribution(Bucket.RECOVER, "宿舍单体回复", single, group="dorm_single",
                            stacking=Stacking.SAME_KIND_MAX, owner=s_owner, target=op.name,
                            skill_id=s_skill.id if s_skill else "",
                            skill_name=s_skill.name if s_skill else "",
                            template=s_skill.template_id if s_skill else "",
                            detail="（锁定心情最低且未满、非提供者的一名干员；同种取最高）"))

    # --- 定向回复（dorm_targeted：满足条件者，求和）---
    for provider in facility.operators:
        if not _active(provider):
            continue
        for skill in _skills_of(provider, SkillKind.DORM_TARGETED):
            ctx = SkillContext(world, provider, op, facility, variables)
            if skill.condition is not None and not skill.condition(ctx):
                continue
            _ok, _amount, _vtxt = _scaled_amount(skill, variables, world, facility, op)
            if not _ok:
                continue
            lg.add(Contribution(Bucket.RECOVER, "宿舍定向回复", _amount,
                                group="dorm_targeted", owner=provider.name, target=op.name,
                                skill_id=skill.id, skill_name=skill.name,
                                template=skill.template_id,
                                detail="（满足条件者叠加求和）" + _vtxt))

    # --- 元修正（M17）：**他人**的恢复效果被强化（摩根「头号陪练」→ 推进之王）---
    # 上游原文（`dorm_rec_toone[000]`）：「进驻宿舍时，**推进之王**对该宿舍中**格拉斯哥帮**
    # 干员恢复效果额外 +0.3」——摩根自己不回复，而是把**别人已经算出的那条贡献**顶上去。
    # 实现：找出被点名提供者（boost_provider）已记入流水账、且 group 匹配（boost_group）的
    # 贡献，逐条补一条**同组同技能**的增量贡献——于是 `SAME_KIND_MAX` 会把它
    # 先并入该技能的合计、再与其他技能取最高（正是"恢复效果额外 +0.3"的语义）。
    # 若把增量记成独立技能，会被"同种取最高"当成竞争者而丢掉。
    for mod_owner in facility.operators:
        if not _active(mod_owner):
            continue
        for ms in _skills_of(mod_owner, SkillKind.DORM_META):
            provider = next((o for o in facility.operators
                             if o.name == ms.boost_provider and _active(o)), None)
            if provider is None:
                continue
            ctx = SkillContext(world, mod_owner, op, facility, variables)
            if ms.condition is not None and not ms.condition(ctx):
                continue
            _ok, amount, vtxt = _scaled_amount(ms, variables, world, facility, op)
            if not _ok or amount <= ZERO:
                continue
            targets = [c for c in lg.of(Bucket.RECOVER)
                       if c.owner == provider.name
                       and (not ms.boost_group or c.group == ms.boost_group)]
            for c in targets:
                lg.add(Contribution(Bucket.RECOVER, c.label, amount, group=c.group,
                                    stacking=c.stacking, owner=c.owner, target=c.target,
                                    skill_id=c.skill_id, skill_name=c.skill_name,
                                    template=c.template, max_group=c.max_group,
                                    detail=f"（由 {mod_owner.name}「{ms.name}」强化）" + vtxt))

    # --- 池分配：冰酿 0.8 总额平摊给"心情未满"的宿舍成员 ---
    if pool_total > ZERO and pool_skill is not None:
        recipients = _non_full_operators(facility)
        if recipients and op in recipients:
            lg.add(Contribution(Bucket.RECOVER, "宿舍池分配", pool_total / len(recipients),
                                group="dorm_pool", stacking=Stacking.POOL,
                                owner=pool_skill.name, target=op.name,
                                skill_id=pool_skill.id, skill_name=pool_skill.name,
                                template=pool_skill.template_id, pool_share=len(recipients),
                                detail=f"（总额 {pool_total} 由 {len(recipients)} 名未满成员均分）"))
    return lg


def _non_full_operators(facility: Facility):
    """宿舍内心情未满（mood < 24）的干员。"""
    return [o for o in facility.operators if o.mood < MOOD_MAX]


def _targeted_recovery(world, op: Operator, facility: Facility) -> Decimal:
    """定向回复（dorm_targeted）的合计值（保留给外部调用；主计算路径见 `_dorm_ledger`）。"""
    lg = _dorm_ledger(world, op, facility)
    return sum((c.value for c in lg.of(Bucket.RECOVER) if c.group == "dorm_targeted"), ZERO)


def _single_recovery(world, op: Operator, facility: Facility, variables=None):
    """单体回复：返回 (值, 提供者名, 技能)。

    取同种最高值，作用于"心情最低且未满"的一名干员。
    说明：文档中的单体回复存在"进驻顺序 / 快照锁定"等复杂机制，
    此处采用可实现的简化：锁定心情最低、未满且不持有单体回复技能的干员。

    ⚠️ 「同种效果取最高」的比较单位是**技能**（含它的各分句），不是分句：
    上游 M09 系列写「…每小时恢复 +0.55（同种效果取最高），**如果目标是 X，
    则恢复效果额外 +0.45**」——基础分句与定向加成分句属于**同一条技能**，
    要先求和（0.55+0.45=1.00）再与其他单体回复技能取最高。
    故这里先把每条分句记进流水账，再用 `MoodLedger.same_kind_winner` 取获胜**技能实例**
    （旧实现按单条分句取 max，会把 +0.45 的定向加成整个吃掉）。
    """
    providers = [o for o in facility.operators
                 if _active(o) and _skills_of(o, SkillKind.DORM_SINGLE)]
    if not providers:
        return ZERO, "", None

    candidates = [o for o in _non_full_operators(facility) if o not in providers]
    if not candidates:
        return ZERO, "", None
    beneficiary = min(candidates, key=lambda o: o.mood)
    if op is not beneficiary:
        return ZERO, "", None

    lg = MoodLedger(op.name, facility.display_name, variables=variables)
    by_inst = {}
    for provider in providers:
        for s in _skills_of(provider, SkillKind.DORM_SINGLE):
            ctx = SkillContext(world, provider, beneficiary, facility, variables)
            if s.condition is not None and not s.condition(ctx):
                continue
            _ok, amount, vtxt = _scaled_amount(s, variables, world, facility, beneficiary)
            if not _ok:
                continue
            lg.add(Contribution(Bucket.RECOVER, "宿舍单体回复", amount,
                                group="dorm_single", stacking=Stacking.SAME_KIND_MAX,
                                owner=provider.name, target=op.name, skill_id=s.id,
                                skill_name=s.name, template=s.template_id,
                                detail="（同种效果取最高）" + vtxt))
            by_inst[(provider.name, base_skill_id(s.id))] = s

    value, rep = lg.same_kind_winner(Bucket.RECOVER, "dorm_single")
    if rep is None or value <= ZERO:
        return ZERO, "", None
    return value, rep.owner, by_inst.get((rep.owner, base_skill_id(rep.skill_id)))


def apply_entry_events(world: BaseLayout, swap_with=None, enabled=None):
    """**进驻瞬间的一次性结算**（M15a 心情互换），就地修改 `world` 的干员心情。

    为什么单独一个入口：这类技能的效果不是「每小时 ±N 点」，而是**进驻那一刻的状态跳变**，
    所以它既不该进 `consume_ledger`/`recovery_ledger`（那不是速率），也不该进时间积分。
    它是**布局初始化**语义，因此做成显式 API，由调用方决定是否应用（`main.py --entry-events`）。

    ⚠️ **会就地修改** `world.operators[].mood`；返回本次结算的事件流水账（`Bucket.EVENT`），
    供 `--explain` 展示。重复调用是幂等的：互换后触发者不再是满心情，条件不再成立。

    已实现：**患难之交**（菲亚梅塔，`dorm_exchangeAp[000]`）
      上游原文：「进驻宿舍时，如果**自身为满心情**，则与当前宿舍**前一位进驻**的干员互换心情」。

    参数（决定"换不换 / 换谁"，两者都可配）：

    | 参数 | 取值 | 优先级 |
    |---|---|---|
    | `enabled` | `True`/`False` = 强制结算/不结算；`None` = 看 `world.entry_events.enabled` | 显式 > JSON > **默认结算**（"调用这个函数"本身就是"要结算"） |
    | `swap_with` | 干员名 = 与**同宿舍**的该干员互换；`None` = 用 JSON 里的 `swap_with`；都没有 = 「前一位进驻」 | 显式 > JSON > 默认前一任 |

    「前一位进驻」= `Facility.operators` 里排在触发者之前的那一位——
    布局的 `operators` **本来就是有序列表**（进驻顺序），所以不需要额外的队列结构。
    若指定的人不在同一宿舍，**不换**，但会记一条 `Bucket.EVENT` 说明原因（界面/`--explain` 能看到）。
    """
    cfg = getattr(world, "entry_events", None)
    if enabled is None:
        configured = getattr(cfg, "enabled", None)
        enabled = True if configured is None else bool(configured)
    if not enabled:
        return []
    if swap_with is None:
        swap_with = getattr(cfg, "swap_with", None) or None

    events = []
    for facility in world.facilities:
        if facility.ftype != FacilityType.DORMITORY:
            continue
        for idx, op in enumerate(facility.operators):
            if not _active(op):
                continue
            for skill in _template_skills(op, "M15a"):
                ctx = SkillContext(world, op, op, facility)
                if skill.condition is not None and not skill.condition(ctx):
                    continue
                if swap_with:                      # ① 指定了交换对象
                    other = next((o for o in facility.operators
                                  if o is not op and o.name == swap_with), None)
                    if other is None:
                        events.append(Contribution(
                            Bucket.EVENT, "进驻事件未执行", ZERO, group="entry_swap_skipped",
                            owner=op.name, target=swap_with, skill_id=skill.id,
                            skill_name=skill.name, template=skill.template_id,
                            detail=f"（指定的交换对象「{swap_with}」不在 {facility.display_name} 里）"))
                        continue
                else:                              # ② 默认「前一位进驻」
                    if idx == 0:
                        continue
                    other = facility.operators[idx - 1]
                if other.mood == op.mood:
                    continue
                before = (op.mood, other.mood)
                op.mood, other.mood = before[1], before[0]
                how = f"指定的 {other.name}" if swap_with else f"「前一位进驻」的 {other.name}"
                events.append(Contribution(
                    Bucket.EVENT, "心情互换", ZERO, group="entry_swap",
                    owner=op.name, target=other.name, skill_id=skill.id,
                    skill_name=skill.name, template=skill.template_id,
                    detail=f"（与{how}互换：{op.name} "
                           f"{before[0]} → {before[1]}，{other.name} {before[1]} → {before[0]}）"))
    return events


def entry_event_holders(world: BaseLayout):
    """列出**可能**触发进驻事件（M15a）的干员 → `[(干员名, 所在房间名), ...]`。

    只做"模板 + 未红脸"的粗筛（真正的触发还要满足条件，如"自身为满心情"），
    供界面提示"这个布局里谁会触发换心情"、以及收集"能和谁换"的候选。
    """
    out = []
    for facility in world.facilities:
        if facility.ftype != FacilityType.DORMITORY:
            continue
        for op in facility.operators:
            if not _active(op):
                continue
            if _template_skills(op, "M15a"):
                out.append((op.name, facility.display_name))
    return out


def compute_consumption(world: BaseLayout, op: Operator, facility: Facility,
                        ledger: MoodLedger = None) -> Decimal:
    """干员的心情消耗速率（点/时，结果 >= 0）。

    `ledger` 非空时，把本次的全部来源追加进去（用于 `--explain` / 调试）。
    """
    lg = consume_ledger(world, op, facility)
    if ledger is not None:
        ledger.items.extend(lg.items)
    return max(ZERO, lg.total(Bucket.CONSUME))


def compute_recovery(world: BaseLayout, op: Operator, facility: Facility,
                     ledger: MoodLedger = None) -> Decimal:
    """干员的心情回复速率（点/时）。`ledger` 非空时同时记录来源。"""
    lg = recovery_ledger(world, op, facility)
    if ledger is not None:
        ledger.items.extend(lg.items)
    return lg.total(Bucket.RECOVER)


# ----------------------------------------------------------------------------
# 顶层查询：净速率 / 剩余心情 / 剩余工作时间 / 工休比
# ----------------------------------------------------------------------------
def mood_ledger(world: BaseLayout, operator_name: str) -> MoodLedger:
    """**完整流水账**：消耗 + 回复 + 净速率（唯一计算路径，见 ledger.py）。

    这是"让内部完全理解干员心情机制"的入口：
        lg = mood_ledger(world, "刺玫"); print(lg.explain())
    会逐条列出：谁（owner）→ 哪条技能（skill/template）→ 作用于谁 → 多少值
    → 按轴 F 哪条规则合成（同种取最高 / 跨干员取最高 / 池分配 / 归零 / 独占）。
    """
    op = world.get_operator(operator_name)
    if op is None:
        raise KeyError(f"基建布局中不存在干员：{operator_name}")
    facility = world.facility_of(operator_name)
    if facility is None:
        lg = MoodLedger(operator_name, "（不在基建内）")
        lg.add(Contribution(Bucket.CONSUME, "未进驻任何设施：不消耗也不回复", ZERO,
                            group="base", template="BASE", target=operator_name))
        return lg
    variables = collect_variables(world)
    lg = consume_ledger(world, op, facility, variables)
    lg.items.extend(recovery_ledger(world, op, facility, variables).items)
    return lg


def compute_net_rate(world: BaseLayout, operator_name: str) -> Decimal:
    """干员的净消耗速率（点 / 时）。>0 心情下降，<0 心情上升。"""
    op = world.get_operator(operator_name)
    if op is None:
        raise KeyError(f"基建布局中不存在干员：{operator_name}")
    if world.facility_of(operator_name) is None:
        return ZERO   # 不在任何设施内，视为既不消耗也不回复
    return mood_ledger(world, operator_name).net_rate()


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
        ledger=mood_ledger(world, operator_name),
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

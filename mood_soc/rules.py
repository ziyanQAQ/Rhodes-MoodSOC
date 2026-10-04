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
from typing import Dict, List, Optional

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
    use_project_decimal_context,
)
from .config import DORM_LEVEL_TABLE
from .ledger import Bucket, Contribution, MoodLedger
from .variables import BASIS_DOC, VariableLedger, basis_count, collect_variables
from .models import BaseLayout, BaseResult, Facility, MoodResult, Operator, OperatorResult
from .models import (clear_manual, facility_occupancy, mark_manual, normalize_entry_when,
                     read_manual, remove_occupant, set_seat)
from .models import ManualLedger
from .skill_templates import Stacking
from .skills import (
    DEFAULT_OPERATORS,
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
        if basis == "power_count":
            # 「仅影响设施数量」的两条修正（森蚺 / 晨曦）：让人一眼看出 4 间是怎么来的
            from .facility_count import explain as _explain_power
            detail += _explain_power(world, FacilityType.POWER)
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

    ⚠️ 结果**按 `(技能槽, 练度, kind)` 缓存**在干员实例上：实测一次 7 周期重算里它被调
    164 万次（每次都要重建一个列表）。`_active_skill_ids` 已经缓存，这里再省掉列表重建。
    **调用方只读**（全仓库没有任何地方改它的返回值）。
    """
    key = (op.elite, op.level, len(op.skill_ids), id(op.skill_ids))
    cache = op.__dict__.get("_skills_by_kind_cache")
    if cache is not None and cache[0] == key:
        got = cache[1].get(kind)
        if got is not None:
            return got
    else:
        cache = (key, {})
        op.__dict__["_skills_by_kind_cache"] = cache
    val = [SKILLS[sid] for sid in _active_skill_ids(op) if SKILLS[sid].kind == kind]
    cache[1][kind] = val
    return val


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
    """干员当前生效的技能 id 集合（已解锁 + 未被 β 替换 + 非待译）。

    ⚠️ **结果缓存在干员实例上**（性能，2026-09）：它只依赖"技能槽 + 练度 + 技能表"，
    与心情、位置无关 —— 而实测一次 7 周期重算里它被调 **164 万次**（占 profile 32% 累计），
    因为每次算速率都会把同一个人的技能槽重新过滤一遍、`base_skill_id` 还要拆 140 万次字符串。
    缓存键＝`(精英化, 等级, len(skill_ids), id(skill_ids))`：
    前两项管解锁与 β 替换，后两项管"构造完再往 `skill_ids` 里塞东西"的场景
    （`scripts/verify_skills.py` 的 L2 就靠手工注入 skill_id 造场景）；
    换成另一个列表对象或改了长度都会自动失效，所以**不需要任何手工清理**。
    """
    key = (op.elite, op.level, len(op.skill_ids), id(op.skill_ids))
    cache = op.__dict__.get("_active_ids_cache")
    if cache is not None and cache[0] == key:
        return cache[1]
    unlocked = {
        sid for sid in op.skill_ids
        if sid in SKILLS and not SKILLS[sid].untranslated and _unlocked(op, sid)
    }
    # β 替换 α：某技能若被"已解锁的提升技能"replaces 指向，则**整条技能**（含全部分句）被替换。
    # 注意 `replaces` 存的是 skill_id（不带 #clause），因此按 base_skill_id 剔除，
    # 否则同一技能的其它分句会漏剔（历史 bug，见 generate_skills_data.load_operators 注释）。
    replaced = {base_skill_id(e.replaces) for sid in unlocked
                if (e := _equip_of(op, sid)) is not None and e.replaces}
    val = {sid for sid in unlocked if base_skill_id(sid) not in replaced}
    op.__dict__["_active_ids_cache"] = (key, val)
    return val


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


# 自动挑交换对象时认这些写法（＝"全基建最累的那位"）
ENTRY_AUTO_TARGETS = ("any", "auto", "anyone", "任意", "最累", "谁都可以")


def _position_of(world: BaseLayout, op: Operator):
    """干员在布局里的 `(设施, 位次)`；不在布局里返回 `(None, -1)`。

    ⚠️ 位次走 `slot_map()`（**带空洞的位次**），不是列表下标：清空过某一位之后
    两者不再相等，用列表下标去写 `operators[i]` 会换错人。
    """
    for f in world.facilities:
        for i, o in f.slot_map().items():
            if o is op:
                return f, i
    return None, -1


def _swap_positions(world: BaseLayout, a: Operator, b: Operator) -> str:
    """把两名干员的**位置**互换（就地）。返回人类可读说明（换不了时返回**一句原因**）。

    走 `models.set_seat`（空洞的唯一写入口），所以 `_slots` 位次映射不会失配。

    ⚠️ **手动锁优先于进驻事件**（用户裁决 2026-10，Q-A）：两人的位置里只要**任何一个**
    是"手动锁住"的（位次被钉住 `pins_slot`，或那个人被钉住 `pins_name`），就
    **只换心情、不换位置** —— 进驻事件的本职是"换心情"，位置对调只是 `restore_back=False`
    的附加效果，不该推翻"手动锁＝第 1 层、永不被动"这条不变式。
    """
    fa, ia = _position_of(world, a)
    fb, ib = _position_of(world, b)
    if fa is None or fb is None:
        return ""
    for fac, idx, op in ((fa, ia, a), (fb, ib, b)):
        led = read_manual(fac)
        if led.pins_slot(idx) or led.pins_name(op.name):
            return (f"（位置**不对调**：{op.name} 所在的 {fac.display_name} "
                    f"第 {idx + 1} 位是你手动锁住的）")
    set_seat(fa, ia, b)
    set_seat(fb, ib, a)
    world.invalidate_index()             # 成员换了房间 ⇒ 名字索引作废（见 `models._name_index`）
    return (f"（位置也对调：{a.name} 去 {fb.display_name}，"
            f"{b.name} 去 {fa.display_name}）")


def entry_target_kind(swap_with, scope: str = "dorm") -> str:
    """判断该按哪种口径找交换对象 → `"named"` / `"auto"` / `"default"`。

    **行为与文案都从这里取**（曾经文案按 `swap_with` 单独判断，导致
    `scope=anywhere` + 不点名时"实际自动挑、却写成前一位进驻"）。
    """
    name = (swap_with or "").strip()
    if name.lower() in ENTRY_AUTO_TARGETS or name in ENTRY_AUTO_TARGETS:
        return "auto"
    if name:
        return "named"
    return "auto" if scope == "anywhere" else "default"


def find_entry_target(world: BaseLayout, holder: Operator, facility: Facility,
                      swap_with=None, scope: str = "dorm"):
    """找进驻事件的交换对象 → `(Operator | None, 说明文本)`。

    规则（`scope="anywhere"` 就是"**基建任意位置**都能换"，口径见 `entry_target_kind`）：

    | 配置 | 结果 |
    |---|---|
    | `swap_with` = 人名 | 在 `scope` 允许范围内找那个人（`dorm` 限**触发者所在的那一间** / `anywhere` 全基建） |
    | `swap_with` = `any` / `任意` / `最累` | **自动挑全基建心情最低的那位**（并列取先出现的） |
    | 没给 `swap_with` 且 `scope="anywhere"` | 同上（自动挑最累的） |
    | 没给 `swap_with` 且 `scope="dorm"` | 默认「**前一位进驻**」（**触发者所在那一间**里排在她之前的那位） |

    ⚠️ **2026-10 触发者放宽后的两处口径**（用户裁决，见 `apply_entry_events`）：
      · `facility` 现在可能是**任意设施**（她不再必须进驻宿舍）⇒「前一位进驻」＝
        **她所在那一间**里排在她之前的那位（她在宿舍时与旧口径逐字相同）；
      · `facility` 为 `None` ＝ 她只在**「不在基建」名单**里、不属于任何房间 ⇒
        「前一位进驻」与「同一宿舍」都无从谈起（没有房间），只有
        `scope="anywhere"` / 点名 / 自动挑才换得动。
    """
    name = (swap_with or "").strip()
    kind = entry_target_kind(swap_with, scope)
    if kind == "default":
        if facility is None:
            return None, "（她人在「不在基建」名单里、不属于任何房间 ⇒ 没有「前一位进驻」可换）"
        idx = facility.operators.index(holder)
        if idx == 0:
            return None, f"（{facility.display_name} 里没有「前一位进驻」的干员）"
        return facility.operators[idx - 1], ""
    if kind == "auto":
        pool = [o for o in world.all_operators() if o is not holder]
        if not pool:
            return None, "（基建里没有别的干员可以换）"
        best = min(pool, key=lambda o: o.mood)
        return best, f"（自动挑：全基建心情最低的是 {best.name} {best.mood}）"
    if scope == "anywhere":
        pool = [o for o in world.all_operators() if o is not holder]
    elif facility is None:
        pool = []                          # 她不在任何房间 ⇒「同一间」无从谈起（见上）
    else:
        pool = [o for o in facility.operators if o is not holder]
    for o in pool:
        if o.name == name:
            return o, ""
    if scope == "anywhere":
        where = "基建内"
    elif facility is None:
        where = "她所在的房间（她人在「不在基建」名单里，不属于任何房间）"
    else:
        where = facility.display_name
    return None, f"（指定的交换对象「{name}」不在{where}）"


def _outside_operator(world: BaseLayout, name: str, mood=None) -> Operator:
    """「不在基建」名单里那位的**代理 `Operator`**（按世界快照缓存）。

    为什么需要它：名单里的人**不在 `world.facilities` 里**（见 `models.BaseLayout.detached`），
    而 2026-10 起 M15a 患难之交把她也算作"存在"（用户裁决，见 `apply_entry_events`）——
    判定"她持不持有这条技能"、以及找交换对象，都要一个干员对象。

    缓存挂在 `world.__dict__` 上（与 `models.BaseLayout._name_index_cache` 同一手法），
    于是 `entry_swapped`（"同一份快照只结算一次"）能像世界里的干员一样留着；
    `reset_entry_events` 会连他们一起归位。

    ⚠️ 她的**心情只存在于调用方那张表**（`detached_moods`）：`mood=None` 时不动代理对象上的值
    （粗筛用，如 `entry_event_holders`）。
    """
    cache = world.__dict__.setdefault("_entry_outside_ops", {})
    op = cache.get(name)
    if op is None:
        op = Operator(name=name, skill_ids=list(DEFAULT_OPERATORS.get(name, ())),
                      # 满练口径：与 `store.layout.build_operator` 的默认（E2 / 30 级）一致
                      elite=2, level=30)
        cache[name] = op
    if mood is not None:
        op.mood = to_decimal(mood)
    return op


def _entry_trigger_ops(world: BaseLayout, detached_moods=None):
    """产出 M15a（进驻事件）的**候选触发者** → `[(干员, 所在设施 | None), ...]`。

    2026-10 触发条件放宽（**用户裁决**，见 `apply_entry_events`）：
      · 她在**这一班的任意设施**里即可（工作设施 / 控制中枢…都算），不再要求"进驻宿舍"；
      · 她在**「不在基建」名单**（`world.detached`）里也算"存在" —— 此时设施是 `None`，
        心情取 `detached_moods[name]`；**没给那张表就当她不在场**
        （名单里的人心情只存在于那张表里，换了也没处记）。
    ⚠️ **副手**不在内（`facility.deputies`）：那是"挂件位"，与"她进驻在某设施"不是一回事，
       本次口径没有涉及它（原先扫描也只走 `operators`）。
    """
    for facility in world.facilities:
        for op in facility.operators:
            if _active(op):
                yield op, facility
    if not detached_moods:
        return
    for name in (world.detached or ()):
        if name not in detached_moods or world.get_operator(name) is not None:
            continue                 # 不在那张表里 / 她本来就在基建里 ⇒ 别重复算一遍
        yield _outside_operator(world, name, detached_moods[name]), None


def apply_entry_events(world: BaseLayout, swap_with=None, enabled=None,
                       scope=None, restore_back=None, when=None, detached_moods=None):
    """**进驻瞬间的一次性结算**（M15a 心情互换），就地修改 `world` 的干员心情。

    为什么单独一个入口：这类技能的效果不是「每小时 ±N 点」，而是**进驻那一刻的状态跳变**，
    所以它既不该进 `consume_ledger`/`recovery_ledger`（那不是速率），也不该进时间积分。
    它是**布局初始化**语义，因此做成显式 API，由调用方决定是否应用（`main.py --entry-events`）。

    ⚠️ **会就地修改** `world`（干员心情；`restore_back=False` 时还包括位置），
    以及 `detached_moods`（名单里那些人的心情）；
    返回本次结算的事件流水账（`Bucket.EVENT`），供 `--explain` 展示。

    已实现：**患难之交**（菲亚梅塔，`dorm_exchangeAp[000]`）
      上游原文：「进驻宿舍时，如果**自身为满心情**，则与当前宿舍**前一位进驻**的干员互换心情」。

    ⚠️⚠️ **触发条件的两次放宽（2026-10，用户裁决 —— 对上游原文的「有意偏离」）**
      **用户原话**：「换心情菲亚梅塔**不要求一定出现在宿舍中**，只要该布局中**存在**菲亚梅塔
      就可以生效。」+ 两条边界：①「她在这一班的**任意设施**里即可（工作设施、控制中枢…都算，
      不只是宿舍）」；②「她即使在**「不在基建」名单**里，也算"存在"、照样触发」。
      因此触发者从"宿舍住户"改成 `_entry_trigger_ops`：**任意设施的在岗干员 + 「不在基建」名单**。
      ⚠️ ② 是**有意例外，不是 bug，别再"改回去"**：它与项目总口径「不在基建的人
      **不参与任何技能计数**、心情一条平线」（`AGENTS.md` 坑 17、`models.BaseLayout.detached`）
      相冲突，用户**明确知道**这层张力并要求本技能照此处理。例外**只限"她算不算在场"**：
      她照旧不在 `facilities` 里（不计入任何技能计数、不消耗不回复、曲线仍是平线），
      只有本事件会把她的心情换掉。见 `documents/04-特殊机制.md` 第 29 条。
      ⚠️ 上游写的是「**进驻宿舍时**」⇒ 这是**用户要求的有意偏离**，不是"实现漏了/写错了"。
      下游随之改了两处：`find_entry_target` 的「前一位进驻」＝**她所在那一间**的前一位
      （她在宿舍时与旧口径逐字相同）；`facility=None`（只在名单里）时只有
      `anywhere` / 点名 / 自动挑才换得动。

    本项目在此之上做了可配置扩展（用户需求）：

    | 参数 | 取值 | 优先级 |
    |---|---|---|
    | `enabled` | `True`/`False` 强制结算/不结算；`None` 看 `world.entry_events.enabled` | 显式 > JSON > 默认结算 |
    | `swap_with` | 人名 / `any`（自动挑最累的）/ `None`（用 JSON，再没有＝「前一位进驻」） | 显式 > JSON > 默认前一任 |
    | `scope` | `"dorm"`（限**她所在的那一间**，旧称"同宿舍"）/ `"anywhere"`（**基建任意位置**） | 显式 > JSON > 默认 dorm |
    | `restore_back` | `True`（默认）= 只换心情、两人都留在原位置；`False` = **位置也一起互换** | 显式 > JSON > 默认 True |
    | `when` | **什么时候换**：`"immediate"`（默认，**强制立刻换**：不管她满不满、也不管对方心情是多少）/ `"wait"`（等她回满再换）/ `"full"`（只在她满心情时换，游戏原口径） | 显式 > JSON > 默认 immediate |
    | `detached_moods` | **「不在基建」名单里那些人的当前心情** `{名字: Decimal}`（**就地读写**） | 排班模拟传实时心情表；不传＝名单里的人不算在场 |

    **强制交换**（用户口径）：只要开了并设了对象，就**执行互换**——
    `when="immediate"` 时连"她是否满心情"都不检查；而且**不再因为"双方心情相同"而跳过**
    （哪怕两边都是 24，事件照记、`restore_back=False` 时位置照换）。

    若指定的对象找不到，**不换**，但会记一条 `Bucket.EVENT`（group=`entry_swap_skipped`）说明原因。
    `when="wait"` 的"等待"是**带时间**的语义，只在 `ui.schedule.simulate_schedule` 里生效；
    本函数是一次性结算，不会等待（此时会记一条说明）。
    """
    cfg = getattr(world, "entry_events", None)
    if enabled is None:
        configured = getattr(cfg, "enabled", None)
        enabled = True if configured is None else bool(configured)
    if not enabled:
        return []
    if swap_with is None:
        swap_with = getattr(cfg, "swap_with", None) or None
    if scope is None:
        scope = getattr(cfg, "scope", "dorm") or "dorm"
    if restore_back is None:
        restore_back = bool(getattr(cfg, "restore_back", True))
    mode = normalize_entry_when(when) or normalize_entry_when(getattr(cfg, "when", None)) \
        or "immediate"

    events = []
    # ⚠️ 触发者＝**任意设施的在岗干员 + 「不在基建」名单**（2026-10 用户裁决的放宽，
    #    原先是"必须是宿舍住户"）。例外与理由见上面 docstring，**别改回宿舍限定**。
    for op, facility in _entry_trigger_ops(world, detached_moods):
        if getattr(op, "entry_swapped", False):
            continue                       # 同一份布局快照里只结算一次（重复调用幂等）
        for skill in _template_skills(op, "M15a"):
            # `facility` 可能是 `None`（她只在名单里）——本技能的条件只读 `ctx.owner.mood`
            ctx = SkillContext(world, op, op, facility)
            if mode != "immediate" and skill.condition is not None \
                    and not skill.condition(ctx):
                # 「非强制」模式才检查"她是否满心情"；配了 wait 又没满时给一条说明
                if mode == "wait" and op.mood < MOOD_MAX:
                    events.append(Contribution(
                        Bucket.EVENT, "进驻事件未执行", ZERO, group="entry_swap_skipped",
                        owner=op.name, target="", skill_id=skill.id, skill_name=skill.name,
                        template=skill.template_id,
                        detail=f"（到点没满（当前 {op.mood}）→ 等她回满再换；"
                               f"「等待」只在带时间的排班模拟里生效，一次性结算不等）"))
                continue
            other, note = find_entry_target(world, op, facility, swap_with, scope)
            if other is None:
                # 只有"点名要换某人 / 自动挑"却没换成时才记一条说明；
                # 默认口径（「前一位进驻」而她排第一）属于游戏本来的"没得换"，静默跳过。
                if swap_with:
                    events.append(Contribution(
                        Bucket.EVENT, "进驻事件未执行", ZERO, group="entry_swap_skipped",
                        owner=op.name, target=(swap_with or ""), skill_id=skill.id,
                        skill_name=skill.name, template=skill.template_id, detail=note))
                continue
            # ⚠️ 这里**不再**做 `other.mood == op.mood` 的跳过：用户口径是
            #    "不管对方心情是多少，只要设置了就执行互换"（数值相同时位置该换也换）。
            before = (op.mood, other.mood)
            op.mood, other.mood = before[1], before[0]
            how = {"auto": f"自动挑的 {other.name}",
                   "named": f"指定的 {other.name}",
                   "default": f"「前一位进驻」的 {other.name}"}[
                       entry_target_kind(swap_with, scope)]
            detail = (f"（与{how}互换：{op.name} "
                      f"{before[0]} → {before[1]}，{other.name} {before[1]} → {before[0]}）")
            if before[0] == before[1]:
                detail += "（双方心情本来就相同，数值不变）"
            if not restore_back:
                detail += _swap_positions(world, op, other) or "（位置对调失败：有人不在布局里）"
            else:
                detail += "（位置不变：被换满的干员留在自己的岗位上）"
            events.append(Contribution(
                Bucket.EVENT, "心情互换", ZERO, group="entry_swap",
                owner=op.name, target=other.name, skill_id=skill.id,
                skill_name=skill.name, template=skill.template_id, detail=detail))
            op.entry_swapped = True    # 标记"这一份快照已经换过了"，重复调用不再来回换
    # 「不在基建」名单里的人：她的心情**只有调用方那张表记得住** ⇒ 换完写回去
    # （`op` 是 `_outside_operator` 的代理对象，不在 `world` 里，`_read_back_moods` 读不到她）
    if detached_moods is not None:
        for name, op in (world.__dict__.get("_entry_outside_ops") or {}).items():
            if name in detached_moods:
                detached_moods[name] = op.mood
    return events


def reset_entry_events(world: BaseLayout) -> int:
    """把"这一份布局快照已结算过进驻事件"的标记**归位**（返回归位的干员数）。

    为什么需要它：`apply_entry_events` 用 `Operator.entry_swapped` 保证**同一份快照只结算一次**
    （重复调用幂等）。而"**再次进驻**"是另一回事——同一个布局副本被**跨班次 / 跨周期复用**时
    （`ui.schedule.simulate_schedule` 就是复用每个班次的副本），每一个班次开始都是一次**新的
    进驻瞬间**，必须重新判定。所以那个入口在每次"进驻那一刻"之前调用本函数。

    ⚠️ 连**「不在基建」名单里的代理干员**（`_entry_outside_ops` 缓存）一起归位：他们和世界里的
    干员一样带 `entry_swapped`，漏掉就会出现"第 2 个周期起一次都不再重判"（与当年那个多周期
    漏算的 bug 同一形状）。

    语义边界（免得被误用）：它**不改心情、不改位置**，只清标记；一次性结算（`main.py --entry-events`
    那样只跑一次的场景）不需要它，也就保持了"重复调用不出二次效果"的原有保证。
    """
    ops = list(world.all_operators())
    ops += list((world.__dict__.get("_entry_outside_ops") or {}).values())
    n = 0
    for op in ops:
        if getattr(op, "entry_swapped", False):
            op.entry_swapped = False
            n += 1
    return n


# ----------------------------------------------------------------------------
# 「闲置入宿」（三层解耦后的第二块：**自动入宿逻辑**，2026-10 重写）
#
# 优先级链（唯一口径，见 `_seat_verdict`）：  手动编辑  >  自动入宿  >  导入布局
#
#   · **导入布局**＝基线，**不是**护身符：它只决定"谁一开始在哪个位置"，
#     住进宿舍的人照样可以被自动入宿换出去（换人时只按位置与心情选目标）。
#   · **手动编辑**（`models.ManualLedger`）＝用户对"某个班次某个位置"的操作：
#     钉住的位次与手动放进去的人**绝对不碰**（不占、不换）。导入不打标。
#   · **锁定位置**（全局配置）＝竖向正序前 `protected_slots` 个位置：
#     里面的人自动不换，但**里面的空位照样能入住**。
#
# 自动入宿本身只有两相：
#
#   相 1（填空床） 竖向正序取第一个"可入住空位"，候选按 (心情↑, 名字↑) 依次入住；
#   相 2（换人）   队列改最小堆：反复取**心情最低**的候选，让她替换
#                  **锁定区外、心情严格大于她、心情最大**的那位住户。
#
#   相 2 的终态＝**锁定区之外的每位住户，心情都 ≥ 队列里剩下的所有人**；找不到
#   这样的住户就立刻停下（这句就是"低心情优先入宿"的可判定形式）。
#   被换出者：心情 < 24、不在黑名单 ⇒ 按 (心情↑, 名字↑) **插回队列**（不是追加队尾）。
#   终止性：每次换人都是"更低心情者替换更高心情者" ⇒ 宿舍内心情总和严格下降 ⇒ 必然收敛。
#
# 关键概念：
#   · **竖向正序** = 位次优先、宿舍序号其次：宿1位1、宿2位1、…、宿1位2、…
#   · **空洞**：清空某一位**不左移**后面的人（位次粘人，`models.set_seat`）；
#     "可入住空位"＝第一个**既没住户、又没被手动钉住**的位次。
#   · **黑名单** = 永远不能"通过闲置入宿进宿舍"的人（可被换出，不是保护位次）。
#
# ⚠️ 已作废（别再加回来）：手动指定位置 / 手动点名交换（那两件事归**手动编辑逻辑**，
#    入口是看板与「干员与心情」表，写进布局快照 + 台账）；竖向反序扫描取"首个严格大于"
#    的自动交换；被换出者追加队尾；以及更早的四级优先级 / 挂件门 / 阵营门 / 菲亚梅塔例外。
# ⚠️ **不设班次数量门槛**：1 个班次的排班照样执行。
# ----------------------------------------------------------------------------
#: 锁定位置数默认值（文档 §5）。
DEFAULT_PROTECTED_SLOTS = 5

#: 座位裁决的四种动作（见 `_seat_verdict`）。
SEAT_AUTO = "auto"            # 空位、无手动标记、非锁定 ⇒ 自动入宿可入住
SEAT_KEEP = "keep"            # 已占位但**不许动**（手动钉住的位次 / 手动放进去的人）
SEAT_LOCKED = "locked"        # 已占位且在锁定区 ⇒ 不可被换出（锁定区的**空位**仍走 AUTO）
SEAT_SWAPPABLE = "swappable"  # 已占位、可被换出（导入住户与自动填入者都在这里）


def _seat_key(world: BaseLayout, facility: Facility) -> tuple:
    """**设施的稳定标识** —— 用作锁定位置集合的键（`(类型, 世界下标 | 实例名)`）。

    ⚠️ 不能用 `id(facility)`：排班层是**先在 pristine（"未动过的计划副本"）上算锁定位置、
    再在每段的深拷贝上跑自动入宿**，两边的 `Facility` 是不同的对象 ⇒ 用 id 当键会让
    锁定区**静默失效**（实测踩到）。
    ⚠️ 也不能只用 `display_name`：**同名宿舍**会共用一个键，锁定位置互相顶掉
    （实测：4 个锁定位置只剩 2 个）。
    ⇒ 写了 `name` 用名字（跨深拷贝稳定、也能扛住设施顺序变化），没写名字退回**世界下标**。
    ✅ **2026-10 起"没写名字"这条路上不会再走到**：`store.layout.build_base_layout` 会给未命名
    设施**自动补名**（`{类型标签}#{同类型序号}`，且跳过已被占用的名字）⇒ 名字分支恒成立，
    世界下标只是**防御性兜底**（手工构造 `Facility` 的测试、或不属于这个世界的设施）。
    这也正是补名的目的：世界下标会随"布局增删设施"漂移 ⇒ 锁定区与手动锁**保护到别的房间**。
    """
    if facility.name:
        return (facility.ftype, facility.name)
    for i, f in enumerate(world.facilities):
        if f is facility:
            return (facility.ftype, i)
    return (facility.ftype, facility.display_name)


def _seat_verdict(facility: Optional[Facility], index: int, *, world=None, protected=None,
                  ledger=None) -> tuple:
    """★ **三层逻辑的唯一裁决点** —— "这一位现在归谁、能不能动"。

    返回 `(action, occupant, reason)`：

    | action | 含义 | 自动入宿能做什么 |
    |---|---|---|
    | `SEAT_AUTO` | 空位（没人占） | 可以入住 |
    | `SEAT_KEEP` | 已占位，但**手动编辑**钉住了这一位或这个人 | 谁都不许动 |
    | `SEAT_LOCKED` | 已占位，且在**锁定区**内 | 不许被换出 |
    | `SEAT_SWAPPABLE` | 已占位，可被换出（含导入住户） | 换人时可作目标 |

    ⚠️ 判定顺序＝优先级链本身：**手动 → 锁定区 → 空位 → 其余**。
    ⚠️ 手动标记**优先级高于"空位"**：被钉住但没人占的位次（`slots` 里钉住、但没人占）
    也算 `SEAT_KEEP` —— "这一位保持空着"就是它要表达的意思。
    （2026-10 起这种状态只由 **API 的 `set_seat_lock`** 造出来 —— "预留空位"；
    界面口径是**摆位即上锁、清空即解锁**，清空的那一位**不在** `slots` 里。）
    ⚠️ `protected` 的键是 `_seat_key(world, facility)`（**不是对象 id**，见那个函数）；
    要判锁定区就必须传 `world`（否则只判手动标记与占位）。
    整个模块里只允许这一个地方做这三条判断（别在别处再写一份）。
    """
    mapping = facility.slot_map() if facility is not None else {}
    occupant = mapping.get(index)
    led = ledger if ledger is not None else (read_manual(facility) if facility is not None
                                            else ManualLedger())
    if led.pins_slot(index) or (occupant is not None and led.pins_name(occupant.name)):
        return SEAT_KEEP, occupant, "手动编辑（这一位/这个人由你钉住）"
    if occupant is None:
        return SEAT_AUTO, None, ""
    if protected and world is not None and (_seat_key(world, facility), index) in protected:
        return SEAT_LOCKED, occupant, "锁定位置"
    return SEAT_SWAPPABLE, occupant, "可换出"


def _dorm_numbered(world: BaseLayout) -> List[tuple]:
    """→ `[(宿舍序号（1 基）, 设施), ...]`：**可用宿舍按布局里的出现顺序**。

    宿舍序号 = 它是布局 `facilities` 里的第几间宿舍（1 基）—— 与界面「宿舍NN」一致。
    ⚠️ 序号必须从"未被排序的原始顺序"里取（曾经拿排序后的下标当序号，换人换错了房间）。
    文档 §4.1：**禁用宿舍不参与排序，也不占用锁定数量**。
    """
    from .config import FacilityType as _FT

    dorms = [f for f in world.facilities if f.ftype == _FT.DORMITORY and f.enabled]
    return list(enumerate(dorms, start=1))


def dorm_state(world: BaseLayout) -> dict:
    """**这一刻的宿舍态**（名字 + 位次信息）

    → `{"dorms": {序号: [名字或 None…按位次]}, "free": [还有空位的序号…],
        "next": {序号: 可入住的位次（1 基）}, "capacity": {序号: 容量},
        "holes": {序号: [空的位次（1 基）…]}}`

    谁在用：`apply_idle_to_dorm` 的 `trace`（**逐位候选**各留一份）与界面面板。
    ⚠️ `dorms[no]` 按**位次**对齐、空槽写 `None`（第 i 项＝第 i+1 位）；
    尾部还没到过人的空位不占数组长度，由 `capacity` / `holes` 表达。
    """
    dorms: dict = {}
    free: List[int] = []
    nxt: dict = {}
    cap: dict = {}
    holes: dict = {}
    for no, dorm in _dorm_numbered(world):
        mapping = dorm.slot_map()
        led = read_manual(dorm)
        cap[no] = int(dorm.capacity)
        # 已"到过"的位次长度：已占位次与**手动钉住的位次**里最靠后的那个（手工清空的位次
        # 也是"到过"——它必须继续显示为一格空的，不能被当成"还没到过"的尾部）。
        reached = [i for i in list(mapping) + [int(s) for s in led.slots] if i < cap[no]]
        reach = (max(reached) + 1) if reached else 0
        dorms[no] = [mapping[i].name if i in mapping else None for i in range(reach)]
        open_slot = dorm.next_open_slot()
        nxt[no] = (open_slot + 1) if open_slot is not None else cap[no] + 1
        holes[no] = [i + 1 for i in range(cap[no]) if i not in mapping]
        if open_slot is not None:
            free.append(no)
    return {"dorms": dorms, "free": free, "next": nxt, "capacity": cap, "holes": holes}


def _protected_positions(world: BaseLayout, protected_slots) -> set:
    """**锁定位置**集合 `{(_seat_key(设施), 0 基位次), …}` —— 竖向正序的前 `protected_slots` 个。

    ⚠️ 键是**稳定标识**（`_seat_key`）而不是对象 id：排班层先在 pristine 上算它、
    再在每段深拷贝上用它（见 `_seat_key` 的说明）。

    文档 §5：`protected_slots` 钳位到 `[0, 当前班次可用宿舍的总位置数]`（超了按总数生效、
    不报错）；枚举按**竖向正序**（位次优先、宿舍序号其次），只收**真实存在**的位置
    （位次 ≤ 该宿舍容量）。默认 5 ⇒ 4 间宿舍各 5 位时锁：宿1位1、宿2位1、宿3位1、宿4位1、宿1位2。
    """
    dorms = _dorm_numbered(world)
    total = sum(int(f.capacity) for _no, f in dorms)
    count = max(0, min(int(protected_slots or 0), total))
    out: set = set()
    if not dorms or count <= 0:
        return out
    max_cap = max(int(f.capacity) for _no, f in dorms)
    made = 0
    for slot in range(1, max_cap + 1):
        for _no, fac in dorms:
            if slot > int(fac.capacity):
                continue
            out.add((_seat_key(world, fac), slot - 1))
            made += 1
            if made >= count:
                return out
    return out


def _next_free_slots(world: BaseLayout) -> List[tuple]:
    """每间可用宿舍"当前可入住的下一个位置" → `[((位次, 宿舍序号), 设施, 0 基位次), …]`。

    只有**既没住户、又没被手动钉住**的位次才算可入住（手动清空的位次会被锁住）。
    排序键＝竖向正序 `(位次, 宿舍序号)`。
    """
    out: List[tuple] = []
    for no, fac in _dorm_numbered(world):
        for i in range(int(fac.capacity)):
            action, _who, _why = _seat_verdict(fac, i, world=world)
            if action == SEAT_AUTO:
                out.append(((i + 1, no), fac, i))
    out.sort(key=lambda row: row[0])
    return out

def _best_swap_victim(world: BaseLayout, candidate_mood, *, protected=None, ceiling=None):
    """**相 2 的目标** → `(设施, 0 基位次, 干员)`；没有合格目标 → `(None, None, None)`。

    口径（相 2 的终态）：在**锁定区之外、且没被手动钉住**的住户里，取
    **心情 ≥ 候选、且心情最大**的那一位（并列时取竖向正序最靠前）。

    为什么是"≥ 里取最大"：相 2 按候选心情**从小到大**处理，且被换出者不在本执行点再入队。
    取"≥ 候选里最大的那位"＝**把最少的那点余量让出去**，剩下的住户仍然够后面的候选换；
    取最小的那位会把余量吃光，后到的、心情更高的候选就**换不进来**（实测：宿舍里留下
    22/24，而外面还站着 10，达不到终态）。

    `ceiling`＝**本次允许换出的心情下限**（`None` = 不限）：调用方传"还排在队里的候选的最低心情"，
    保证被换出去的人不会比还在排队的人更该进宿舍。导入布局的住户与自动填入者都在目标里
    （导入不上锁）。
    """
    best = None                          # (排序键, 设施, 位次, 干员)
    for no, fac in _dorm_numbered(world):
        for i in range(int(fac.capacity)):
            action, occupant, _why = _seat_verdict(fac, i, world=world, protected=protected)
            if action != SEAT_SWAPPABLE or occupant.mood < candidate_mood:
                continue
            if ceiling is not None and occupant.mood < ceiling:
                continue
            key = (-occupant.mood, i, no)     # 心情最大优先；再竖向正序
            if best is None or key < best[0]:
                best = (key, fac, i, occupant)
    if best is None:
        return None, None, None
    return best[1], best[2], best[3]


def apply_idle_to_dorm(world: BaseLayout, *, enabled=None,
                       idle=None, only=None, swap_with=None, scope=None,
                       trace: Optional[dict] = None) -> List[Contribution]:
    """**把"未满心情的闲置干员"安排进宿舍**（换班执行点上的布局事件；就地修改 `world`）。

    口径＝三层解耦后的**自动入宿**（见本段开头的说明与 `_seat_verdict`）：

    ```
    相 1  竖向正序填空床（候选按 心情↑、名字↑）
    相 2  队列改最小堆：取最低心情候选 → 替换"锁定区外、严格大于她、心情最大"的住户
          被换出者（心情 < 24、非黑名单）按 (心情↑, 名字↑) 插回队列
          找不到合格住户 ⇒ 立刻停（终态：锁定区外每位住户的心情都 ≥ 队列剩下的所有人）
    ```

    与 `apply_entry_events` 同一层：它改的是**布局**（谁在哪个房间），不是每小时速率，
    所以不进 `consume_ledger` / `recovery_ledger`，由调用方在**每个换班执行点**显式结算
    （`store.schedule.simulate_schedule` 会为「每个真实班次的班初 ＋ 长班的每个内部换班点」各调一次）。

    参数：
        enabled      三态；`None` = 用 `world.idle_to_dorm.enabled`（**没配置也按开**），
                     显式 `False` 才不结算
        idle         本班**没排进布局**的干员 → 心情：`{名字: 心情}`；给了才把他们当候选
        only         只处理这些干员（`None` = 全部候选）
        swap_with    **已作废**（手动点名归手动编辑逻辑）；留着只为兼容旧调用
        scope        这一刻是"第几周期的第几班" → `(周期序号, 班次序号)`（1 基）；
                     只用来取逐人设置里的"这一位参不参与"（`IdleToDormEntry.enabled`）
        trace        可选**出参**：逐位候选记一份"**轮到她的那一刻**"的宿舍态（`dorm_state`）

    ⚠️ **会就地修改 `world`**（有人进宿舍、有人被换出）。返回事件流水账（`Bucket.EVENT`）。
    ⚠️ 它**不碰手动台账**：台账只由 `store/session.py` 的编辑入口写，这里只读。
    """
    cfg = getattr(world, "idle_to_dorm", None)
    if enabled is None:
        configured = getattr(cfg, "enabled", None)
        # 默认**开**（用户口径"闲置入宿默认是开启的"）：没配置 / `None` 都按开，显式 false 才关。
        enabled = True if configured is None else bool(configured)
    if not enabled:
        return []

    from .scenario import build_operator      # 局部导入：避免模块级循环依赖

    blacklist = {str(n) for n in (getattr(cfg, "blacklist", None) or [])}
    protected_cfg = getattr(cfg, "protected_slots", None)
    protected = _protected_positions(
        world, DEFAULT_PROTECTED_SLOTS if protected_cfg is None else protected_cfg)
    cycle_no, shift_no = (scope if scope else (None, None))

    events: List[Contribution] = []
    #: 队列项 = `[心情, 名字, 干员对象, 她本来从哪来]`
    queue: List[list] = []
    #: 本执行点已经"了结"过的人（坐进去了 / 没戏了）：不再入队，防打转
    settled: set = set()

    def _enqueue(name, op, mood, where):
        """**初始候选**入队（心情满 24 / 黑名单 / 已了结过 / 已在队列里 ⇒ 都不入队）。"""
        if mood >= MOOD_MAX or name in blacklist or name in settled:
            return
        if any(row[1] == name for row in queue):
            return
        queue.append([mood, name, op, where])

    def _sort_queue():
        queue.sort(key=lambda row: (row[0], row[1]))

    def _pop():
        _sort_queue()
        return queue.pop(0)

    # ---- 初始候选（文档 §7）：该班**完全没有出现在任何设施**里 + 心情 < 24 + 非黑名单 ----
    for raw_name, mood in (idle or {}).items():
        name = str(raw_name)
        if only is not None and name not in only:
            continue
        if name in blacklist or world.get_operator(name) is not None:
            continue
        mood = to_decimal(mood)
        if mood >= MOOD_MAX:
            continue
        _enqueue(name, None, mood, "未排班")
    _sort_queue()

    def _op_for(name, op, mood):
        """候选的干员对象：被换出又回来的那位本来就在世界里；初始候选现场造一个。"""
        return op if op is not None else build_operator({"name": name, "mood": mood})

    def _skip(name, target, why):
        events.append(Contribution(
            Bucket.EVENT, "闲置入宿未执行", ZERO, group="idle_to_dorm_skipped",
            owner=name, target=target or "", detail=why))

    def _place(fac, slot, name, op, mood, where, how):
        # ⚠️ 顺序要紧：**先离开原设施、再进宿舍** —— 反过来的话"离开"会把刚放进去的人删掉。
        _leave_previous_facility(world, name)
        set_seat(fac, slot, _op_for(name, op, mood), invalidate=world.invalidate_index)
        events.append(Contribution(
            Bucket.EVENT, "闲置入宿", ZERO, group="idle_to_dorm",
            owner=name, target=fac.display_name, detail=(
                f"（{name} 心情 {mood} 未满且在闲置（{where}）→ 进 {fac.display_name} "
                f"第 {slot + 1} 位（竖向正序最靠前的空位）；{how}）")))

    def _swap(fac, slot, victim, name, op, mood, where, how):
        # 进来的人**接替被换出者的原位次**（不产生空洞）；被换出者离开宿舍 → 闲置。
        # ⚠️ 被换出者**不在本执行点里再入队**（实测：那样做出来的队列扰动永远是空转 ——
        #    换人条件保证"她心情 ≥ 候选"，而候选取自最小堆 ⇒ 她进不了队列的下一轮；
        #    她心情更低时又该留在宿舍里）。她的重新评估发生在**下一个换班执行点**：
        #    那时她已在外面、心情也变过了，会被当成普通候选重新入队（不变式见模块说明）。
        _leave_previous_facility(world, name)
        set_seat(fac, slot, _op_for(name, op, mood), invalidate=world.invalidate_index)
        events.append(Contribution(
            Bucket.EVENT, "闲置入宿", ZERO, group="idle_to_dorm",
            owner=name, target=victim.name, detail=(
                f"（{name} 心情 {mood} 未满且在闲置（{where}）→ 与 {fac.display_name} 第 "
                f"{slot + 1} 位、心情 {victim.mood} 的 {victim.name} 互换：{name} 进宿舍恢复，"
                f"{victim.name} 换出来闲置（既不工作也不在宿舍）；{how}）")))

    # ================= 相 1：填空床（竖向正序） =================
    while queue:
        mood, name, op, where = _pop()
        if trace is not None:
            # 逐位候选各留一份"此刻的宿舍态"：**在判"参与"之前**登记
            trace[name] = dorm_state(world)
        if name in blacklist:
            settled.add(name)
            continue                               # 防御性
        entry = cfg.entry_for(name, cycle_no, shift_no) if cfg is not None else None
        if entry is not None and not entry.enabled:
            settled.add(name)
            continue                               # 逐人设置：这一位不参与
        if op is not None:
            mood = op.mood                         # 用**实时**心情（她被换出去过也一样）
        if mood >= MOOD_MAX:
            settled.add(name)
            continue
        # 只信**有住户的**设施：空设施里的手动手工台账不该让"最后一个候选"被误判成"没空位"
        free = _next_free_slots(world)
        if not free:
            queue.insert(0, [mood, name, op, where])   # 没有空位 ⇒ 交棒给相 2
            break
        _key, fac, slot = free[0]
        _place(fac, slot, name, op, mood, where,
               "自动：竖向正序最靠前的空位")
        settled.add(name)

    # ================= 相 2：换人（最小堆 + 定点） =================
    while queue:
        mood, name, op, where = _pop()
        if name in blacklist:
            settled.add(name)
            continue
        if op is not None:
            mood = op.mood
        if mood >= MOOD_MAX:
            settled.add(name)
            continue
        # 换出的心情下限＝**队里"除她以外"候选的最低心情**：被换出去的人不该比还在排队的人
        # 更该进宿舍，否则她下一轮又会被换回来（来回打转）。⚠️ 不能算上她自己 ——
        # 被换出者会插回队列，用 `min(全部)` 会拿她自己的心情当门槛，把她自己挡在门外。
        others = [row[0] for row in queue if row[1] != name]
        ceiling = min(others) if others else None
        fac, slot, victim = _best_swap_victim(world, mood, protected=protected,
                                              ceiling=ceiling)
        if fac is None:
            # 定点达成：锁定区外没人比她更满（且不比排队的候选更该进宿舍）⇒ 她这一班进不去
            _skip(name, "", (
                f"（{name} 心情 {mood} 想入宿，但宿舍全满、锁定区之外没有合适的可换对象 "
                f"→ 这一班不动）"))
            settled.add(name)
            continue
        _swap(fac, slot, victim, name, op, mood, where,
              "自动：宿舍全满 ⇒ 换出锁定区外心情最小、且不低于候选的那位")
        settled.add(name)
    return events


def _leave_previous_facility(world: BaseLayout, name: str) -> None:
    """把该干员从原设施里摘掉（挂件位/宿舍都适用；不在任何设施里就什么都不做）。

    ⚠️ 摘人**留空洞、不左移**（`models.remove_occupant`）：位次是这个项目的正式概念
    （手动台账、`slots` 导出都按位次对齐），左移会让"第 3 位"变成另一个人。
    ⚠️ 它**不动手动台账**：台账只由 `store/session.py` 的编辑入口维护。
    """
    for f in world.facilities:
        if f.slot_of(name) is not None:
            remove_occupant(f, name)
            world.invalidate_index()   # 成员变了 ⇒ 名字索引作废
            return


def mood_skill_summary(op: Operator):
    """该干员的**心情技能练度摘要** → `(已解锁数, [(技能名, 需要精英, 需要等级), ...])`。

    数据来源：`skills.SKILL_EQUIPS`（键 = `(干员名, skill_id#clause)`，由上游 `operators.txt` 的
    `unlock`/`elite`/`level` 三列派生）+ `skills.SKILLS`（本项目建模的 250 条 clause）。
    同一技能多个分句只算一次（取要求最高的那次）；只统计**心情类**技能（在 `SKILLS` 里的）。

    用途：界面上解释"为什么这个人的速率和满练不一样"——例如
    `练度 E1 · 已解锁 6 条；因未满练少 2 条（「手工艺品·β」需要精英 2）`。
    """
    unlocked = 0
    locked: dict = {}
    for (name, key), eq in SKILL_EQUIPS.items():
        if name != op.name or key not in SKILLS:
            continue                        # 只统计本项目建模的心情技能
        if op.elite >= eq.unlock_elite and op.level >= eq.unlock_level:
            unlocked += 1
            continue
        sid = key.split("#")[0]
        cand = (SKILLS[key].name, eq.unlock_elite, eq.unlock_level)
        prev = locked.get(sid)
        if prev is None or (cand[1], cand[2]) > (prev[1], prev[2]):
            locked[sid] = cand
    return unlocked, sorted(locked.values(), key=lambda row: (row[1], row[2], row[0]))


def entry_event_holders(world: BaseLayout):
    """列出**可能**触发进驻事件（M15a）的干员 → `[(干员名, 所在房间名), ...]`。

    只做"模板 + 未红脸"的粗筛（真正的触发还要满足条件，如"自身为满心情"），
    供界面提示"这个布局里谁会触发换心情"、以及收集"能和谁换"的候选。

    ⚠️ 2026-10 触发条件放宽（用户裁决，见 `apply_entry_events`）：**任意设施**都算
    （不再只限宿舍）；**「不在基建」名单**里的人也算"存在"，房间名写 `不在基建`
    ——她的心情不在 `world` 里，所以"未红脸"这一条**筛不了**，只按技能槽粗筛。
    """
    out = []
    for op, facility in _entry_trigger_ops(world):
        if _template_skills(op, "M15a"):
            out.append((op.name, facility.display_name))
    for name in (world.detached or ()):
        if world.get_operator(name) is not None:
            continue                     # 她本来就在基建里（上面那一轮已经算过）
        if _template_skills(_outside_operator(world, name), "M15a"):
            out.append((name, "不在基建"))
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
def mood_ledger(world: BaseLayout, operator_name: str,
                variables: Optional[VariableLedger] = None) -> MoodLedger:
    """**完整流水账**：消耗 + 回复 + 净速率（唯一计算路径，见 ledger.py）。

    这是"让内部完全理解干员心情机制"的入口：
        lg = mood_ledger(world, "刺玫"); print(lg.explain())
    会逐条列出：谁（owner）→ 哪条技能（skill/template）→ 作用于谁 → 多少值
    → 按轴 F 哪条规则合成（同种取最高 / 跨干员取最高 / 池分配 / 归零 / 独占）。

    `variables`：**基建级变量快照**（人间烟火/热情值/无声共鸣…）。它只跟"这一刻的世界"有关、
    与"算谁"无关 ⇒ 一次算一批人时**传同一份**（见 `net_rates`），别每人重算一遍
    （实测占整轮重算 ~15%：一次 ≈15µs × 47 人 × 每次重算速率）。
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
    variables = variables if variables is not None else collect_variables(world)
    lg = consume_ledger(world, op, facility, variables)
    lg.items.extend(recovery_ledger(world, op, facility, variables).items)
    return lg


def compute_net_rate(world: BaseLayout, operator_name: str,
                     variables: Optional[VariableLedger] = None) -> Decimal:
    """干员的净消耗速率（点 / 时）。>0 心情下降，<0 心情上升。

    `variables` 同 `mood_ledger`（算一批人时传同一份，见 `net_rates`）。
    """
    op = world.get_operator(operator_name)
    if op is None:
        raise KeyError(f"基建布局中不存在干员：{operator_name}")
    if world.facility_of(operator_name) is None:
        return ZERO   # 不在任何设施内，视为既不消耗也不回复
    return mood_ledger(world, operator_name, variables).net_rate()


def net_rates(world: BaseLayout, names: Optional[Sequence[str]] = None) -> Dict[str, Decimal]:
    """**一次算一批人的净速率**（`names` 缺省＝全基建所有人）。

    存在的意义只有一个：`collect_variables(world)` 是**世界级**的量（与"算谁"无关），
    一次算一批人时共享同一份快照，而不是每人重算一遍 —— 示例排班实测**整轮重算快 1.3×**。
    ⚠️ 快照跟 `world` 的**成员与心情**绑定：世界一变（换人 / 改心情）就得重新收集，
    所以只在"同一个世界快照、连续算一批人"的场景里用。
    """
    if names is None:
        names = [o.name for o in world.all_operators()]
    variables = collect_variables(world)
    return {n: (compute_net_rate(world, n, variables) if world.get_operator(n) is not None else ZERO)
            for n in names}


def remaining_mood_after(world: BaseLayout, operator_name: str, hours,
                         variables: Optional[VariableLedger] = None) -> Decimal:
    """目标时段（hours 小时）结束后，该干员的剩余心情（安时积分，钳位到 [0,24]）。

    注：假设时段内基建布局不变、且其余干员心情无限（即速率恒定）。
    若时段内该干员心情归零，其后仍为 0（红脸继续工作只是效率下降，心情不再减少）。
    """
    op = world.get_operator(operator_name)
    if op is None:
        raise KeyError(f"基建布局中不存在干员：{operator_name}")
    net = compute_net_rate(world, operator_name, variables)
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
    """single 模式：一次性测算目标干员在指定时段结束后的状态汇总。

    ⚠️ 入口先固定**本线程**的 Decimal 上下文（线程局部；见 `config.use_project_decimal_context`）。
    """
    use_project_decimal_context()
    op = world.get_operator(operator_name)
    if op is None:
        raise KeyError(f"基建布局中不存在干员：{operator_name}")
    facility = world.facility_of(operator_name)

    variables = collect_variables(world)       # 下面三处共用同一份（见 `net_rates`）
    net = compute_net_rate(world, operator_name, variables)
    remaining = remaining_mood_after(world, operator_name, period_hours, variables)
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
        ledger=mood_ledger(world, operator_name, variables),
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
    ⚠️ 入口先固定**本线程**的 Decimal 上下文（线程局部；见 `config.use_project_decimal_context`）。
    """
    use_project_decimal_context()
    rates = net_rates(world)         # 一次算全基建（共享变量快照，见 `net_rates`）
    entries = []   # (name, facility_label, mood, net, 个体到红脸时长)
    for op in world.all_operators():
        facility = world.facility_of(op.name)
        net = rates.get(op.name, ZERO)
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

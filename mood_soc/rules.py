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
    ALL_WORKPLACE_FACILITIES,
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
from .models import normalize_entry_when
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
    """干员在布局里的 (设施, 下标)；不在布局里返回 (None, -1)。"""
    for f in world.facilities:
        for i, o in enumerate(f.operators):
            if o is op:
                return f, i
    return None, -1


def _swap_positions(world: BaseLayout, a: Operator, b: Operator) -> str:
    """把两名干员的**位置**互换（就地）。返回人类可读说明（换不了时为空串）。"""
    fa, ia = _position_of(world, a)
    fb, ib = _position_of(world, b)
    if fa is None or fb is None:
        return ""
    fa.operators[ia], fb.operators[ib] = fb.operators[ib], fa.operators[ia]
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
    | `swap_with` = 人名 | 在 `scope` 允许范围内找那个人（`dorm` 限同宿舍 / `anywhere` 全基建） |
    | `swap_with` = `any` / `任意` / `最累` | **自动挑全基建心情最低的那位**（并列取先出现的） |
    | 没给 `swap_with` 且 `scope="anywhere"` | 同上（自动挑最累的） |
    | 没给 `swap_with` 且 `scope="dorm"` | 默认「**前一位进驻**」（同宿舍里排在触发者之前的那位） |
    """
    name = (swap_with or "").strip()
    kind = entry_target_kind(swap_with, scope)
    if kind == "default":
        idx = facility.operators.index(holder)
        if idx == 0:
            return None, "（同一宿舍里没有「前一位进驻」的干员）"
        return facility.operators[idx - 1], ""
    if kind == "auto":
        pool = [o for o in world.all_operators() if o is not holder]
        if not pool:
            return None, "（基建里没有别的干员可以换）"
        best = min(pool, key=lambda o: o.mood)
        return best, f"（自动挑：全基建心情最低的是 {best.name} {best.mood}）"
    pool = ([o for o in world.all_operators() if o is not holder]
            if scope == "anywhere" else [o for o in facility.operators if o is not holder])
    for o in pool:
        if o.name == name:
            return o, ""
    where = "基建内" if scope == "anywhere" else facility.display_name
    return None, f"（指定的交换对象「{name}」不在{where}）"


def apply_entry_events(world: BaseLayout, swap_with=None, enabled=None,
                       scope=None, restore_back=None, when=None):
    """**进驻瞬间的一次性结算**（M15a 心情互换），就地修改 `world` 的干员心情。

    为什么单独一个入口：这类技能的效果不是「每小时 ±N 点」，而是**进驻那一刻的状态跳变**，
    所以它既不该进 `consume_ledger`/`recovery_ledger`（那不是速率），也不该进时间积分。
    它是**布局初始化**语义，因此做成显式 API，由调用方决定是否应用（`main.py --entry-events`）。

    ⚠️ **会就地修改** `world`（干员心情；`restore_back=False` 时还包括位置）；
    返回本次结算的事件流水账（`Bucket.EVENT`），供 `--explain` 展示。

    已实现：**患难之交**（菲亚梅塔，`dorm_exchangeAp[000]`）
      上游原文：「进驻宿舍时，如果**自身为满心情**，则与当前宿舍**前一位进驻**的干员互换心情」。
      本项目在此之上做了可配置扩展（用户需求）：

    | 参数 | 取值 | 优先级 |
    |---|---|---|
    | `enabled` | `True`/`False` 强制结算/不结算；`None` 看 `world.entry_events.enabled` | 显式 > JSON > 默认结算 |
    | `swap_with` | 人名 / `any`（自动挑最累的）/ `None`（用 JSON，再没有＝「前一位进驻」） | 显式 > JSON > 默认前一任 |
    | `scope` | `"dorm"`（限同宿舍）/ `"anywhere"`（**基建任意位置**） | 显式 > JSON > 默认 dorm |
    | `restore_back` | `True`（默认）= 只换心情、两人都留在原位置；`False` = **位置也一起互换** | 显式 > JSON > 默认 True |
    | `when` | **什么时候换**：`"immediate"`（默认，**强制立刻换**：不管她满不满、也不管对方心情是多少）/ `"wait"`（等她回满再换）/ `"full"`（只在她满心情时换，游戏原口径） | 显式 > JSON > 默认 immediate |

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
    for facility in world.facilities:
        if facility.ftype != FacilityType.DORMITORY:
            continue                       # 触发者必须"进驻宿舍"（技能原文），目标才不限位置
        for op in facility.operators:
            if not _active(op) or getattr(op, "entry_swapped", False):
                continue                   # 同一份布局快照里只结算一次（重复调用幂等）
            for skill in _template_skills(op, "M15a"):
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
    return events


def reset_entry_events(world: BaseLayout) -> int:
    """把"这一份布局快照已结算过进驻事件"的标记**归位**（返回归位的干员数）。

    为什么需要它：`apply_entry_events` 用 `Operator.entry_swapped` 保证**同一份快照只结算一次**
    （重复调用幂等）。而"**再次进驻**"是另一回事——同一个布局副本被**跨班次 / 跨周期复用**时
    （`ui.schedule.simulate_schedule` 就是复用每个班次的副本），每一个班次开始都是一次**新的
    进驻瞬间**，必须重新判定。所以那个入口在每次"进驻那一刻"之前调用本函数。

    语义边界（免得被误用）：它**不改心情、不改位置**，只清标记；一次性结算（`main.py --entry-events`
    那样只跑一次的场景）不需要它，也就保持了"重复调用不出二次效果"的原有保证。
    """
    n = 0
    for op in world.all_operators():
        if getattr(op, "entry_swapped", False):
            op.entry_swapped = False
            n += 1
    return n


def _idle_candidates(world: BaseLayout, idle=None, only=None):
    """「闲置入宿」的候选 → `[(Operator | 名字, 心情, 原位置)]`，**先"不在工作也不在宿舍"的人**，
    再挂件位入驻者；**每组内部按心情从低到高**。

    候选 = **不在宿舍** 且 **不消耗心情** 且 **心情没满**：

    | 原位置 | 是否算候选 | 排序归属 |
    |---|---|---|
    | 宿舍 | ✗（已经在恢复） | — |
    | 消耗心情的工作设施（制造/贸易/发电/中枢/会客/办公） | ✗（正在上班，不动） | — |
    | **本班未排班**（不在布局里，心情由 `idle` 传入）/「不在基建」名单 | ✓ | **第一组**（"不在工作也不在宿舍"） |
    | **挂件位**（加工站 / 训练室） | ✓（不消耗也不回复，心情一直卡着） | 第二组 |

    ⚠️ **候选是"依次"处理的**（每人依次找空位 / 找换人对象），所以顺序决定"谁先拿到空位、
    谁先挑换人对象"。**用户口径（2026-09）：把"不在工作也不在宿舍"的人排在前面**——
    他们是真的没位置、心情就卡在那里；挂件位的人至少"人在基建内"还能给别人提供效果。
    每组内部仍是**心情从低到高**（最需要恢复的先安排）。

    `idle`：`{干员名: 心情}`——本班次**没排进布局**的干员（他们的心情在布局里看不到，
    只能由调用方给）。`only`：只处理这些人（界面勾了"参与"的），`None` = 全都算。
    """
    from .config import FacilityType as _FT

    out: list = []
    for op in world.all_operators():
        fac = world.facility_of(op.name)
        if fac is not None and fac.ftype == _FT.DORMITORY:
            continue                                     # 已经在宿舍
        if fac is not None and fac.ftype not in (_FT.WORKSHOP, _FT.TRAINING):
            continue                                     # 正在上班（会消耗心情）
        if op.mood >= MOOD_MAX:
            continue                                     # 满心情，不需要恢复
        if only is not None and op.name not in only:
            continue
        out.append((op, op.name, op.mood, fac.display_name if fac else "未排班"))
    for name, mood in (idle or {}).items():
        if world.get_operator(name) is not None:
            continue                                     # 已经在布局里（上面处理过了）
        mood = to_decimal(mood)
        if mood >= MOOD_MAX:
            continue
        if only is not None and name not in only:
            continue
        out.append((None, name, mood, "未排班"))
    # 排序键：① 是不是"不在工作也不在宿舍"（`op is None` ⇒ 来自 `idle`）② 心情 ③ 名字。
    out.sort(key=lambda row: (0 if row[0] is None else 1, row[2], row[1]))
    return out


# ----------------------------------------------------------------------------
# 「闲置入宿」的四级优先级（用户口径，见 `apply_idle_to_dorm`）
#
#   ① 有空位 → 直接住进去（不换人）
#   ② 宿舍全满 → 换「宿舍#2~#4 的第 2~5 位」里**实时满心情**的那位
#   ③ 还找不到 → 只在**"吃不到阵营联动"的人**里挑满心情的那位：白板，或阵营在**工作区**
#                里没有同伴的人（`_faction_protected`）；＋满 24 的菲亚梅塔（`TIER3_EXTRA_NAMES`）
#   ④ 都不满足 → 有点名 → 与你点名的那位"主动换"（不限心情）；
#                没点名 → 与宿舍里**心情最高**的那位（同上"吃不到联动"口径）换（不限 24）；
#                连这样的都没有才"这一班不动"
#
# ②③④ 的自动换人还都**跳过"挂件"**（`_is_pendant`：她一走别人就要吃亏；
#   用户口径"有阵营效果，或者她在不在宿舍会影响其他干员的技能"）；点名不受限。
# ⚠️ "自回型不被换出"那道门**已取消**（用户裁决 2026-09"在宿舍的自回型不进行门保护"）。
# 任何互换（含点名）都还要过一道闸：**目标的实时心情必须严格大于你**（用户口径）——
#   否则等于把更需要恢复的人挤出去；②③ 要求目标满 24、候选必定 <24 ⇒ 实际只在 ④ 生效。
#
# **同优先级内的顺序：优先 4、最后 1** ——
#   宿舍之间：`#4 → #3 → #2 → #1`；同一宿舍内：第 2 位 → 第 5 位 → 第 1 位。
# 为什么：用户指定"优先 4 最后 1"（最远的宿舍#4 当蓄水池，主力宿舍#1 最后动）；
#   位次 1 排在同宿舍最后，是因为 ② 的点名范围本来就只含"第 2~5 位"。
# ----------------------------------------------------------------------------
#: ② 的点名范围：这些**宿舍序号**（1 基、按布局里的出现顺序，与界面「宿舍NN」一致）
DORM_PREFERRED_RANGE = (2, 3, 4)
#: ② 的点名**位次**（1 基；第 1 位不在其中，第 1 位只在 ③ 兜底时才轮到）
DORM_PREFERRED_SLOTS = (2, 3, 4, 5)
#: ③ 兜底层**除"吃不到联动的白板"外**额外放行的人（用户裁决）：满 24 心情的**菲亚梅塔**。
#: 为什么放她：她的价值全在「患难之交」（M15a）——**进驻宿舍那一刻**把心情换给上一位，
#: **满 24 就够、不靠"待在宿舍里"**；她「自律」（M14）又能自己回满，
#: 所以满心情时把她当"备用容量"换出去不亏（换出那一刻她已是满的）。
#: ⚠️ 仍要求**满 24**（`_pickable` 那道闸照旧），只放开"阵营门"这一道；
#: ⚠️ 名字写死是用户口径（当前 M15a 只有她一个持有者）。
TIER3_EXTRA_NAMES: Tuple[str, ...] = ("菲亚梅塔",)

#: **「阵营门」的工作区**（用户口径 2026-09）：只有出现在这些房间里的阵营才算"吃得到联动" ——
#: **在宿舍里休息的同伴不算**（她人不在工作区）。＝官方「工作场所」（`ALL_WORKPLACE_FACILITIES`）
#: **去掉训练室**（本项目把加工站/训练室当挂件位、不消耗心情；用户口径"只算消耗心情的工作设施"）
#: ⇒ 控制中枢 / 制造站 / 贸易站 / 发电站 / 会客室 / 办公室。活动室本来就不进心情模型。
#: 判据见 `_faction_protected`：她的阵营在工作区里**一个同伴都没有** ⇒ 当白板、可被换出。
FACTION_WORK_TYPES: Tuple[FacilityType, ...] = tuple(
    t for t in ALL_WORKPLACE_FACILITIES if t is not FacilityType.TRAINING)


def _factionless(op: Operator) -> bool:
    """她是不是**「白板」**（不属于**任何已记录阵营**）？

    判据：`OPERATOR_FACTIONS`（由 `data/factions.txt` + 人工补充表生成）里**没有她的名字**，
    且她自己也没有 `factions` / `trait` 之类的额外标注。
    ⚠️ "已记录"＝**数据表的全集**（那两张表里出现过的每一个阵营），
    与"这一刻基建里有没有该阵营的人"无关。

    用途：**静态白板刻画**（谁压根没有阵营）= `_faction_protected` 的快路径，
    也是文档/术语里"白板"的定义。**它本身不是门** —— 阵营门现在看的是
    "她的阵营在**工作区**里还有没有同伴"（`_faction_protected`，用户口径 2026-09）。
    """
    return not _factions_of(op)


def _dorm_order(world: BaseLayout) -> List[Facility]:
    """全部**可用**宿舍，按"优先 4 最后 1"排好序（`#4 → #3 → #2 → #1`，不足 4 间则前移）。

    宿舍序号 = 它是布局 `facilities` 里的第几间宿舍（1 基）—— 与界面「宿舍NN」一致。
    ⚠️ 排序**不是**"按氛围"：氛围只影响恢复快慢，与"先动哪一间"无关（用户口径）。
    """
    return [f for _no, f in _dorm_numbered(world)[::-1]]


def _dorm_numbered(world: BaseLayout) -> List[tuple]:
    """→ `[(宿舍序号（1 基）, 设施), ...]`，**按布局里的出现顺序**。

    ⚠️ 序号必须从"未被排序的原始顺序"里取（曾经拿排序后的列表下标当序号，
    于是"优先 #4"挑中的其实是布局里的第 1 间，换人换错了房间）。
    """
    from .config import FacilityType as _FT

    dorms = [f for f in world.facilities if f.ftype == _FT.DORMITORY and f.enabled]
    return list(enumerate(dorms, start=1))


def dorm_state(world: BaseLayout) -> dict:
    """**这一刻的宿舍态**（只收名字）：`{"dorms": {宿舍序号: [名字…]}, "free": [还空着的序号…]}`。

    谁在用：`apply_idle_to_dorm` 的 `trace`（**逐位候选**各留一份）与界面「闲置入宿」逐次表。
    面板里每一行的「换谁 / 宿舍NN」必须按**轮到她的那一刻**算 —— 候选是**依次**处理的，
    排前面的人会把宿舍里的人换出去：用户报过"面板里列着清流、那一刻宿舍里并没有清流"
    （那是拿**排班快照**当引擎世界使的结果）。
    序号口径与 `_dorm_numbered` 相同（可用宿舍在布局里的出现顺序，1 基）。
    """
    dorms: dict = {}
    free: List[int] = []
    for no, dorm in _dorm_numbered(world):
        dorms[no] = [o.name for o in dorm.operators]
        if len(dorm.operators) < dorm.capacity:
            free.append(no)
    return {"dorms": dorms, "free": free}


def _faction_protected(world: BaseLayout, op: Operator, memo: Optional[dict] = None) -> bool:
    """**她该被"阵营门"保护吗？** → `True` = 自动换人不碰她。

    用户口径（2026-09）：**阵营门只在"工作区里有同阵营的干员"时才保护** ——
    孤家寡人的阵营（这座基建里没有同伴）本来一点联动都吃不到，把她当**白板**看待、
    可以自动换出去；有同伴的照旧留在宿舍。

    | 条件 | 结果 |
    |---|---|---|
    | 她是**白板**（`_factionless`：不属于任何已记录阵营） | `False`（没有阵营要保护） |
    | 她的**任一个**阵营在工作区里有**别人** | `True`（保护、不换出） |
    | 一个同伴都没有（同伴只在宿舍休息 / 只在加工站·训练室 / 根本没有） | `False`（当白板） |

    **工作区**＝`FACTION_WORK_TYPES`（控制中枢 / 制造站 / 贸易站 / 发电站 / 会客室 / 办公室）：
    **在宿舍里休息的同伴不算**（她人不在工作区，联动吃不到）；加工站/训练室（挂件位）与活动室也不算。
    ⚠️ 判据用**实时**世界（每班开始时那一刻谁在哪）。`memo` 是同一个班次内的缓存
    （与挂件判据共用一份，换过人 ⇒ 位置变了 ⇒ 调用方一起清空）。
    """
    if _factionless(op):
        return False                     # 白板：没有阵营可保护
    mine = set(_factions_of(op))
    if not mine:
        return False
    key = "__work_factions__"
    work = memo.get(key) if memo is not None else None
    if work is None:
        work = set()
        for other in world.all_operators():
            fac = world.facility_of(other.name)
            if fac is not None and fac.ftype in FACTION_WORK_TYPES:
                work.update(_factions_of(other))
        if memo is not None:
            memo[key] = work
    return bool(mine & work)


def _dorm_with_free_slot(world: BaseLayout):
    """**第 ① 级**：还有未占满位次的宿舍（"优先 4 最后 1"）；都没有就返回 `None`。

    为什么"有空位"排在换人之前：换人会**打断一个正在恢复的人**，而空位是白捡的。
    """
    for dorm in _dorm_order(world):
        if len(dorm.operators) < dorm.capacity:
            return dorm
    return None


def _swap_mate(world: BaseLayout, exclude: Optional[set] = None,
               memo: Optional[dict] = None):
    """挑一个「宿舍里**实时心情已满**」的干员互换 → `(宿舍, 干员, 第几优先级)`。

    两级回退（顺序即优先级）：

    | 级 | 范围 | 说明 |
    |---|---|---|
    | `2` | 宿舍 `#4 → #3 → #2` 的**第 2~5 位** | 点名范围；每间内部按第 2 位→第 5 位 |
    | `3` | 宿舍里**其余任何位置**（含宿舍#1 的全部位次、以及上面那些宿舍的第 1 位） | 兜底 |

    两级都**跳过"挂件"**（`_is_pendant`：她一走别人就要吃亏）；
    ③ 另有**阵营门**（`_faction_protected`，见那一级）与例外名单。
    ⚠️ "自回型不被换出"那道门**已取消**（用户裁决 2026-09）。

    都找不到 → `(None, None, None)`，调用方走第 ④ 级"这一班不动"。

    ⚠️ **必须用"调用那一刻"的实时心情**（`op.mood`）：调用方在班次开始时已经
    `_sync_moods`，而且**先把进驻事件（换心情）结算完**才走到这里。
    `memo` 是同一个班次内的"挂件判据"缓存（见 `_is_pendant`）。
    """
    dorms = _dorm_order(world)
    skip = exclude or set()
    by_no = dict(_dorm_numbered(world))                      # 宿舍序号（1 基）→ 设施

    def _pickable(dorm, op) -> bool:
        """这个人此刻**真的还在**这间宿舍里、没被换出去、实时满心情、**且不是"挂件"**？

        ⚠️ 必须查 `world.facility_of`：`dorm.operators` 是"跑了一整轮模拟"的那个副本，
        可能残留**已经不在基建里**的陈旧对象（她早先被换出去过）。只比对成员列表的话，
        会挑到一个不在宿舍里的人来换 —— 换了个寂寞（`dorm.operators[idx] = op` 改的是
        一个早已不在世界里的槽位）。
        ⚠️ `_is_pendant`（用户口径"先选不是挂件"）：她一走别人就要吃亏（见那个函数）——
        ②③ 也一样不换她，顺位找下一位。**点名**（`_named_mate`）不受这道闸限制。
        ⚠️ **没有"自回型不被换出"这道门了**（用户裁决 2026-09："在宿舍的自回型不进行门保护"）：
        自回型（菲亚梅塔「自律」、缪尔赛思「天生丽质」这类）现在**会**被 ②③ 换出去。
        """
        if op.name in skip or op.mood < MOOD_MAX:
            return False
        here = world.facility_of(op.name)      # 便宜的先判（名字索引 O(1)）：陈旧对象/已换出去
        if here is None or here is not dorm:
            return False
        if _is_pendant(world, op.name, memo):  # 贵的一步（全基建净速率探针）放最后
            return False
        return True

    # --- 第 ② 级：宿舍 #4 → #3 → #2 的第 2~5 位 ---
    for no in sorted(DORM_PREFERRED_RANGE, reverse=True):
        dorm = by_no.get(no)
        if dorm is None:
            continue
        for slot in DORM_PREFERRED_SLOTS:                    # 位次（1 基）
            if slot - 1 >= len(dorm.operators):
                break
            op = dorm.operators[slot - 1]
            if _pickable(dorm, op):
                return dorm, op, 2

    # --- 第 ③ 级：兜底**只换"吃不到联动"的人**（白板，或阵营在工作区里没有同伴的人）；
    #     一个都没有就不换。顺序同"优先 4 最后 1"；每间先第 2~5 位，再第 1 位。
    #     例外：`TIER3_EXTRA_NAMES`（满 24 的菲亚梅塔）——她待在宿舍没价值，换出去不亏。
    for dorm in dorms:
        for slot in list(DORM_PREFERRED_SLOTS) + [1]:
            if slot - 1 >= len(dorm.operators):
                continue
            op = dorm.operators[slot - 1]
            extra = op.name in TIER3_EXTRA_NAMES
            if (extra or not _faction_protected(world, op, memo)) and _pickable(dorm, op):
                return dorm, op, 3
    return None, None, None


def _is_pendant(world: BaseLayout, name: str, memo: Optional[dict] = None) -> bool:
    """她是不是**"挂件"**（她一走，别人就要吃亏）？

    用户口径（原话）：**"挂件指的就是有阵营效果，或者她在不在宿舍会影响其他干员的技能"**。
    拆成三条：

    | 类 | 判据 | 落在哪道闸 |
    |---|---|---|
    | **有阵营效果** | `_faction_protected(world, op)` 为真（她的阵营在**工作区**里有同伴） | "阵营门"（③④；② 不看阵营） |
    | **影响别人的技能** | 把她从宿舍摘掉 → **全基建**有人的净速率**变差** | 本函数 |
    | **位置上的挂件** | 加工站 / 训练室入驻者、副手（不占正式位次、只为提供效果而在场） | 本函数 |

    "变差" ＝ `compute_net_rate` 变大（消耗上升 / 回复下降）。**只看变差这一侧**，是为了
    自动排除"她走了别人反而更爽"的假阳性 —— 典型是冰酿的池分摊：她走了分母少一个，
    每人分得更多（那种不算挂件，见用户裁决）。

    这一条能覆盖的机制：她把效果送给别人（宿舍群体 / 单体 / 定向回复、M17 元修正），
    以及她的在场让别人的技能条件成立（"宿舍内每有 1 名干员"类中间货币、同设施点名条件）。
    ⚠️ 反过来"她在场把别人的技能压住了"（如会客室「只有自身工作时」）**不算**挂件。

    ⚠️ 判据用**调用那一刻**的实时心情：先是全基建各算一遍净速率，再在一份**浅拷贝探针**
    （`_world_without`，把她从 `fac.operators` 里摘掉＝真被换出后的状态）上重新逐人比对。
    **原世界一个字节都不改** —— 调用点正遍历着 `dorm.operators`，就地增删太脆弱。
    ⚠️ `memo` 是**同一个班次内**的缓存（`apply_idle_to_dorm` 建一次、换过人后清空）。
    2026-09 起那张"摘人**之前**的全基建速率表"也挂在 `memo` 上共享（`_all_rates`）：
    原来"每评一个人就把 47 个速率重算一遍"，而"除她以外所有人的速率"只差她自己那一项
    ⇒ 一张表就够（实测 7 周期重算里判据占 41% 的耗时，共享后砍掉约一半）。
    """
    if memo is not None and name in memo:
        return memo[name]
    verdict = _is_pendant_uncached(world, name, memo)
    if memo is not None:
        memo[name] = verdict
    return verdict


def _all_rates(world: BaseLayout, memo: Optional[dict] = None) -> Dict[str, Decimal]:
    """**这一刻全基建每人的净速率**（挂件判据的共享底表）。

    为什么共享：判据要看"把她摘掉之后有没有人变差"，而"除她以外所有人的速率"在
    **同一个世界快照**里是同一张表 —— 原先每个被评的人都要重算一遍（47 人 × 2 遍），
    现在一张表 + 每次只做"摘掉她"那一遍。
    ⚠️ 有效期＝**世界没变**：表挂在 `memo` 上，换人（位置变了）时随 `memo.clear()` 一起失效；
    心情在同一轮闲置入宿里也不变（调用方进循环前 `_sync_moods` 过一次）。
    """
    cached = memo.get(_ALL_RATES_KEY) if memo is not None else None
    if cached is None:
        cached = {o.name: compute_net_rate(world, o.name) for o in world.all_operators()}
        if memo is not None:
            memo[_ALL_RATES_KEY] = cached
    return cached


#: `memo` 里存共享速率表的键（干员名不会长这样，不会与"逐人判据缓存"撞键）
_ALL_RATES_KEY = "\x00all_rates"


def _is_pendant_uncached(world: BaseLayout, name: str, memo: Optional[dict] = None) -> bool:
    """`_is_pendant` 的实际计算（不带缓存）；见那里的口径说明。"""
    fac = world.facility_of(name)
    if fac is None:
        return False
    if fac.ftype in (FacilityType.WORKSHOP, FacilityType.TRAINING):
        return True                          # 挂件位：本身就是"只为提供效果而在场"
    if any(o.name == name for o in fac.deputies):
        return True                          # 副手不占位次，同上
    if fac.ftype != FacilityType.DORMITORY:
        return False                         # 只对"宿舍成员被换出"这件事下判断

    before = _all_rates(world, memo)
    probe = _world_without(world, fac, name)   # 摘掉她 = 真被换出后的状态
    for other, old in before.items():
        if other == name:
            continue                         # 她自己不在"别人"里
        if compute_net_rate(probe, other) > old:
            return True                      # 有人变差 ⇒ 她走了别人吃亏 ⇒ 她是挂件
    return False


def _world_without(world: BaseLayout, fac: Facility, name: str) -> BaseLayout:
    """**不动原世界**的一份探针：浅拷贝世界，并把 `name` 从那间设施里摘掉。

    ⚠️ 为什么不用"就地摘掉、算完再放回去"：`_is_pendant` 的调用点正在**遍历
    `dorm.operators`**（`_fallback_mate`），就地增删那个列表属于"边遍历边改"，
    能跑但极其脆弱。浅拷贝只复制两层壳（世界 + 那一间设施），代价可忽略，
    设施与干员对象本身照旧共享（判据只读它们）。
    """
    import copy as _copy                 # 局部导入：本模块只有这一处需要

    probe = _copy.copy(world)
    probe.facilities = list(world.facilities)
    probe.invalidate_index()             # 浅拷贝会把索引**引用**带过来 ⇒ 必须丢掉（探针改了成员）
    idx = next((i for i, f in enumerate(probe.facilities) if f is fac), None)
    if idx is None:
        return probe
    clone = _copy.copy(fac)
    clone.operators = [o for o in fac.operators if o.name != name]
    probe.facilities[idx] = clone
    return probe


def _fallback_mate(world: BaseLayout, exclude: Optional[set] = None,
                   memo: Optional[dict] = None):
    """**第 ④ 级的默认兜底**（用户口径）：自动 ②③ 都挑不到**满 24** 的人时，
    在宿舍里挑**心情最高**的那位互换 —— **不要求满 24**（例：宿舍里最高只有 21，就跟 21 那位换）。

    仍然守两道门（用户口径"守"）：

    | 门 | 判据 |
    |---|---|
    | **阵营门** | `_faction_protected(world, op, memo)`：白板，或**阵营在工作区里没有同伴**的人 ⇒ 可换；工作区里有同阵营同伴的 ⇒ 不换（与 ③ 同一道门） |
    | **排除挂件** | `_is_pendant(world, name, memo)`（她一走别人就要吃亏：给别人送效果 / 让别人条件成立 / 加工站·训练室·副手那类位置挂件 —— 见那个函数） |

    ⚠️ **"自回型不被换出"那道门已取消**（用户裁决 2026-09："在宿舍的自回型不进行门保护"）——
    自回型（菲亚梅塔「自律」、缪尔赛思「天生丽质」这类）现在照换。
    ⚠️ 判据用 `_faction_protected` 而不是 `_factionless`（静态白板）：**阵营门只看工作区**，
    在宿舍里休息的同伴不算（见那个函数）。

    另外仍要求她**此刻真的还在宿舍里**（`facility_of` 校验，防"跑过一轮的副本"里的陈旧对象）。

    排序：**心情从高到低**（换出损失最小的先换），同心情按名字。
    返回 `(宿舍, 干员)`；一个都没有 → `(None, None)`（调用方走"这一班不动"）。
    ⚠️ 这里**不做**"目标必须比你更满"那道闸（它要拿候选人的心情比）：那是调用方在
    真正换人之前的统一检查（见 `apply_idle_to_dorm` 的"心情闸"）——本函数挑的是
    宿舍里心情最高的白板，若连她都不比候选更满，别人更不可能满足。
    """
    skip = exclude or set()
    best = None
    for dorm in _dorm_order(world):
        for op in dorm.operators:
            if op.name in skip or op.mood >= MOOD_MAX:
                continue                     # 满 24 的留给 ②③（那里还要看位次与点名范围）
            if _faction_protected(world, op, memo):
                continue                     # 阵营门：工作区里有同阵营同伴的留在宿舍
            if _is_pendant(world, op.name, memo):
                continue
            if world.facility_of(op.name) is not dorm:
                continue                     # 陈旧对象 / 已经不在这间宿舍
            key = (-op.mood, op.name)
            if best is None or key < best[0]:
                best = (key, dorm, op)
    return (best[1], best[2]) if best else (None, None)


def _named_mate(world: BaseLayout, name: str, exclude: Optional[set] = None):
    """**你点名**要互换的那位 → `(宿舍, 干员)`；不在宿舍里就返回 `(None, None)`。

    ⚠️ 与自动路径（②③）**刻意不同**：这里**不要求她满 24**、也不受"阵营门 /
    挂件门"限制 —— 这是**主动换**（用户显式指定，代价由用户承担，他的口径是
    "因为是主动换，所以可以选心情没满的在宿舍的人"）。仍然要求：
      - 她**此刻真的在某一间宿舍里**（`facility_of` 校验，防陈旧副本对象）；
      - 本班次还没被换出去过（`exclude`）；
      - **她的实时心情严格大于候选**（那道"心情闸"由调用方判，见 `apply_idle_to_dorm`）。
    """
    skip = exclude or set()
    if not name or name in skip:
        return None, None
    for f in world.facilities:
        if f.ftype != FacilityType.DORMITORY:
            continue
        if not any(o.name == name for o in f.operators):
            continue
        if world.facility_of(name) is f:          # 真的还在这间（不是陈旧对象）
            for o in f.operators:
                if o.name == name:
                    return f, o
    return None, None


def apply_idle_to_dorm(world: BaseLayout, enabled=None, idle=None, only=None,
                       swap_with=None, scope=None, trace: Optional[dict] = None) -> List[Contribution]:
    """**把"未满心情的闲置干员"安排进宿舍**（班次开始时的布局事件；就地修改 `world`）。

    与 `apply_entry_events` 同一层：它改的是**布局**（谁在哪个房间），不是每小时速率，
    所以同样不进 `consume_ledger` / `recovery_ledger`，由调用方在"班次开始"显式结算
    （CLI `--idle-to-dorm`、界面上的开关、或 `ui.schedule.simulate_schedule`）。

    规则（用户口径，**四级优先级 ＋ 一条候选顺序**）：

    | 级 | 条件 | 动作 |
    |---|---|---|
    | **候选顺序** | —— | **先"不在工作也不在宿舍"的人**（本班未排班 /「不在基建」名单），**再**挂件位（加工站/训练室）入驻者；每组内部**心情从低到高**。候选是**依次**处理的 ⇒ 这决定"谁先拿到空位 / 谁先挑换人对象" |
    | ① | 任一间宿舍**还有未占满的位次** | **直接住进去**（有空位就不换人）；顺序"优先 4 最后 1" |
    | ② | 宿舍**全满** | 换「宿舍 **#4 → #3 → #2** 的**第 2~5 位**」里**实时心情已满**的那位 |
    | ③ | ②找不到 | 只在**"吃不到阵营联动"的人**里挑实时满心情的那位：**白板**，或阵营在**工作区**（中枢/制造/贸易/发电/会客/办公）里**没有同伴**的人（`_faction_protected`）；**满 24 的菲亚梅塔**也在此列（见 `TIER3_EXTRA_NAMES`） |
    | ④ | 都找不到 | **有点名**（`swap_with`）→ 与**你点名**的那位互换（**主动换**，不限心情）；**没点名** → 与宿舍里**心情最高**的那位（同上"吃不到联动"口径）互换（**不限 24**，例：最高 21 就换 21 那位）；连这样的都没有才"这一班不动"（记 `idle_to_dorm_skipped`） |
    | **心情闸** | 任何互换（**含点名**）都要求**目标的实时心情严格大于你**（用户口径"换的时候比较此干员与目标干员的心情，如果目标干员心情大于此干员才交换"）；不满足 → **这一班不动**并写明两边心情。②③ 的目标是实时满 24、候选必定 < 24 ⇒ 天然满足，所以**实际只在 ④ 生效** |

    - **同优先级内的顺序：优先 4、最后 1** —— 宿舍 `#4 → #3 → #2 → #1`；
      同一宿舍内 第 2 位 → 第 5 位 → 第 1 位（位次 1 只在③兜底时轮到）。
      ④默认兜底则**按心情从高到低**挑（换出损失最小的先换），宿舍顺序只用于同分。
    - **"阵营门"**＝`_faction_protected(world, op, memo)`（用户口径 2026-09）：**她的阵营在
      这座基建的"工作区"里还有同伴吗** ——
      · 有 ⇒ **保护**她（自动换人不碰）：联动吃得到，换出去会连带打断别人的阵营加成；
      · 没有（白板 / 孤家寡人的阵营）⇒ 当她**白板**、可以换出。
      "工作区"＝`FACTION_WORK_TYPES`（中枢/制造站/贸易站/发电站/会客室/办公室）——
      **在宿舍里休息的同伴不算**（她人不在工作区，联动吃不到）；加工站/训练室、活动室也不算。
      某人若有多个阵营，**任一个**阵营在工作区有同伴即受保护。**② 不看这道门**（它只看位次与满 24）。
      例外：**满 24 的菲亚梅塔**（③ 也放行）—— 她的价值在「患难之交」（M15a）**进驻那一刻**，
      满 24 就够，不靠"待在宿舍里"。
    - **"挂件"**＝`_is_pendant(world, name, memo)`：用户口径"**有阵营效果，或者她在不在宿舍
      会影响其他干员的技能**"。前半句由上面那道"阵营门"管（工作区里有同伴 ⇒ 阵营有价值）；
      后半句由 `_is_pendant` 现场判：**把她从宿舍摘掉 → 全基建有人的净速率变差**
      （消耗上升 / 回复下降）就算挂件（只看变差，故"她走了别人反而分得更多"那类池分摊不算）。
      ②③④ 的自动换人**都不换挂件**，顺位找下一位；**点名不受这道闸限制**。
    - **自动路径（②③）的"被换出者"必须是实时满 24**；**④ 的默认兜底不限心情**，
      但仍守"阵营门 / **排除挂件**"两道门。
    - ⚠️ **"自回型不被换出"那道门已取消**（用户裁决 2026-09：**"在宿舍的自回型不进行门保护"**）：
      菲亚梅塔「自律」、缪尔赛思「天生丽质」这类"在宿舍能自己回满"的人，现在照换
      （②③④ 都可能换出她们）。代价是她们的自身回复随之中断、曲线变平线。
    - **目标必须比你更满**（用户口径，见上表"心情闸"）：换人是"拿一个人的宿舍位子换给另一个人"，
      目标不比你更满时等于**把更需要恢复的那位挤出去** ⇒ 不换（**严格大于**，两边一样也不换）。
      实测示例排班：`梅 19.5 ↔ 温蒂 12.3`、`幽灵鲨 20.1 ↔ 温蒂 12.3` 因此改为"不动"，
      温蒂留在宿舍从 12.3 回满到 24，而幽灵鲨留在外面（20.1，进不去）。
    - **④ 的替代动作是"主动换"**（点名）：此时**不要求对方满 24**、也不受"阵营门 / 挂件门"限制
      —— 代价由点名的人自己承担；仍要求对方**此刻真在某间宿舍里**、且过"心情闸"。
    - 被换出的那位**离开宿舍 → 既不工作也不在宿舍**（心情不再变化）。
      进来的人**接替他被换出的那个位次**（不是排到末尾）。
    - **候选顺序**（用户口径）：**先"不在工作也不在宿舍"的人**（本班未排班 / 「不在基建」名单
      —— 他们真的没位置、心情就卡在那里），**再**挂件位（加工站 / 训练室）入驻者
      （他们至少"人在基建内"，还能给别人提供效果）；**每组内部按心情从低到高**。
      为什么这个顺序有意义：候选是**依次**处理的（每人依次找空位 / 找换人对象）⇒
      它决定"谁先拿到空位、谁先挑换人对象"。
    - 每处理一位都用**那一刻的实时心情**重判优先级与候选
      （前面几位换人后，"还有没有空位/还有谁满心情"都会变）。
    - 正在上班的人**不动**（换走会打乱排班）；挂件位（加工站/训练室）与本班未排班都算候选。
    - 指定了"放进哪一间宿舍的空位"（`dorm`）**优先于上面全部**（⓪），且严格按指定：
      那间满了 / 不存在 → 跳过这一位（不退回自动）。

    参数：
        enabled  三态；**默认开**（用户口径"闲置入宿默认是开启的"）：`None` = 用
                 `world.idle_to_dorm.enabled`（没配置也按**开**），显式 `False` 才不结算
        idle     本班未排班的干员 → 心情：`{名字: 心情}`；给了才把他们当候选
        only     只处理这些干员（界面勾了"参与"的人；`None` = 全部候选）
        swap_with  指定交换对象：`{候选名: 目标名}`（`None`/`""` = 自动）
        scope    这一刻是"第几周期的第几班" → `(周期序号, 班次序号)`（1 基）。
                 逐人设置按它取**最具体**的那一条（周期×班次 > 周期/班次 > 全局）；
                 `None` = 不限定（只认没写作用域的设置）
        trace    可选的**出参**：逐位候选记一份"**轮到她的那一刻**"的宿舍态
                 （`dorm_state`，只收名字）→ `{候选名: {"dorms": …, "free": …}}`。
                 界面「闲置入宿」逐次表按它出每行的「换谁 / 宿舍NN」—— 候选是**依次**处理的，
                 前面的人会把宿舍里的人换出去，只有逐位那份才对得上引擎真正会接受谁。
                 ⚠️ **先登记、再判"参与"**：勾掉参与（`enabled=False`）的那位也要有那一刻的世界，
                 否则面板上她的那一行会退回"班末"那份世界。

    ⚠️ **会就地修改 `world`**（有人进宿舍、有人被换出）。返回事件流水账（`Bucket.EVENT`）。
    """
    cfg = getattr(world, "idle_to_dorm", None)
    if enabled is None:
        configured = getattr(cfg, "enabled", None)
        # 默认**开**（用户口径"闲置入宿默认是开启的"）：没配置 / `None` 都按开；
        # 显式 `false`（JSON 或调用方）才关。
        enabled = True if configured is None else bool(configured)
    if not enabled:
        return []

    from .scenario import build_operator      # 局部导入：避免模块级循环依赖

    events: List[Contribution] = []
    swapped_out: set = set()
    # "挂件判据"的**班次内缓存**：一次判据要跑两遍全基建净速率，不缓存会重复试算。
    # 每次真的换了人（成员表变了）就清掉——判据依赖"这一刻谁在宿舍里"。
    pendant_memo: dict = {}
    cycle_no, shift_no = (scope if scope else (None, None))
    for op, name, mood, where in _idle_candidates(world, idle=idle, only=only):
        if trace is not None:
            # 逐位候选各留一份"此刻的宿舍态"：界面每一行的「换谁 / 宿舍NN」按它算
            # （**在判"参与"之前**登记，勾掉参与的人也要有那一刻的世界）。
            trace[name] = dorm_state(world)
        # 界面"逐人/逐次设置"里的参与 / 指定对象（按 (周期, 班次) 取最具体的那一条）
        entry = cfg.entry_for(name, cycle_no, shift_no) if cfg is not None else None
        if entry is not None and not entry.enabled:
            continue
        want = None
        if swap_with is not None:
            want = (swap_with.get(name) or None) if isinstance(swap_with, dict) else None
        if want is None and entry is not None:
            want = entry.swap_with or None

        # ⓪ 指定了"放进哪一间宿舍的空位"（`dorm`）→ 按它放，不看氛围、也不换人
        if entry is not None and entry.dorm is not None:
            dorms = [f for f in world.facilities
                     if f.ftype == FacilityType.DORMITORY and f.enabled]
            if entry.dorm < 1 or entry.dorm > len(dorms):
                events.append(Contribution(
                    Bucket.EVENT, "闲置入宿未执行", ZERO, group="idle_to_dorm_skipped",
                    owner=name, target=f"宿舍{entry.dorm:02d}", detail=(
                        f"（指定的「宿舍{entry.dorm:02d}」不存在——本布局只有 "
                        f"{len(dorms)} 间宿舍 → 跳过这一位）")))
                continue
            dorm = dorms[entry.dorm - 1]
            if len(dorm.operators) >= dorm.capacity:
                events.append(Contribution(
                    Bucket.EVENT, "闲置入宿未执行", ZERO, group="idle_to_dorm_skipped",
                    owner=name, target=dorm.display_name, detail=(
                        f"（指定的「宿舍{entry.dorm:02d}」（{dorm.display_name}）那一刻已经满了 → "
                        f"跳过这一位；要它照样能进，就改成「自动」或指一个满心情的人）")))
                continue
            if op is None:
                op = build_operator({"name": name, "mood": mood})
            _leave_previous_facility(world, name)
            pos = len(dorm.operators) + 1
            dorm.operators.append(op)
            world.invalidate_index()
            pendant_memo.clear()             # 成员表变了 ⇒ 挂件判据与共享速率表都要重算
            extra = ""
            if entry.slot is not None and entry.slot != pos:
                extra = (f"（指定第 {entry.slot} 位，但最靠前的空位是第 {pos} 位；"
                         f"宿舍位次没有机制差异，按第 {pos} 位放）")
            events.append(Contribution(
                Bucket.EVENT, "闲置入宿", ZERO, group="idle_to_dorm",
                owner=name, target=dorm.display_name, detail=(
                    f"（{name} 心情 {mood} 没满且在闲置（{where}）→ 进 "
                    f"宿舍{entry.dorm:02d}（{dorm.display_name}）的第 {pos} 个空位{extra}）")))
            continue

        # ① **有空位就直接住**（最高优先级；"优先 4 最后 1"）
        dorm = _dorm_with_free_slot(world)
        if dorm is not None:
            if op is None:
                op = build_operator({"name": name, "mood": mood})
            # ⚠️ 顺序要紧：**先离开原设施、再进宿舍**——反过来的话"离开"会把刚放进去的人删掉
            _leave_previous_facility(world, name)
            pos = len(dorm.operators) + 1
            dorm.operators.append(op)
            world.invalidate_index()
            pendant_memo.clear()             # 成员表变了 ⇒ 挂件判据与共享速率表都要重算
            events.append(Contribution(
                Bucket.EVENT, "闲置入宿", ZERO, group="idle_to_dorm",
                owner=name, target=dorm.display_name, detail=(
                    f"（{name} 心情 {mood} 没满且在闲置（{where}）→ 进 {dorm.display_name} "
                    f"的第 {pos} 个空位恢复；有空位就不换人 —— 优先级①）")))
            continue

        # ②③ **宿舍全满**：先走**自动**四级规则（点名范围 → "吃不到联动"的人 + 满 24 的菲亚梅塔）
        dorm, mate, tier = _swap_mate(world, exclude=swapped_out, memo=pendant_memo)
        if mate is None and want:
            # ④ 的**替代动作**：自动彻底挑不到人时，才用**你点名**的那位。
            #    ⚠️ 这是"主动换"：不要求她满 24，也不受"阵营门 / 挂件门"限制
            #    （用户的代价他自己承担）；但仍要求她此刻真在某间宿舍里。
            dorm, mate = _named_mate(world, want, exclude=swapped_out)
            if mate is not None:
                tier = 0                     # 0 = 用户点名（走指定口径）
        if mate is None:
            # ④ 的**默认兜底**（用户口径）：没人点名时不再"不动"，而是与**宿舍里心情最高**的
            #    "吃不到阵营联动"的那位互换（不限 24）——例：最高只有 21，就跟 21 那位换。
            dorm, mate = _fallback_mate(world, exclude=swapped_out, memo=pendant_memo)
            if mate is not None:
                tier = 4
        if mate is None:
            why = (f"（{name} 心情 {mood} 想入宿，但宿舍全满、自动也挑不到可换的人"
                   + (f"，且你点名的「{want}」这一刻不在宿舍里" if want else "")
                   + " → 这一班不动 —— 优先级④）")
            events.append(Contribution(
                Bucket.EVENT, "闲置入宿未执行", ZERO, group="idle_to_dorm_skipped",
                owner=name, target=want or "", detail=why))
            continue

        # ④ 的最后一道闸（用户口径）：**被换出者（目标）必须比你更满** ——
        #    "换的时候比较此干员与目标干员的心情，如果目标干员心情大于此干员才交换"。
        #    为什么要它：换人是"拿一个人的宿舍位子换给另一个人"，目标不比你更满时，
        #    等于把**更需要恢复的那位挤出去**、床位给了不那么需要的你，净亏。
        #    ⚠️ 位置放在"挑完人、真正换之前"：②③ 要求目标**实时满 24**、而候选必定 < 24
        #    ⇒ 天然满足，所以这条**实际只在 ④ 生效**（点名与默认兜底都算）。
        #    ⚠️ **严格大于**：两边一模一样时换不换对基地总量没区别，不换更保守。
        if mate.mood <= mood:
            events.append(Contribution(
                Bucket.EVENT, "闲置入宿未执行", ZERO, group="idle_to_dorm_skipped",
                owner=name, target=mate.name, detail=(
                    f"（{name} 心情 {mood}、{mate.name} 心情 {mate.mood} —— 目标并不比你更满，"
                    f"换她出去等于把更需要恢复的人挤掉 → 这一班不动 —— 优先级④）")))
            continue

        if op is None:
            op = build_operator({"name": name, "mood": mood})
        _leave_previous_facility(world, name)
        mate_slot = dorm.operators.index(mate) + 1           # 被换出者的位次（1 基）
        mate_idx = mate_slot - 1
        # ⚠️ 让进来的人**接替被换出者的那个位次**（而不是排到末尾）：这样"换的是第 2~5 位"
        #    才名副其实；末尾追加会让位次随人数漂移、P2 的点名范围就说不清了。
        dorm.operators[mate_idx] = op                        # 她进宿舍
        world.invalidate_index()                             # 成员换了 ⇒ 名字索引作废
        swapped_out.add(mate.name)                           # 满心情那位**换出来 → 闲置**（不占位）
        pendant_memo.clear()                                 # 成员表变了 ⇒ 挂件/阵营判据都要重算
        how = ("你点名的（优先级④的替代动作）" if tier == 0 else
               ("宿舍#2~#4 的第 2~5 位（优先级②）" if tier == 2 else
                ("宿舍其余位置（优先级③）" if tier == 3 else
                 "宿舍里心情最高、又吃不到阵营联动的那位（优先级④的默认兜底）")))
        full = "满心情" if mate.mood >= MOOD_MAX else f"心情 {mate.mood}（你主动换的）"
        events.append(Contribution(
            Bucket.EVENT, "闲置入宿", ZERO, group="idle_to_dorm",
            owner=name, target=mate.name, detail=(
                f"（{name} 心情 {mood} 没满且在闲置（{where}）→ 与 {dorm.display_name} 第 "
                f"{mate_slot} 位、{full}的 {mate.name} 互换：{name} 进宿舍恢复，"
                f"{mate.name} 换出来闲置（既不工作也不在宿舍）—— {how}）")))
    return events


def _leave_previous_facility(world: BaseLayout, name: str) -> None:
    """把该干员从原设施里摘掉（挂件位/宿舍都适用；不在任何设施里就什么都不做）。"""
    for f in world.facilities:
        for i, o in enumerate(list(f.operators)):
            if o.name == name:
                del f.operators[i]
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

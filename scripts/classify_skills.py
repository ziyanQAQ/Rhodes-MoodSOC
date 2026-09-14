"""scripts/classify_skills.py —— 给基建技能挂「六轴模板」，并生成覆盖台账。

产物（两个）：

1. `resources/moods_skills.txt` —— **clause 级**心情技能库，追加两列：
     - `template_id`：模板 ID（见 mood_soc/skill_templates.py），如 `M07a`
     - `params`     ：模板参数，`k=v;k=v` 形式（只放生成器推不出来的新信息）
   同时应用「口径修正」（见 OVERRIDES，例如巫恋「低语」room_others → room）。

2. `resources/skills_registry.txt` —— **buff 级**覆盖台账（仓库全部 755 条 buff 各一行）：
     buff_id, name, room_type, tier, template_ids, modeled, note
   作用：任何新 buff 都必须在这张表里有归宿；`--check` 断言
   「台账行数 == 仓库 buff 数」且「无 tier=?? 的行」——把漏技能变成必报错误。

用法：

    # 需要上游仓库数据时（首次生成 / 版本更新后）
    .venv/Scripts/python.exe scripts/classify_skills.py --agd <ArknightsGameData>

    # 只做覆盖校验（CI / 日常，不需要仓库）
    .venv/Scripts/python.exe scripts/classify_skills.py --check

上游：`zh_CN/gamedata/excel/building_data.json`（buffs + chars）。
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "resources"
SKILLS_TXT = RES / "moods_skills.txt"
REGISTRY_TXT = RES / "skills_registry.txt"


def _load_templates_module():
    """按文件直接加载 skill_templates.py，绕开 mood_soc/__init__.py。

    直接 `import mood_soc.skill_templates` 会连锁触发 rules → skills → skills_data
    （生成物），使脚本无法在 skills_data 过期时运行（引导死锁）。
    """
    import importlib.util
    import sys
    path = ROOT / "mood_soc" / "skill_templates.py"
    spec = importlib.util.spec_from_file_location("_mood_soc_skill_templates", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod      # dataclass 需要模块已在 sys.modules 中
    spec.loader.exec_module(mod)
    return mod


_TPL = _load_templates_module()
TEMPLATES = _TPL.TEMPLATES
PARTIAL_APPLY = _TPL.PARTIAL_APPLY
PARTIAL_HOLD = _TPL.PARTIAL_HOLD
ROOM2_MAX_GROUP = _TPL.ROOM2_MAX_GROUP
ROOM2_MAX_GROUP_SKILL_IDS = _TPL.ROOM2_MAX_GROUP_SKILL_IDS

SKILLS_HEADER = ["skill_id", "clause", "name", "kind", "family", "target",
                 "condition", "value_milli", "status", "control_capability",
                 "template_id", "params"]
REGISTRY_HEADER = ["buff_id", "name", "room_type", "tier", "template_ids", "modeled", "note"]

STRIP_TAGS = re.compile(r"</?(?:@cc\.\w+|\$cc\.\w+)>")
strip_tags = lambda s: STRIP_TAGS.sub("", s or "")
norm_id = lambda b: b.replace("[", "_").replace("]", "")

# 已建模的设施（本轮纳入每小时心情模型的房间）
MODELED_ROOMS = {"CONTROL", "DORMITORY", "POWER", "MANUFACTURE", "TRADING", "HIRE", "MEETING"}

# ---------------------------------------------------------------------------
# 口径修正：用户已拍板的判定，作用在**源数据**上（可复现、可审计）
# ---------------------------------------------------------------------------
OVERRIDES = {
    # 巫恋「低语」：官方原文「同时全体心情每小时消耗+0.25」= 含自身 → M05，
    # 原先按 room_others（不含自身）建模作废。
    "trade_ord_vodfox_000": {
        "family": "room",
        "_note": "低语口径修正：全体含自身（原 room_others 作废）",
    },
    # 若叶睦「互为半身」：机制是「与丰川祥子同中枢时消除自身心情消耗影响」，
    # 与令「杯莫停」同类，归 M13（模板确定），条件由 M13 的 filter 槽承担。
    "control_mp_cost_reset_000": {
        "family": "immune",
        "_note": "互为半身归入 M13（消除自身消耗影响）",
    },
}

# ---------------------------------------------------------------------------
# 轴 F 取值修正：这些技能属于官方「特殊比较规则 / 取最高集」
# ---------------------------------------------------------------------------
def params_for_clause(skill_id: str, clause: str, family: str, kind: str,
                      target: str, condition: str, cap: str) -> tuple[str, str]:
    """返回 (template_id, params)。"""
    text = f"{target} {condition} {cap}"

    # —— 中枢跨设施回复（M02 族）——
    if cap == "mlynar_fixed_spread":
        return "M02c", f"value_scope=room1;spread=cc.c.skill;max_group={ROOM2_MAX_GROUP}"
    if family == "work_area":
        grp = f";max_group={ROOM2_MAX_GROUP}" if skill_id in ROOM2_MAX_GROUP_SKILL_IDS else ""
        return "M02b", f"scope=room2{grp}"

    # —— 中枢→宿舍 ——
    if family == "control_room":
        if "所有宿舍" in target or "所有宿舍" in condition:
            return "M01", "target=dorm_all;stacking=same_kind_max"
        if skill_id in ("control_mp_cost_double_000", "control_mp_cost_double_001"):
            return "M04", 'with_operator="阿米娅"'
        return "M03", "target=cc_all;stacking=sum"

    # —— 宿舍 ——
    if family == "dorm_group":
        if any(k in text for k in ("心情18以下", "心情20以下", "格拉斯哥帮", "多心情子句")):
            return "M12", "stacking=sum"
        excl = "true" if "除自身以外" in target or "除自身以外" in condition else "false"
        return "M08", f"exclude_self={excl};stacking=same_kind_max"
    if family == "dorm_single":
        return "M09", "target_policy=minimum_unfull_exclude_provider;stacking=same_kind_max"
    if family == "dorm_meta":
        # M17 元修正：**强化他人**在宿舍里的恢复效果（摩根「头号陪练」→ 推进之王 +0.3）。
        # `boost_provider`（被点名强化的持有者）由人工写在 params 里，分类器不产生该键，
        # 故上面的「保留手写参数」逻辑会让它原样留存。
        return "M17", "boost_group=dorm_group;stacking=sum"
    if family == "dorm_self":
        return "M10", "stacking=same_kind_max"
    if family == "dorm_pool":
        return "M11", "total=0.8;recipients=unfull"
    if family == "no_external":
        return "M14", "exclusive=true"
    if family == "swap":
        return "M15a", "swap_with=previous_occupant;partial_mode=hold"
    if family == "complex":
        # family=complex 只是"分句结构复杂"，不代表条件缺失；partial_mode 交给下面的
        # partial 判定统一决定（净化呼吸等其实已由 COND_MARKERS 翻译好）。
        return "M12", "stacking=sum"

    # —— 事件类 ——
    if family == "immune":
        return "M13", "filter=sui" if "岁" in text else "filter=all"

    # —— 同设施消耗 ——
    if family == "room":
        return "M05", "include_self=true;stacking=sum"
    if family == "room_others":     # 修正后应无使用者
        return "M06", "include_self=false;stacking=sum"

    # —— 自身 ——
    if family == "self":
        if kind == "recover":
            return "M07b", "partial_mode=hold"
        return "M07a", "stacking=sum"

    return "??", ""


# ---------------------------------------------------------------------------
# buff 级台账分类（未进 moods_skills.txt 的那些 buff）
# ---------------------------------------------------------------------------
LEDGER_RULES = [
    # (模板, tier, 判定函数)
    ("M07a", "A1", lambda s, r: "每小时" in s and "心情" in s),               # 训练室等未建模设施
    ("M15b", "A2", lambda s, r: "恢复自身一次心情" in s or "恢复一次心情" in s),
    # 「改别人的技能」的元修正：描述里往往不写「心情」（如 摩根 头号陪练）
    ("M17", "A1", lambda s, r: "恢复效果" in s and "额外" in s),
    ("X08", "A2", lambda s, r: r == "WORKSHOP" and "心情" in s),             # 配方心情消耗（按次）
    ("M16", "A3", lambda s, r: "心情" in s),
    ("X08", "A4", lambda s, r: r == "WORKSHOP"),
    ("X09", "A4", lambda s, r: any(v in s for v in
        ("人间烟火", "感知信息", "无声共鸣", "热情值", "记忆碎片", "因果", "业报", "木天蓼",
         "魔物料理", "乌萨斯特饮", "外势", "实地", "巫术结晶", "赤金生产线", "念力",
         "意识实体", "徘徊旋律", "怅惘和声", "无词颂歌", "梦境", "小节", "工程机器人",
         "情报储备", "丰富工作经验"))),
    ("X05", "A4", lambda s, r: "线索" in s),
    ("X06", "A4", lambda s, r: r == "TRAINING" or "训练" in s or "专精" in s),
    ("X04", "A4", lambda s, r: "无人机" in s or "充能" in s or r == "POWER"),
    ("X03", "A4", lambda s, r: "订单" in s or "赤金" in s or r == "TRADING"),
    ("X02", "A4", lambda s, r: "仓库容量" in s),
    ("X01", "A4", lambda s, r: "生产力" in s or "生产" in s or r == "MANUFACTURE"),
    ("X06", "A4", lambda s, r: "招募位" in s or "联络" in s or r == "HIRE"),
    ("X05", "A4", lambda s, r: r == "MEETING"),
    ("X10", "A4", lambda s, r: "设施数量" in s or "仅影响" in s),
    ("X01", "A4", lambda s, r: r == "CONTROL"),
]


def classify_buff(desc: str, room: str) -> tuple[str, str]:
    """未进心情技能库的 buff → (template_id, tier)。"""
    s = strip_tags(desc)
    for tpl, tier, fn in LEDGER_RULES:
        if fn(s, room):
            return tpl, tier
    return "??", "??"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def load_agd_rows(agd: Path):
    """从上游仓库读 buffs / chars。"""
    bd = json.loads((agd / "zh_CN/gamedata/excel/building_data.json").read_text(encoding="utf-8"))
    ct = json.loads((agd / "zh_CN/gamedata/excel/character_table.json").read_text(encoding="utf-8"))
    owners = defaultdict(list)
    for cid, c in bd["chars"].items():
        nm = (ct.get(cid) or {}).get("name", cid)
        for g in c.get("buffChar") or []:
            for x in g.get("buffData") or []:
                owners[x["buffId"]].append(nm)
    return bd["buffs"], owners


def backfill_skills_txt() -> tuple[list[dict], Counter, Counter]:
    """给 moods_skills.txt 追加 template_id / params，并应用 OVERRIDES。"""
    with SKILLS_TXT.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]

    out, tpl_count, partial_count = [], Counter(), Counter()
    for r in body:
        if len(r) < 9:
            continue
        rec = dict(zip(header, r))
        sid = rec["skill_id"]

        # 口径修正
        if sid in OVERRIDES:
            ov = OVERRIDES[sid]
            for k, v in ov.items():
                if not k.startswith("_"):
                    rec[k] = v

        tpl, params = params_for_clause(
            sid, rec["clause"], rec["family"], rec["kind"],
            rec.get("target", ""), rec.get("condition", ""),
            rec.get("control_capability", ""))

        # partial 处置：模板确定但条件槽空（原 status=complex）
        if rec["status"] == "complex":
            if "partial_mode=" not in params:
                # 判定主效果是否无条件成立：
                #   · 条件文本含「额外」→ 该 clause 的 value 是**基础效果**，条件只决定额外加成 ⇒ apply
                #   · 条件文本为空   → 没有缺失条件（complex 只是文本被截断）⇒ apply
                #   · 其余（每有/每个/阈值/共事等）→ 该 clause 的 value **本身**以缺失条件为前提 ⇒ hold
                cond_text = rec.get("condition", "").strip()
                if not cond_text or "额外" in cond_text:
                    mode = PARTIAL_APPLY
                else:
                    mode = PARTIAL_HOLD
                params += f";partial_mode={mode}"
            params += ";partial=true"
            partial_count[params.split("partial_mode=")[1].split(";")[0]] += 1
        else:
            params += ";partial=false"

        # ⚠️ 参数幂等：分类器只负责它自己算得出的参数（stacking / max_group / spread / partial_mode…），
        # **必须保留手写参数**（basis= / var= / var_per= / var_min=），
        # 否则重跑一次 `--agd` 就会把手工回填的折算规则全部抹掉。
        # `partial` / `partial_mode` 是"条件槽是否已解析"的判定，而该判定可能来自
        # 分类器之外的地方（`CLAUSE_COND` / `var=` / `basis=` / `COND_MARKERS` / 人工确认）。
        # 因此：**已存在的判定不被分类器覆盖**，分类器只负责给新行一个初值。
        merged = parse_params(rec.get("params", ""))
        computed = parse_params(params)
        for k in ("partial", "partial_mode"):
            if k in merged:
                computed.pop(k, None)
        merged.update(computed)
        rec["template_id"] = tpl
        rec["params"] = ";".join(f"{k}={v}" for k, v in merged.items())
        tpl_count[tpl] += 1
        out.append([rec.get(h, "") for h in SKILLS_HEADER])

    with SKILLS_TXT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(SKILLS_HEADER)
        w.writerows(out)
    return out, tpl_count, partial_count


def build_registry(buffs: dict, owners: dict, modeled_ids: dict[str, str]) -> list[list[str]]:
    """755 行覆盖台账。modeled_ids: norm_id -> template_id（来自 clause 级）。"""
    ledger = []
    for bid, b in sorted(buffs.items()):
        nid = norm_id(bid)
        room = b.get("roomType") or ""
        desc = b.get("description") or ""
        if nid in modeled_ids:
            tpl = modeled_ids[nid]
            tier = TEMPLATES[tpl].tier.value if tpl in TEMPLATES else "??"
            ledger.append([bid, b.get("buffName") or "", room, tier, tpl, "yes",
                           f"clause 级已建模；持有 {len(owners.get(bid, []))} 人"])
        else:
            tpl, tier = classify_buff(desc, room)
            if tpl == "M17":
                note = "元修正技能（改他人的恢复效果），需专门机制，登记不建模"
            elif tier == "A1" and room not in MODELED_ROOMS:
                note = f"{room} 未纳入每小时模型（设施未建模）"
            elif tier == "A1":
                note = "A1 心情速率但未进 skill 库，待人工确认"
            elif tier == "A2":
                note = "非速率的心情影响（按次/触发/事件），登记不建模"
            elif tier == "A3":
                note = "把心情当条件用，效果非心情，登记不建模"
            else:
                note = "非心情，登记不建模"
            ledger.append([bid, b.get("buffName") or "", room, tier, tpl, "no", note])
    return ledger


def audit_enhanced(agd: Path) -> list[str]:
    """审计 `operators.txt` 的 `enhanced` 列是否与上游「槽位结构」一致。

    ## 判定依据（上游权威，可复核）

    `building_data.json → chars[charId].buffChar[]` 里**每个元素是一个基建技能槽**：

    | 上游结构 | 含义 |
    |---|---|
    | 同一槽位内有多个 `buffData`（如 PHASE_0 与 PHASE_2） | 同一技能的**精英化提升链**（α→β）→ 高阶段那条必须是 `enhanced=1` |
    | 不同槽位 | 两个**独立**基建技能 |

    实例对照：火神「工匠精神·α/·β」同槽 0（已知提升链）；
    泡泡「囤积者」槽 0、「大就是好！」槽 1（两个独立技能）。

    ## 为什么需要它

    历史上的 `enhanced` 列是**推断**出来的（上游没有 `replaces` 字段），
    一旦漏标，β 就不会替换 α，两者**同时生效**。
    实测抓到 1 例：刺玫「芬芳疗养·β」漏标 → α 与 β 同时进流水账
    （数值上被"同种取最高"掩盖，但 β 的条件加成子句会重复生效）。
    这个漏标正是**心情流水账**（`mood_ledger().explain()`）暴露出来的。
    """
    bd = json.loads((agd / "zh_CN/gamedata/excel/building_data.json").read_text(encoding="utf-8"))
    ct = json.loads((agd / "zh_CN/gamedata/excel/character_table.json").read_text(encoding="utf-8"))
    norm = lambda b: b.replace("[", "_").replace("]", "")
    phase_rank = {"PHASE_0": 0, "PHASE_1": 1, "PHASE_2": 2}

    # 上游：干员 -> {(buff_id): 是否提升链里阶段最高者}
    should_enhance = {}
    for cid, c in bd["chars"].items():
        name = (ct.get(cid) or {}).get("name", cid)
        for group in c.get("buffChar") or []:
            data = group.get("buffData") or []
            if len(data) < 2:
                continue                     # 一个槽只有一个 buff = 独立技能
            ranked = sorted(data, key=lambda x: phase_rank.get((x.get("cond") or {}).get("phase"), 0))
            # 链上**除最低阶段外**都应为"提升"（每条替换它的前一条）。
            # 例：赫拉格 槽0 = 超脱(PHASE_0) → 挣脱(PHASE_1) → 解脱(PHASE_2)，
            #     后两条 enhanced=1（挣脱替换超脱、解脱替换挣脱）。
            for i, x in enumerate(ranked):
                should_enhance[(name, norm(x["buffId"]))] = (i > 0)

    issues = []
    with SKILLS_TXT.open(encoding="utf-8") as f:
        pass                                  # 占位：技能库不含解锁信息
    with (RES / "operators.txt").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["operator_name"], r["skill_id"])
            if key not in should_enhance:
                continue
            want = should_enhance[key]
            got = r.get("enhanced") == "1"
            if want != got:
                issues.append(
                    f"{r['operator_name']}「{r['skill_name']}」({r['skill_id']}) "
                    f"enhanced={int(got)}，上游槽位结构要求 {int(want)}"
                    + ("（该条是提升链的高阶段，应替换低版本）" if want else "（该条是低版本，不应标提升）"))
    return issues


def parse_params(raw: str) -> dict:
    """解析 `k=v;k=v` 字符串。"""
    out = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if part and "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def agd_commit(agd: Path) -> str:
    """上游仓库当前 commit（可追溯性：结论要能对回具体版本）。

    策略见 AGENTS.md §11：未知数据一律以上游仓库为准，因此每次生成都要留下版本号。
    """
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(agd), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def main() -> int:
    argv = sys.argv[1:]
    check_only = "--check" in argv
    agd = None
    if "--agd" in argv:
        agd = Path(argv[argv.index("--agd") + 1])

    if not check_only:
        if agd is None or not (agd / "zh_CN/gamedata/excel/building_data.json").exists():
            print("需要 --agd <ArknightsGameData 路径> 才能重新生成；或用 --check 只校验。")
            return 2
        rows, tpl_count, partial_count = backfill_skills_txt()
        modeled_ids = {}
        for r in rows:
            modeled_ids[r[0]] = r[10]
        buffs, owners = load_agd_rows(agd)
        ledger = build_registry(buffs, owners, modeled_ids)
        with REGISTRY_TXT.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(REGISTRY_HEADER)
            w.writerows(ledger)
        print(f"已更新 {SKILLS_TXT.relative_to(ROOT)}（{len(rows)} 个 clause）")
        print(f"已生成 {REGISTRY_TXT.relative_to(ROOT)}（{len(ledger)} 条 buff）")
        print(f"上游 ArknightsGameData：{agd} @ {agd_commit(agd)}"
              f"（AGENTS.md §11 数据查找策略；结论请引用该 commit）")

    # ---------------- 校验 ----------------
    print("\n=== 校验 ===")
    with SKILLS_TXT.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    with REGISTRY_TXT.open(encoding="utf-8", newline="") as f:
        ledger = list(csv.DictReader(f))

    bad_tpl = [r["skill_id"] for r in rows if r["template_id"] not in TEMPLATES]
    enhanced_issues = audit_enhanced(agd) if agd is not None else []
    bad_tier = [r["buff_id"] for r in ledger if r["tier"] in ("??", "")]
    print(f"clause 行数              : {len(rows)}")
    print(f"  模板未命中            : {len(bad_tpl)} {bad_tpl[:8]}")
    print(f"台账行数（应 = buff 总数）: {len(ledger)}")
    print(f"  tier 未判定           : {len(bad_tier)} {bad_tier[:8]}")

    tpl_dist = Counter(r["template_id"] for r in rows)
    print("\nclause → 模板分布：")
    for t in sorted(tpl_dist):
        print(f"  {t:6s} {TEMPLATES[t].name if t in TEMPLATES else '?':22s} {tpl_dist[t]:3d}")
    tier_dist = Counter(r["tier"] for r in ledger)
    print("\nbuff → 建模地位分布：")
    for t in sorted(tier_dist):
        print(f"  {t:4s} {tier_dist[t]:4d}")

    unmodeled_mood = [r for r in ledger if r["modeled"] == "no" and r["tier"] in ("A1", "A2", "A3")]
    print(f"\n登记但未建模的「心情相关」buff：{len(unmodeled_mood)}")
    for r in unmodeled_mood[:12]:
        print(f"  [{r['tier']}] {r['buff_id']:34s} {r['name']:10s} {r['note']}")

    print(f"\n提升链(enhanced) 与上游槽位不符：{len(enhanced_issues)}")
    for i in enhanced_issues:
        print(f"  - {i}")

    ok = not bad_tpl and not bad_tier and not enhanced_issues and len(ledger) == 755
    print(f"\n结果：{'✅ 全部有归属且提升链一致' if ok else '❌ 存在问题项'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""tests/test_skill_coverage.py —— 技能**全量覆盖**黑盒测试（三层）。

它回答的问题不是"某几条技能算得对不对"，而是**"整套心情模型把上游技能覆盖全了没有、
每条都对不对"**。三层：

| 层 | 对象 | 断言 |
|---|---|---|
| **L1 模板级** | 32 个模板（M01~M17 / X01~X11） | 模板 ↔ 数据自洽；每个**已建模**模板都有覆盖路径；机制本身（含自身 / 取最高 / 归零 / 独占 / 池 / 扩散 / 元修正 / 事件）用真干员验证 |
| **L2 clause 级** | `skills.SKILLS` 的 **250** 条 clause | 逐条自动造"满足它条件"的场景，断言它出现在流水账里、落在正确的桶、**数值 == 模板口径**、**作用范围正确**（该落在谁账上就落在谁账上） |
| **L3 描述对照** | 上游 `building_data.json` 描述原文 | 描述里的数字与方向词 ↔ 数据表 `value` 逐条对照（0 处不符；1 处已登记差异见报告） |

另有一条"台账双向核对"：上游 **755** 条 buff 必须**全部有归宿**
（`modeled=yes` ⇒ 有 clause；`modeled=no` ⇒ 一条 clause 也没有）。

⚠️ 核对逻辑在 `scripts/verify_skills.py`（同样只读，可独立跑出报告）。本文件只做断言，
**不重复实现**口径；注入式用例（故意把引擎改坏）用来证明"核对真的会报错"。

L3 需要上游仓库：找不到时**跳过**（不假装通过），并提示用 `--agd` 或环境变量
`ARKNIGHTS_GAMEDATA` 指定。

运行：.venv/Scripts/python.exe -m unittest tests.test_skill_coverage -v
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mood_soc import build_base_layout                                    # noqa: E402
from mood_soc.config import FacilityType                                  # noqa: E402
from mood_soc.ledger import Bucket                                        # noqa: E402
from mood_soc.rules import apply_entry_events, mood_ledger                # noqa: E402
from mood_soc.skills import SKILLS, SkillKind, base_skill_id              # noqa: E402
from mood_soc.skill_templates import ModelTier, TEMPLATES                 # noqa: E402
from mood_soc.variables import basis_count                                # noqa: E402

REPORT_MD = ROOT / "resources" / "skill_verify_report.md"


def _load_verifier():
    """按文件加载 `scripts/verify_skills.py`（`scripts/` 不是包，用 importlib 最稳）。"""
    path = ROOT / "scripts" / "verify_skills.py"
    spec = importlib.util.spec_from_file_location("_verify_skills", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


V = _load_verifier()

# 三层里**预期**的规模（写死成数字：数据规模变了必须有人来解释）
CLAUSE_TOTAL = 250
BUFF_TOTAL = 755
MODELED_BUFFS = 210
TEMPLATE_TOTAL = 32


# ============================================================================
# L1 模板级
# ============================================================================
# 模板 → 覆盖它的**机制用例**（表里必须写全"有专门用例"的那些模板；
# 其余已建模模板靠 L2 的"它的 clause 全部核对通过"来覆盖）
MECHANISM_TESTS = {
    "M02b": "test_M02b跨干员取最高不叠加",
    "M02c": "test_M02c公事公办把中枢回复扩散到其他设施",
    "M03": "test_M03阵营计数按人数倍增",
    "M05": "test_M05同设施全体消耗含自身",
    "M11": "test_M11池分配按未满人数平分",
    "M13": "test_M13消除把自身消耗整组归零",
    "M14": "test_M14独占回复清空其它来源",
    "M15a": "test_M15a患难之交是进驻事件不是速率",
    "M17": "test_M17元修正并入被强化者的小计",
}


class Test模板级(unittest.TestCase):
    """L1：模板 ↔ 数据自洽 + 每个模板的机制本身。"""

    def test_模板与数据自洽(self):
        """每个 clause 的模板都存在；已建模模板都有使用者（除文档登记的 3 个）。"""
        self.assertEqual(V.check_templates(), [])
        self.assertEqual(len(TEMPLATES), TEMPLATE_TOTAL)

    def test_上游台账755条双向对上(self):
        """上游 755 条 buff 一条不漏：标了建模的必须有 clause，没标的必须没有。"""
        issues, stats = V.check_registry()
        self.assertEqual(issues, [])
        self.assertEqual(stats["rows"], BUFF_TOTAL)
        self.assertEqual(stats["modeled"], MODELED_BUFFS)
        self.assertEqual(stats["unmodeled"], BUFF_TOTAL - MODELED_BUFFS)
        # 轴 A 分布：A1 每小时速率 / A2 心情事件 / A3 心情当条件 / A4 非心情
        self.assertEqual(stats["tiers"], {"A1": 214, "A2": 40, "A3": 6, "A4": 495})

    def test_每个已建模模板都有覆盖路径(self):
        """已建模模板要么有专门的机制用例，要么它的 clause 全过 L2（二选一，不能都没有）。"""
        used = {tid: 0 for tid in TEMPLATES}
        for s in SKILLS.values():
            used[s.template_id] = used.get(s.template_id, 0) + 1
        thin = []
        for tid, t in TEMPLATES.items():
            if not tid.startswith("M") or not t.modeled:
                continue
            if tid in V.NO_USER_TEMPLATES:
                continue
            if used[tid] == 0:
                thin.append(f"{tid} 既没有使用者，也没登记为「当前无使用者」")
        self.assertEqual(thin, [])
        # 机制用例表里的方法必须真的存在（改了名字要一起改表）
        for tid, method in MECHANISM_TESTS.items():
            self.assertTrue(hasattr(Test模板级, method), f"{tid} 指向的用例不存在：{method}")

    def test_计数基准的硬数字(self):
        """轴 E3/E4 的"每有 N 个什么"：计数基准必须数对（2 间发电站=2、宿舍 Lv5=5…）。"""
        world = build_base_layout({"facilities": [
            {"type": "控制中枢", "level": 5, "operators": ["路人甲"]},
            {"type": "发电站", "level": 1, "operators": ["路人乙"]},
            {"type": "发电站", "level": 1, "operators": ["路人丙"]},
            {"type": "办公室", "level": 3, "operators": ["路人丁"]},
            {"type": "宿舍", "level": 5, "operators": ["路人戊", "路人己"]},
        ]})
        dorm = world.facility_of("路人戊")
        self.assertEqual(basis_count(world, "power_count"), Decimal("2"))
        self.assertEqual(basis_count(world, "dorm_level", dorm, world.get_operator("路人戊")),
                         Decimal("5"))
        self.assertEqual(basis_count(world, "recruit_slot"), Decimal("3"))       # 办公室 Lv3
        self.assertEqual(basis_count(world, "dorm_others", dorm,
                                     world.get_operator("路人戊")), Decimal("1"))
        self.assertEqual(basis_count(world, "flat"), Decimal("1"))

    # ------------------------------------------------------------ 各模板机制
    def test_M05同设施全体消耗含自身(self):
        """「该贸易站内所有干员的心情每小时消耗+0.25」= **含持有者自己**（上游原文口径）。"""
        world = self._trade({"巫恋": 24, "路人甲": 24})
        for who in ("巫恋", "路人甲"):
            vals = [c.value for c in mood_ledger(world, who).of(Bucket.CONSUME)
                    if base_skill_id(c.skill_id) == "trade_ord_vodfox_000"]
            self.assertEqual(vals, [Decimal("0.25")], f"{who} 应当也被 +0.25")

    def test_M13消除把自身消耗整组归零(self):
        """槐琥「团队精神」：把同制造站**所有**干员的"自身技能"影响整组归零。"""
        world = build_base_layout({"facilities": [
            {"type": "控制中枢", "level": 1, "operators": ["路人甲"]},
            {"type": "制造站", "level": 3, "operators": ["槐琥", "泡泡"]},
        ]})
        # 泡泡「锻造」自身减耗 -0.25 本来在账上
        alone = build_base_layout({"facilities": [
            {"type": "控制中枢", "level": 1, "operators": ["路人甲"]},
            {"type": "制造站", "level": 3, "operators": ["泡泡", "路人乙"]},
        ]})
        self.assertTrue(any(c.group == "self_consume"
                            for c in mood_ledger(alone, "泡泡").of(Bucket.CONSUME)))
        lg = mood_ledger(world, "泡泡")
        self.assertEqual([c.zeroes_group for c in lg.of(Bucket.CONSUME) if c.zeroes_group],
                         ["self_consume"], "应当有一条把自身消耗整组归零的贡献")
        # 合计里不再含「自身消耗」这一组：等于把其余各组直接相加
        rest = sum((c.value for c in lg.of(Bucket.CONSUME)
                    if c.group != "self_consume" and not c.zeroes_group), Decimal("0"))
        self.assertEqual(lg.total(Bucket.CONSUME), rest)

    def test_M14独占回复清空其它来源(self):
        """菲亚梅塔「自律」：自身 +2，且**拒绝**宿舍基础回复与其它任何来源。"""
        world = build_base_layout({"facilities": [
            {"type": "宿舍", "level": 5, "operators": ["菲亚梅塔", "路人甲"]},
        ]})
        lg = mood_ledger(world, "菲亚梅塔")
        items = lg.of(Bucket.RECOVER)
        self.assertEqual(len(items), 1, "独占时只应留下自己那一条")
        self.assertEqual(items[0].value, Decimal("2"))
        self.assertTrue(items[0].exclusive)
        self.assertEqual(lg.total(Bucket.RECOVER), Decimal("2"))

    def test_M11池分配按未满人数平分(self):
        """冰酿「小酌怡情」：0.8 总额平分给**心情未满**的宿舍成员。"""
        world = build_base_layout({"facilities": [
            {"type": "宿舍", "level": 5, "operators": [
                {"name": "冰酿", "mood": 12}, {"name": "路人甲", "mood": 12},
                {"name": "路人乙", "mood": 24}]},
        ]})
        lg = mood_ledger(world, "路人甲")
        pool = [c for c in lg.of(Bucket.RECOVER) if c.pool_share]
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0].value, Decimal("0.4"))      # 0.8 / 2 名未满
        self.assertEqual(pool[0].pool_share, 2)

    def test_M02b跨干员取最高不叠加(self):
        """孤光共照 + 巴别塔之帜同属 `room2_recover` 集：**取最高**，不求和。"""
        world = build_base_layout({"facilities": [
            {"type": "控制中枢", "level": 5, "operators": ["重岳", "维什戴尔"]},
            {"type": "制造站", "level": 3, "operators": ["路人甲"]},
        ]})
        lg = mood_ledger(world, "路人甲")
        items = lg.of(Bucket.RECOVER)
        room2 = [c for c in items if c.max_group == "room2_recover"]
        self.assertGreaterEqual(len(room2), 2, "两条都应在账上（谁生效由取最高决定）")
        self.assertTrue(all(c.stacking.value == "F3" for c in room2))
        best = max(c.value for c in room2)
        others = sum((c.value for c in items if not c.max_group), Decimal("0"))
        self.assertEqual(lg.total(Bucket.RECOVER), others + best,
                         "room2 那一组只能算最高的一条，不能相加")

    def test_M02c公事公办把中枢回复扩散到其他设施(self):
        """玛恩纳「公事公办」：白名单里的 15 条**中枢内**回复技能，额外扩散到工作设施。

        这 15 条（左膀右臂 / S.W.E.E.P. / 笑靥如春…）本人的作用范围只有**中枢内**；
        中枢里有玛恩纳时，它们才额外落到发电/制造/贸易/办公/会客的干员身上。
        """
        def world_with(spread: bool):
            cc = ["玛恩纳", "临光"] if spread else ["临光"]
            return build_base_layout({"facilities": [
                {"type": "控制中枢", "level": 5, "operators": cc},
                {"type": "制造站", "level": 3, "operators": ["路人甲"]},
                {"type": "发电站", "level": 1, "operators": ["路人乙"]},
            ]})

        def reach(world, who):
            return [c.value for c in mood_ledger(world, who).of(Bucket.RECOVER)
                    if base_skill_id(c.skill_id) == "control_mp_cost_000"]   # 左膀右臂

        without, with_spread = world_with(False), world_with(True)
        self.assertEqual(reach(without, "路人甲"), [])          # 没有扩散 → 制造站够不到
        self.assertEqual(reach(without, "路人乙"), [])          # 发电站同样够不到
        self.assertEqual(reach(without, "临光"), [Decimal("0.05")])   # 但中枢内一直有效
        self.assertEqual(reach(with_spread, "路人甲"), [Decimal("0.05")])
        self.assertEqual(reach(with_spread, "路人乙"), [Decimal("0.05")])

    def test_M03阵营计数按人数倍增(self):
        """陈「德才兼备」：中枢内**每个龙门近卫局**干员 +0.05（按人数倍增，不是固定值）。

        ⚠️ 计数**含持有者自己**（陈本身就是龙门近卫局），所以"只有陈"时是 1 人。
        """
        one = build_base_layout({"facilities": [
            {"type": "控制中枢", "level": 5, "operators": ["陈", "路人甲"]},
        ]})
        two = build_base_layout({"facilities": [
            {"type": "控制中枢", "level": 5, "operators": ["陈", "诗怀雅", "路人甲"]},
        ]})

        def got(world):
            return [c.value for c in mood_ledger(world, "陈").of(Bucket.RECOVER)
                    if base_skill_id(c.skill_id) == "control_mp_cost&faction_000"]

        self.assertEqual(got(one), [Decimal("0.05")])           # 只有陈自己
        self.assertEqual(got(two), [Decimal("0.10")])           # 陈 + 诗怀雅

    def test_M17元修正并入被强化者的小计(self):
        """摩根「头号陪练」：不产生自己的回复，而是把**推进之王**那条顶高 +0.3。

        （若把增量记成独立技能，会被"同种取最高"当成竞争者而丢掉——这里的断言盯住"并入"。）
        条件：目标是**格拉斯哥帮**干员（推进之王自己就是）。
        """
        world = build_base_layout({"facilities": [
            {"type": "宿舍", "level": 5, "operators": [
                {"name": "摩根", "mood": 12}, {"name": "推进之王", "mood": 12}]},
        ]})
        lg = mood_ledger(world, "推进之王")
        boost = [c for c in lg.of(Bucket.RECOVER) if "头号陪练" in (c.detail or "")]
        self.assertEqual(len(boost), 1, "应当有一条由头号陪练带来的增量")
        self.assertEqual(boost[0].value, Decimal("0.3"))
        self.assertEqual(boost[0].owner, "推进之王")            # 记在**被强化者**名下
        self.assertEqual(boost[0].stacking.value, "F2")         # 与它同组，先求和再比
        # 她自己没有任何直接回复技能被误算成"摩根给的"
        self.assertEqual([c for c in lg.of(Bucket.RECOVER)
                          if c.owner == "摩根" and c.skill_name != "头号陪练"], [])

    def test_M15a患难之交是进驻事件不是速率(self):
        """菲亚梅塔「患难之交」：进驻那一刻**互换心情**（事件），不进每小时速率。

        （她是宿舍里第一个进驻的就没有"前一位"，所以测试里把路人放在前面。）
        """
        world = build_base_layout({"facilities": [
            {"type": "宿舍", "level": 5, "operators": [
                {"name": "路人甲", "mood": 10}, {"name": "菲亚梅塔", "mood": 24}]},
        ]})
        # 速率侧没有她的非零贡献
        self.assertEqual([c for c in mood_ledger(world, "菲亚梅塔").of(Bucket.RECOVER)
                          if base_skill_id(c.skill_id) == "dorm_exchangeAp_000"
                          and c.value != 0], [])
        events = apply_entry_events(world)
        self.assertEqual(len(events), 1, "满心情 → 与前一位互换")
        self.assertEqual(world.get_operator("菲亚梅塔").mood, Decimal("10"))
        self.assertEqual(world.get_operator("路人甲").mood, Decimal("24"))

    def test_不以物喜按上游原文走回复侧(self):
        """夕「不以物喜」：上游写「控制中枢内所有干员的心情每小时**恢复**+0.05」→ 回复侧。

        回归用例：这条原先按 `M05`「同设施全体**消耗** -0.05」建模（净效果看起来一样，
        但消耗侧末尾有「钳位 ≥0」，中枢里有人消耗已经减到 0 时就会少给 0.05）。
        见 `documents/05-技能分类大纲.md` §5.12。
        """
        world = build_base_layout({"facilities": [
            {"type": "控制中枢", "level": 5, "operators": ["夕", "路人甲"]},
        ]})
        cls = SKILLS["control_mp_cost&bd1_000#1"]
        self.assertEqual(cls.kind, SkillKind.CC_RECOVER)
        self.assertEqual(cls.value, Decimal("0.05"))
        self.assertEqual(cls.template_id, "M03")
        for who in ("夕", "路人甲"):                     # 中枢内**每人**各一条
            got = [c.value for c in mood_ledger(world, who).of(Bucket.RECOVER)
                   if base_skill_id(c.skill_id) == "control_mp_cost&bd1_000"]
            self.assertEqual(got, [Decimal("0.05")], f"{who} 应当收到 +0.05 回复")
        # 消耗侧不该再有她这条
        self.assertEqual([c for c in mood_ledger(world, "路人甲").of(Bucket.CONSUME)
                          if base_skill_id(c.skill_id) == "control_mp_cost&bd1_000"], [])

    def test_登记不建模的模板不产生心情贡献(self):
        """X 族（非心情）与 M15b/M16：登记在册但**一条 clause 都不该有**。"""
        for tid, t in TEMPLATES.items():
            if t.tier == ModelTier.A4_NONE or tid in ("M15b", "M16"):
                self.assertEqual([k for k, s in SKILLS.items() if s.template_id == tid], [],
                                 f"{tid} 是登记不建模的模板，不该有 clause")

    # ------------------------------------------------------------ 工具
    def _trade(self, ops):
        return build_base_layout({"facilities": [
            {"type": "控制中枢", "level": 1, "operators": ["路人乙"]},
            {"type": "贸易站", "level": 3,
             "operators": [{**({"name": n} if isinstance(n, str) else n), "mood": m}
                           for n, m in ops.items()]},
        ]})


# ============================================================================
# L2 clause 级（全量）
# ============================================================================
class Test全量核对(unittest.TestCase):
    """L2：250 条 clause 逐条造场景核对（数值 + 桶 + 作用范围）。"""

    @classmethod
    def setUpClass(cls):
        cls.results = V.check_clauses()

    def test_250条clause全部参与核对(self):
        """核对集合必须**恰好**是 SKILLS 全集（新增 clause 自动进入核对，不会被漏掉）。"""
        self.assertEqual(len(self.results), CLAUSE_TOTAL)
        self.assertEqual({c.key for c in self.results}, set(SKILLS))

    def test_没有一条不符(self):
        bad = [(c.key, c.detail) for c in self.results if not c.ok]
        self.assertEqual(bad, [], f"有 {len(bad)} 条 clause 与模板口径不符")

    def test_每条都落到明确的结论上(self):
        """每条 clause 的结论只能是这几类之一（新增状态要显式登记，不许模糊）。"""
        allowed = {"ok", "unreachable", "event", "eliminate", "pool", "boost", "var"}
        self.assertEqual({c.status for c in self.results} - allowed, set())
        counts = {s: sum(1 for c in self.results if c.status == s) for s in allowed}
        # 绝大多数应当是"正常生效"，异常类别只占极少数（数字变了要有人解释）
        self.assertEqual(counts["ok"], 242)
        self.assertEqual(counts["unreachable"], 2)       # 潮汐守望「反之」两条（文档第 23 条）
        self.assertEqual(counts["eliminate"], 3)         # 槐琥 / 令 / 若叶睦
        self.assertEqual(counts["pool"], 1)              # 冰酿
        self.assertEqual(counts["boost"], 1)             # 摩根
        self.assertEqual(counts["event"], 1)             # 菲亚梅塔患难之交

    def test_核对器能发现引擎数值错(self):
        """注入式：把折算值整体 +1 → 必须大面积报"数值不符"（证明核对不是空转）。"""
        import mood_soc.rules as R
        orig = R._scaled_amount
        R._scaled_amount = lambda s, v=None, w=None, f=None, o=None: (True, s.value + 1, "")
        try:
            bad = [c for c in V.check_clauses() if not c.ok]
        finally:
            R._scaled_amount = orig
        self.assertGreaterEqual(len(bad), 200)

    def test_核对器能发现扩散失效(self):
        """注入式：去掉扩散路径 → 白名单那 15 条必须报"作用范围不符"。"""
        import mood_soc.rules as R
        orig = R._reaches
        R._reaches = lambda s, t, sp: bool(s.facility_types) and t in s.facility_types
        try:
            bad = [c.key for c in V.check_clauses() if not c.ok]
        finally:
            R._reaches = orig
        self.assertEqual(len(bad), 15)
        self.assertTrue(all(SKILLS[k] and base_skill_id(k) in
                            __import__("mood_soc.skills", fromlist=["x"]).SPREAD_SKILL_IDS
                            for k in bad))

    def test_核对器能发现阵营计数错(self):
        """注入式：把"每个该阵营干员"恒算成 1 人 → 7 条 per-count 技能必须报错。"""
        import mood_soc.rules as R
        orig = R._count_faction
        R._count_faction = lambda f, x: Decimal("1")
        try:
            bad = [c for c in V.check_clauses() if not c.ok]
        finally:
            R._count_faction = orig
        self.assertEqual(len(bad), 7)
        self.assertTrue(all(SKILLS[c.key].count_faction for c in bad))


# ============================================================================
# L3 上游描述对照
# ============================================================================
class Test描述对照(unittest.TestCase):
    """L3：数据表的 value 必须能在上游描述原文里找到同值同方向的数字。"""

    @classmethod
    def setUpClass(cls):
        cls.path = V.find_upstream()
        if cls.path is None:
            raise unittest.SkipTest(
                "没找到上游仓库（用 --agd 或环境变量 ARKNIGHTS_GAMEDATA 指定）")
        cls.data = V.load_upstream(cls.path)
        cls.buffs = cls.data["buffs"]
        cls.results = V.check_descriptions(cls.data)

    def test_每条clause都能定位到上游buff(self):
        """clause → buff 的 id 换算必须条条命中（换算是核对的前提）。"""
        missing = [k for k in SKILLS
                   if V.buff_id_of(base_skill_id(k)) not in self.buffs]
        self.assertEqual(missing, [])

    def test_描述里的数字与数据表一致(self):
        """上游描述里的数字 + 方向词，与数据表逐条一致（当前 0 处差异）。"""
        bad = [(d.key, d.detail) for d in self.results if not d.ok]
        self.assertEqual(bad, [], f"有 {len(bad)} 条与上游描述对不上")
        counts = {}
        for d in self.results:
            counts[d.status] = counts.get(d.status, 0) + 1
        self.assertEqual(counts.get("ok"), 246)
        self.assertEqual(counts.get("skip"), 4)          # value=0 的消除/事件类
        self.assertEqual(counts.get("known"), None)      # 已无"已登记差异"
        self.assertEqual(V.L3_KNOWN_DIFFS, {})

    def test_已登记差异必须还在差异状态(self):
        """自清理：`L3_KNOWN_DIFFS` 里的条目修好之后必须从表里删掉，否则本用例会红。"""
        for key, why in V.L3_KNOWN_DIFFS.items():
            self.assertTrue(why, f"{key} 的登记理由不能为空")
            desc = self.buffs[V.buff_id_of(base_skill_id(key))]["description"]
            self.assertEqual(V.check_description(key, SKILLS[key], desc).status, "known",
                             f"{key} 已经与描述一致了 → 请从 L3_KNOWN_DIFFS 里删掉")

    def test_描述对照能发现数值改错(self):
        """注入式：把某条的 value 改成一个描述里没有的数 → 必须报错。"""
        import dataclasses
        key = "dorm_rec_single_020#1"          # 慈悲 +0.75
        desc = self.buffs[V.buff_id_of(base_skill_id(key))]["description"]
        wrong = dataclasses.replace(SKILLS[key], value=Decimal("0.777"))
        self.assertEqual(V.check_description(key, SKILLS[key], desc).status, "ok")
        self.assertEqual(V.check_description(key, wrong, desc).status, "bad")


# ============================================================================
# 报告
# ============================================================================
class Test报告(unittest.TestCase):
    """报告是给人看的：内容必须完整，且**不能过期**。"""

    @classmethod
    def setUpClass(cls):
        cls.results = V.check_clauses()
        cls.upstream = V.find_upstream()
        cls.descs = V.check_descriptions(V.load_upstream(cls.upstream)) \
            if cls.upstream else None
        cls.text = V.build_report(cls.results, cls.descs, V.check_templates(), cls.upstream)

    def test_报告含六个章节(self):
        for want in ("# 技能核对报告（全量）", "## 一、总览", "## 二、模板使用",
                     "## 三、逐条 clause", "## 四、逐条 buff", "## 六、怎么复现"):
            self.assertIn(want, self.text)

    def test_报告逐条列出所有clause(self):
        for key in SKILLS:
            self.assertIn(f"| `{key}` |", self.text)

    def test_仓库里的报告没有过期(self):
        """`resources/skill_verify_report.md` 的规模数字必须与当前数据一致。"""
        self.assertTrue(REPORT_MD.exists(), "报告没生成过：跑 scripts/verify_skills.py --report")
        saved = REPORT_MD.read_text(encoding="utf-8")
        live = [ln for ln in self.text.splitlines()
                if ln.startswith("- 心情 clause（") or ln.startswith("- L2 clause 级：")]
        for ln in live:
            self.assertIn(ln, saved, "报告过期了：请重跑 scripts/verify_skills.py --report")


if __name__ == "__main__":
    unittest.main(verbosity=2)

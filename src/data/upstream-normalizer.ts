import type { UpstreamCatalogRaw } from "./upstream-types.js";

export interface NormalizedSkillUnlock {
  readonly skillId: string;
  readonly phase: string;
  readonly level: number;
}

export interface NormalizedOperator {
  readonly operatorId: string;
  readonly name: string;
  readonly maxManpowerRaw: number;
  readonly skillIds: readonly string[];
  readonly skillUnlocks: readonly NormalizedSkillUnlock[];
  readonly factionIds: readonly string[];
}

export interface NormalizedSkill {
  readonly skillId: string;
  readonly name: string;
  readonly category: string;
  readonly roomType: string;
  readonly description: string;
  readonly efficiencyRaw: number;
  readonly targets: readonly string[];
}

export interface NormalizedFacilityType {
  readonly facilityTypeId: string;
  readonly name: string;
  readonly description: string;
  readonly maxCount: number;
  readonly category: string;
  readonly size: { readonly row: number; readonly col: number };
  readonly phases: readonly {
    readonly level: number;
    readonly unlockConditionId: string;
    readonly electricity: number;
    readonly maxStationedNum: number;
    readonly manpowerCostRaw: number;
    readonly manpowerRecoverRaw?: number;
    readonly decorationLimit?: number;
  }[];
}

export interface NormalizedUpstreamCatalog {
  readonly source: UpstreamCatalogRaw["source"];
  readonly operators: readonly NormalizedOperator[];
  readonly skills: readonly NormalizedSkill[];
  readonly facilityTypes: readonly NormalizedFacilityType[];
  readonly constants: {
    readonly laborRecoverTime: number;
    readonly comfortManpowerRecoverFactor: number;
    readonly manpowerDisplayFactor: number;
    readonly tiredApThreshold: number;
  };
}

function compactIds(values: ReadonlyArray<string | null | undefined>): readonly string[] {
  return Object.freeze([...new Set(values.filter((value): value is string => Boolean(value)))]);
}

export function normalizeUpstreamCatalog(raw: UpstreamCatalogRaw): NormalizedUpstreamCatalog {
  // 全部目录按稳定 ID 排序，使相同输入与生成时间得到字节级一致的产物。
  const skills = Object.values(raw.buildingData.buffs)
    .map((buff) =>
      Object.freeze({
        skillId: buff.buffId,
        name: buff.buffName,
        category: buff.buffCategory,
        roomType: buff.roomType,
        description: buff.description,
        efficiencyRaw: buff.efficiency,
        targets: Object.freeze([...buff.targets]),
      }),
    )
    .sort((left, right) => left.skillId.localeCompare(right.skillId));

  const operators = Object.values(raw.buildingData.chars)
    .map((buildingCharacter) => {
      const character = raw.characterTable[buildingCharacter.charId];
      if (!character?.name) {
        throw new Error(`CATALOG_REFERENCE_MISSING: character_table.${buildingCharacter.charId}`);
      }
      // 精英阶段和等级保持上游解锁条件原值，后续由领域目录解释其可用性。
      const skillUnlocks = buildingCharacter.buffChar.flatMap((group) =>
        group.buffData.map((entry) =>
          Object.freeze({ skillId: entry.buffId, phase: entry.cond.phase, level: entry.cond.level }),
        ),
      );
      return Object.freeze({
        operatorId: buildingCharacter.charId,
        name: character.name,
        maxManpowerRaw: buildingCharacter.maxManpower,
        skillIds: compactIds(skillUnlocks.map((entry) => entry.skillId)),
        skillUnlocks: Object.freeze(skillUnlocks),
        // 只保留上游稳定 ID；当前阶段不猜测阵营显示名或成员集合语义。
        factionIds: compactIds([character.nationId, character.groupId, character.teamId]),
      });
    })
    .sort((left, right) => left.operatorId.localeCompare(right.operatorId));

  const facilityTypes = Object.values(raw.buildingData.rooms)
    .map((room) => {
      // 宿舍恢复与氛围上限位于 dormData，按阶段顺序与 rooms.DORMITORY 合并。
      const dormPhases = room.id === "DORMITORY" ? raw.buildingData.dormData.phases : undefined;
      return Object.freeze({
        facilityTypeId: room.id,
        name: room.name,
        description: room.description,
        maxCount: room.maxCount,
        category: room.category,
        size: Object.freeze({ ...room.size }),
        phases: Object.freeze(
          room.phases.map((phase, index) =>
            Object.freeze({
              level: index + 1,
              unlockConditionId: phase.unlockCondId,
              electricity: phase.electricity,
              maxStationedNum: phase.maxStationedNum,
              // Raw 字段仍是上游数值，尚未转换为领域层十进制定点或计算公式。
              manpowerCostRaw: phase.manpowerCost,
              ...(dormPhases?.[index]
                ? {
                    manpowerRecoverRaw: dormPhases[index].manpowerRecover,
                    decorationLimit: dormPhases[index].decorationLimit,
                  }
                : {}),
            }),
          ),
        ),
      });
    })
    .sort((left, right) => left.facilityTypeId.localeCompare(right.facilityTypeId));

  return Object.freeze({
    source: raw.source,
    operators: Object.freeze(operators),
    skills: Object.freeze(skills),
    facilityTypes: Object.freeze(facilityTypes),
    constants: Object.freeze({
      laborRecoverTime: raw.buildingData.laborRecoverTime,
      comfortManpowerRecoverFactor: raw.buildingData.comfortManpowerRecoverFactor,
      manpowerDisplayFactor: raw.buildingData.manpowerDisplayFactor,
      tiredApThreshold: raw.buildingData.tiredApThreshold,
    }),
  });
}

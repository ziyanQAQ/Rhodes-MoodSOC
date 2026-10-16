/** 将已校验的上游资料规范化为有序目录记录，保留原值与来源，不推断技能机制。 */
import type { CatalogDefinition, CatalogConstants, FactionLinkRecord } from "./catalog-types.js";
import type { UpstreamCatalogRaw } from "./upstream-types.js";
import { CatalogValidationError, validateUpstreamCatalog } from "./validator.js";

export type NormalizedUpstreamCatalog = Omit<CatalogDefinition, "version"> & {
  readonly source: UpstreamCatalogRaw["source"];
};

const excel = "zh_CN/gamedata/excel/";
// 使用明确的 UTF-16 字符顺序，不受操作系统区域设置影响。
export function compareIds(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function uniqueIds(ids: readonly string[]): readonly string[] {
  return [...new Set(ids)].sort(compareIds);
}

export function normalizeUpstreamCatalog(input: unknown): NormalizedUpstreamCatalog {
  const checked = validateUpstreamCatalog(input);
  if (!checked.ok) throw new CatalogValidationError(checked.error);
  const raw = checked.value;
  const skills = Object.values(raw.buildingData.buffs).map((buff) => ({
    skillId: buff.buffId, sourcePath: `${excel}building_data.json#/buffs/${buff.buffId}`,
    name: buff.buffName, category: buff.buffCategory, roomType: buff.roomType,
    description: buff.description, efficiencyRaw: buff.efficiency, targets: [...buff.targets],
    // 来源保真不等于机制已建模；不能把这些条目直接作为可执行技能。
    modelingStatus: "unmodeled" as const,
  })).sort((a, b) => compareIds(a.skillId, b.skillId));

  const operators = Object.values(raw.buildingData.chars).map((entry) => {
    const character = raw.characterTable[entry.charId]!;
    const sourcePath = `${excel}building_data.json#/chars/${entry.charId}`;
    const skillUnlocks = entry.buffChar.flatMap((group, groupIndex) => group.buffData.map((buff, index) => ({
      skillId: buff.buffId, phase: buff.cond.phase, level: buff.cond.level, groupIndex,
      sourcePath: `${sourcePath}/buffChar/${groupIndex}/buffData/${index}`,
    })));
    const factionLinks: FactionLinkRecord[] = [];
    for (const [field, kind] of [["nationId", "nation"], ["groupId", "group"], ["teamId", "team"]] as const) {
      const id = character[field];
      if (id !== null) factionLinks.push({ factionId: id, kind, sourcePath: `${excel}character_table.json#/${entry.charId}/${field}` });
    }
    return {
      operatorId: entry.charId, name: character.name, sourcePath, maxManpowerRaw: entry.maxManpower,
      skillIds: uniqueIds(skillUnlocks.map((unlock) => unlock.skillId)), skillUnlocks,
      factionIds: uniqueIds(factionLinks.map((link) => link.factionId)), factionLinks,
    };
  }).sort((a, b) => compareIds(a.operatorId, b.operatorId));

  const facilityTypes = Object.values(raw.buildingData.rooms).map((room) => ({
    facilityTypeId: room.id, sourcePath: `${excel}building_data.json#/rooms/${room.id}`,
    name: room.name, description: room.description, maxCount: room.maxCount,
    category: room.category, size: { ...room.size },
    phases: room.phases.map((phase, index) => {
      // 已校验两个阶段表长度；等级按上游阶段顺序保留，不在此推算小时回复。
      const dorm = room.id === "DORMITORY" ? raw.buildingData.dormData.phases[index] : undefined;
      return {
        level: index + 1, sourcePath: `${excel}building_data.json#/rooms/${room.id}/phases/${index}`,
        unlockConditionId: phase.unlockCondId, electricity: phase.electricity,
        maxStationedNum: phase.maxStationedNum, manpowerCostRaw: phase.manpowerCost,
        ...(dorm ? { manpowerRecoverRaw: dorm.manpowerRecover, decorationLimit: dorm.decorationLimit,
          dormSourcePath: `${excel}building_data.json#/dormData/phases/${index}` } : {}),
      };
    }),
  })).sort((a, b) => compareIds(a.facilityTypeId, b.facilityTypeId));

  const factions = Object.values(raw.handbookTeamTable).map((team) => {
    const members = operators.flatMap((operator) => operator.factionLinks
      .filter((link) => link.factionId === team.powerId)
      .map((link) => ({ operatorId: operator.operatorId, kind: link.kind, sourcePath: link.sourcePath })));
    return { factionId: team.powerId, name: team.powerName,
      sourcePath: `${excel}handbook_team_table.json#/${team.powerId}`, membershipScope: "catalog-direct-links" as const,
      operatorIds: uniqueIds(members.map((member) => member.operatorId)), members };
  }).sort((a, b) => compareIds(a.factionId, b.factionId));

  const constant = (field: keyof CatalogConstants) => ({
    rawValue: raw.buildingData[field], sourcePath: `${excel}building_data.json#/${field}`, unitStatus: "unverified" as const,
  });
  return { source: structuredClone(raw.source), operators, skills, facilityTypes, factions, constants: {
    laborRecoverTime: constant("laborRecoverTime"), comfortManpowerRecoverFactor: constant("comfortManpowerRecoverFactor"),
    manpowerDisplayFactor: constant("manpowerDisplayFactor"), tiredApThreshold: constant("tiredApThreshold"),
  } };
}

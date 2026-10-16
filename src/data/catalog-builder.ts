/** 校验目录记录及跨记录引用，构建可按稳定 ID 查询的深度只读快照。 */
import type {
  CatalogBuildError,
  CatalogBuildResult,
  CatalogDefinition,
  CatalogSnapshot,
  FactionRecord,
  FacilityTypeRecord,
  OperatorRecord,
  SkillRecord,
} from "./catalog-types.js";
import { validateCatalogDefinition } from "./validator.js";

function failure(
  code: CatalogBuildError["code"],
  path: string,
  message: string,
): CatalogBuildResult {
  return { ok: false, error: { code, path, message } };
}

function freezeOperator(record: OperatorRecord): OperatorRecord {
  // 同时复制并冻结嵌套数组，避免调用方在构建后通过原始引用修改快照。
  return immutableCopy(record);
}

function freezeFaction(record: FactionRecord): FactionRecord {
  return immutableCopy(record);
}

function freezeRecord<T extends SkillRecord | FacilityTypeRecord>(record: T): T {
  return immutableCopy(record);
}

function immutableCopy<T>(value: T): T {
  const copy = structuredClone(value);
  const freeze = (entry: unknown): void => {
    if (entry !== null && typeof entry === "object") {
      Object.values(entry).forEach(freeze);
      Object.freeze(entry);
    }
  };
  freeze(copy);
  return copy;
}

export function buildCatalogSnapshot(input: unknown): CatalogBuildResult {
  const checked = validateCatalogDefinition(input);
  if (!checked.ok) return checked;
  const definition: CatalogDefinition = checked.value;
  // 校验顺序保持稳定：来源元数据与必填字段、ID 唯一性、最后检查跨记录引用。
  const { version } = definition;
  if (!version.id || !version.source || !version.generatedAt) {
    return failure("CATALOG_SOURCE_INVALID", "version", "Catalog version metadata is required.");
  }

  const requiredText: Array<[string, string]> = [];
  for (const [position, operator] of definition.operators.entries()) {
    requiredText.push([`operators[${position}].operatorId`, operator.operatorId]);
    requiredText.push([`operators[${position}].name`, operator.name]);
  }
  for (const [position, skill] of definition.skills.entries()) {
    requiredText.push([`skills[${position}].skillId`, skill.skillId]);
    requiredText.push([`skills[${position}].sourcePath`, skill.sourcePath]);
  }
  for (const [position, facilityType] of definition.facilityTypes.entries()) {
    requiredText.push([`facilityTypes[${position}].facilityTypeId`, facilityType.facilityTypeId]);
    requiredText.push([`facilityTypes[${position}].sourcePath`, facilityType.sourcePath]);
  }
  for (const [position, faction] of definition.factions.entries()) {
    requiredText.push([`factions[${position}].factionId`, faction.factionId]);
    requiredText.push([`factions[${position}].name`, faction.name]);
  }
  const missingText = requiredText.find(([, value]) => !value.trim());
  if (missingText) {
    return failure("CATALOG_SOURCE_INVALID", missingText[0], "Required source data is missing.");
  }

  const operators = definition.operators.map(freezeOperator);
  const skills = definition.skills.map(freezeRecord);
  const facilityTypes = definition.facilityTypes.map(freezeRecord);
  const factions = definition.factions.map(freezeFaction);
  const operatorById = new Map<string, OperatorRecord>();
  const skillById = new Map<string, SkillRecord>();
  const facilityTypeById = new Map<string, FacilityTypeRecord>();
  const factionById = new Map<string, FactionRecord>();

  const addUnique = <T>(
    records: readonly T[],
    getId: (record: T) => string,
    index: Map<string, T>,
    path: string,
  ): CatalogBuildResult | undefined => {
    for (const [position, record] of records.entries()) {
      const id = getId(record);
      if (index.has(id)) {
        return failure("CATALOG_DUPLICATE_ID", `${path}[${position}]`, `Duplicate ID: ${id}`);
      }
      index.set(id, record);
    }
    return undefined;
  };

  const duplicateError =
    addUnique(operators, (record) => record.operatorId, operatorById, "operators") ??
    addUnique(skills, (record) => record.skillId, skillById, "skills") ??
    addUnique(facilityTypes, (record) => record.facilityTypeId, facilityTypeById, "facilityTypes") ??
    addUnique(factions, (record) => record.factionId, factionById, "factions");
  if (duplicateError) return duplicateError;

  for (const [position, operator] of operators.entries()) {
    for (const [referencePosition, skillId] of operator.skillIds.entries()) {
      if (!skillById.has(skillId)) {
        return failure(
          "CATALOG_REFERENCE_MISSING",
          `operators[${position}].skillIds[${referencePosition}]`,
          `Unknown skill ID: ${skillId}`,
        );
      }
    }
    for (const [referencePosition, factionId] of operator.factionIds.entries()) {
      if (!factionById.has(factionId)) {
        return failure(
          "CATALOG_REFERENCE_MISSING",
          `operators[${position}].factionIds[${referencePosition}]`,
          `Unknown faction ID: ${factionId}`,
        );
      }
    }
  }

  const sameIds = (left: readonly string[], right: readonly string[]): boolean => {
    const leftIds = new Set(left);
    const rightIds = new Set(right);
    return leftIds.size === left.length && leftIds.size === rightIds.size &&
      [...leftIds].every((id) => rightIds.has(id));
  };
  for (const [position, operator] of operators.entries()) {
    const unlockSources = new Set<string>();
    for (const [index, unlock] of operator.skillUnlocks.entries()) {
      if (!skillById.has(unlock.skillId)) return failure("CATALOG_REFERENCE_MISSING",
        `operators[${position}].skillUnlocks[${index}].skillId`, "Unknown unlock skill ID.");
      if (unlockSources.has(unlock.sourcePath)) return failure("CATALOG_FIELD_INVALID",
        `operators[${position}].skillUnlocks[${index}]`, "Duplicate unlock source path.");
      unlockSources.add(unlock.sourcePath);
    }
    if (!sameIds(operator.skillIds, operator.skillUnlocks.map((unlock) => unlock.skillId))) {
      return failure("CATALOG_FIELD_INVALID", `operators[${position}].skillUnlocks`, "Unlocks differ from skill IDs.");
    }
    if (!sameIds(operator.factionIds, operator.factionLinks.map((link) => link.factionId))) {
      return failure("CATALOG_FIELD_INVALID", `operators[${position}].factionLinks`, "Links differ from faction IDs.");
    }
    for (const [index, link] of operator.factionLinks.entries()) {
      const faction = factionById.get(link.factionId)!;
      if (!faction.members.some((member) => member.operatorId === operator.operatorId && member.kind === link.kind && member.sourcePath === link.sourcePath)) {
        return failure("CATALOG_FIELD_INVALID", `operators[${position}].factionLinks[${index}]`, "Reverse membership missing.");
      }
    }
  }
  for (const [position, skill] of skills.entries()) {
    if (!facilityTypeById.has(skill.roomType)) return failure("CATALOG_REFERENCE_MISSING",
      `skills[${position}].roomType`, "Unknown facility ID.");
  }

  for (const [position, faction] of factions.entries()) {
    for (const [referencePosition, operatorId] of faction.operatorIds.entries()) {
      if (!operatorById.has(operatorId)) {
        return failure(
          "CATALOG_REFERENCE_MISSING",
          `factions[${position}].operatorIds[${referencePosition}]`,
          `Unknown operator ID: ${operatorId}`,
        );
      }
    }
    if (!sameIds(faction.operatorIds, faction.members.map((member) => member.operatorId))) {
      return failure("CATALOG_FIELD_INVALID", `factions[${position}].members`, "Members differ from operator IDs.");
    }
    const memberKeys = new Set<string>();
    for (const [index, member] of faction.members.entries()) {
      const key = `${member.operatorId}:${member.kind}`;
      if (memberKeys.has(key)) return failure("CATALOG_DUPLICATE_ID", `factions[${position}].members[${index}]`, "Duplicate member link.");
      memberKeys.add(key);
      const operator = operatorById.get(member.operatorId)!;
      if (!operator.factionLinks.some((link) => link.factionId === faction.factionId && link.kind === member.kind && link.sourcePath === member.sourcePath)) {
        return failure("CATALOG_FIELD_INVALID", `factions[${position}].members[${index}]`, "Reverse faction link missing.");
      }
    }
  }

  const immutableVersion = Object.freeze({ ...version });
  // Map 仅由闭包持有；调用方只能使用稳定 ID 查询，不能直接修改索引。
  const snapshot: CatalogSnapshot = {
    version: immutableVersion,
    constants: immutableCopy(definition.constants),
    operator: (operatorId) => operatorById.get(operatorId),
    skill: (skillId) => skillById.get(skillId),
    facilityType: (facilityTypeId) => facilityTypeById.get(facilityTypeId),
    faction: (factionId) => factionById.get(factionId),
  };

  return { ok: true, snapshot: Object.freeze(snapshot) };
}

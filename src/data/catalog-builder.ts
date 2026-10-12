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

function failure(
  code: CatalogBuildError["code"],
  path: string,
  message: string,
): CatalogBuildResult {
  return { ok: false, error: { code, path, message } };
}

function freezeOperator(record: OperatorRecord): OperatorRecord {
  return Object.freeze({
    ...record,
    skillIds: Object.freeze([...record.skillIds]),
    factionIds: Object.freeze([...record.factionIds]),
  });
}

function freezeFaction(record: FactionRecord): FactionRecord {
  return Object.freeze({ ...record, operatorIds: Object.freeze([...record.operatorIds]) });
}

function freezeRecord<T extends SkillRecord | FacilityTypeRecord>(record: T): T {
  return Object.freeze({ ...record }) as unknown as T;
}

export function buildCatalogSnapshot(definition: CatalogDefinition): CatalogBuildResult {
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
  }

  const immutableVersion = Object.freeze({ ...version });
  const snapshot: CatalogSnapshot = {
    version: immutableVersion,
    operator: (operatorId) => operatorById.get(operatorId),
    skill: (skillId) => skillById.get(skillId),
    facilityType: (facilityTypeId) => facilityTypeById.get(facilityTypeId),
    faction: (factionId) => factionById.get(factionId),
  };

  return { ok: true, snapshot: Object.freeze(snapshot) };
}

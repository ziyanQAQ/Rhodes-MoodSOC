/** 对未知运行时输入执行上游资料、目录记录及清单校验，报告可定位的结构化错误。 */
import type { CatalogBuildError, CatalogBuildErrorCode, CatalogDefinition } from "./catalog-types.js";
import type { UpstreamCatalogRaw } from "./upstream-types.js";
import { CATALOG_SCHEMA_VERSION, CATALOG_GENERATOR_VERSION, CATALOG_ARTIFACT_NAMES } from "./catalog-generator.js";
import type { CatalogManifest } from "./catalog-generator.js";

export class CatalogValidationError extends Error {
  constructor(readonly detail: CatalogBuildError) {
    super(`${detail.code}: ${detail.path}: ${detail.message}`);
    this.name = "CatalogValidationError";
  }
}

export type ValidationResult<T> =
  | { readonly ok: true; readonly value: T }
  | { readonly ok: false; readonly error: CatalogBuildError };

function invalid(path: string, message: string, code: CatalogBuildErrorCode = "CATALOG_FIELD_INVALID"): never {
  throw new CatalogValidationError({ code, path, message });
}

function object(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) invalid(path, "Expected object.");
  return value as Record<string, unknown>;
}

function array(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) invalid(path, "Expected array.");
  return value;
}

function text(value: unknown, path: string): string {
  if (typeof value !== "string" || !value.trim()) invalid(path, "Expected non-empty string.");
  return value;
}

function finite(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) invalid(path, "Expected finite number.");
  return value;
}

function integer(value: unknown, path: string, minimum: number): number {
  const number = finite(value, path);
  if (!Number.isSafeInteger(number) || number < minimum) invalid(path, `Expected integer >= ${minimum}.`);
  return number;
}

function recordTable(value: unknown, path: string, check: (item: Record<string, unknown>, path: string, id: string) => void): void {
  for (const [id, entry] of Object.entries(object(value, path))) {
    check(object(entry, `${path}.${id}`), `${path}.${id}`, id);
  }
}

function records(value: unknown, path: string, check: (item: Record<string, unknown>, path: string) => void): void {
  array(value, path).forEach((entry, index) => check(object(entry, `${path}[${index}]`), `${path}[${index}]`));
}

function texts(value: unknown, path: string): void {
  array(value, path).forEach((entry, index) => text(entry, `${path}[${index}]`));
}

function factionKind(value: unknown, path: string): void {
  if (value !== "nation" && value !== "group" && value !== "team") invalid(path, "Unknown faction link kind.");
}

function elitePhase(value: unknown, path: string): void {
  // 已核对当前上游 cond.phase；新阶段需审阅映射版本后支持。
  if (value !== "PHASE_0" && value !== "PHASE_1" && value !== "PHASE_2") invalid(path, "Unsupported elite phase.");
}

export function validateCatalogManifest(value: unknown): ValidationResult<CatalogManifest> {
  try {
    const manifest = object(value, "manifest");
    if (text(manifest.schemaVersion, "manifest.schemaVersion") !== CATALOG_SCHEMA_VERSION) {
      invalid("manifest.schemaVersion", "Unsupported catalog schema.", "CATALOG_VERSION_UNSUPPORTED");
    }
    if (text(manifest.generatorVersion, "manifest.generatorVersion") !== CATALOG_GENERATOR_VERSION) {
      invalid("manifest.generatorVersion", "Unsupported catalog mapping.", "CATALOG_VERSION_UNSUPPORTED");
    }
    if (!/^agd-[a-f0-9]{64}$/.test(text(manifest.catalogVersion, "manifest.catalogVersion"))) invalid("manifest.catalogVersion", "Invalid catalog identity.");
    if (Number.isNaN(Date.parse(text(manifest.generatedAt, "manifest.generatedAt")))) invalid("manifest.generatedAt", "Invalid timestamp.");
    const source = object(manifest.source, "manifest.source");
    if (source.kind !== "local-archive") invalid("manifest.source.kind", "Unsupported source kind.");
    const files = object(source.files, "manifest.source.files");
    const fingerprint = (value: unknown, path: string): Record<string, unknown> => {
      const record = object(value, path);
      if (!/^[a-f0-9]{64}$/.test(text(record.sha256, `${path}.sha256`))) invalid(`${path}.sha256`, "Expected SHA-256.");
      integer(record.bytes, `${path}.bytes`, 0);
      return record;
    };
    for (const [name, fileName] of [["buildingData", "building_data.json"], ["characterTable", "character_table.json"],
      ["gamedataConst", "gamedata_const.json"], ["handbookTeamTable", "handbook_team_table.json"]] as const) {
      const p = `manifest.source.files.${name}`;
      const file = fingerprint(files[name], p);
      if (file.fileName !== fileName) invalid(`${p}.fileName`, "Expected logical source file name.");
    }
    const counts = object(manifest.counts, "manifest.counts");
    for (const field of ["operators", "skills", "facilities", "factions"]) integer(counts[field], `manifest.counts.${field}`, 0);
    const artifacts = object(manifest.artifacts, "manifest.artifacts");
    for (const name of CATALOG_ARTIFACT_NAMES) fingerprint(artifacts[name], `manifest.artifacts.${name}`);
    return { ok: true, value: value as CatalogManifest };
  } catch (error) {
    if (error instanceof CatalogValidationError) return { ok: false, error: error.detail };
    throw error;
  }
}

/** 生成文件和内存调用共用校验，避免 TypeScript 断言绕过公开边界。 */
export function validateCatalogDefinition(value: unknown): ValidationResult<CatalogDefinition> {
  try {
    const catalog = object(value, "catalog");
    const version = object(catalog.version, "version");
    for (const field of ["id", "source", "generatedAt"]) text(version[field], `version.${field}`);
    if (Number.isNaN(Date.parse(String(version.generatedAt)))) invalid("version.generatedAt", "Invalid timestamp.");
    records(catalog.operators, "operators", (record, path) => {
      for (const field of ["operatorId", "name", "sourcePath"]) text(record[field], `${path}.${field}`);
      finite(record.maxManpowerRaw, `${path}.maxManpowerRaw`);
      texts(record.skillIds, `${path}.skillIds`);
      texts(record.factionIds, `${path}.factionIds`);
      records(record.skillUnlocks, `${path}.skillUnlocks`, (unlock, p) => {
        for (const field of ["skillId", "phase", "sourcePath"]) text(unlock[field], `${p}.${field}`);
        elitePhase(unlock.phase, `${p}.phase`);
        integer(unlock.level, `${p}.level`, 1);
        integer(unlock.groupIndex, `${p}.groupIndex`, 0);
      });
      records(record.factionLinks, `${path}.factionLinks`, (link, p) => {
        text(link.factionId, `${p}.factionId`); text(link.sourcePath, `${p}.sourcePath`);
        factionKind(link.kind, `${p}.kind`);
      });
    });
    records(catalog.skills, "skills", (record, path) => {
      for (const field of ["skillId", "sourcePath", "name", "category", "roomType", "description"]) text(record[field], `${path}.${field}`);
      finite(record.efficiencyRaw, `${path}.efficiencyRaw`);
      texts(record.targets, `${path}.targets`);
      if (record.modelingStatus !== "unmodeled") invalid(`${path}.modelingStatus`, "Unsupported modeling status.");
    });
    records(catalog.facilityTypes, "facilityTypes", (record, path) => {
      for (const field of ["facilityTypeId", "sourcePath", "name", "category"]) text(record[field], `${path}.${field}`);
      if (record.description !== null && typeof record.description !== "string") invalid(`${path}.description`, "Expected string or null.");
      if (!Number.isSafeInteger(finite(record.maxCount, `${path}.maxCount`))) invalid(`${path}.maxCount`, "Expected safe integer.");
      const size = object(record.size, `${path}.size`);
      integer(size.row, `${path}.size.row`, 1); integer(size.col, `${path}.size.col`, 1);
      const phases = array(record.phases, `${path}.phases`);
      if (!phases.length) invalid(`${path}.phases`, "At least one phase required.");
      records(phases, `${path}.phases`, (phase, p) => {
        integer(phase.level, `${p}.level`, 1);
        for (const field of ["sourcePath", "unlockConditionId"]) text(phase[field], `${p}.${field}`);
        finite(phase.electricity, `${p}.electricity`); finite(phase.manpowerCostRaw, `${p}.manpowerCostRaw`);
        integer(phase.maxStationedNum, `${p}.maxStationedNum`, 0);
        if (record.facilityTypeId === "DORMITORY") {
          finite(phase.manpowerRecoverRaw, `${p}.manpowerRecoverRaw`);
          integer(phase.decorationLimit, `${p}.decorationLimit`, 0);
          text(phase.dormSourcePath, `${p}.dormSourcePath`);
        } else if (phase.manpowerRecoverRaw !== undefined || phase.decorationLimit !== undefined || phase.dormSourcePath !== undefined) {
          invalid(p, "Dormitory fields on non-dormitory phase.");
        }
      });
      phases.forEach((entry, index) => {
        if (object(entry, path).level !== index + 1) invalid(`${path}.phases[${index}].level`, "Expected contiguous phase levels.");
      });
    });
    records(catalog.factions, "factions", (record, path) => {
      for (const field of ["factionId", "name", "sourcePath"]) text(record[field], `${path}.${field}`);
      texts(record.operatorIds, `${path}.operatorIds`);
      if (record.membershipScope !== "catalog-direct-links") invalid(`${path}.membershipScope`, "Unsupported membership scope.");
      records(record.members, `${path}.members`, (member, p) => {
        text(member.operatorId, `${p}.operatorId`); text(member.sourcePath, `${p}.sourcePath`);
        factionKind(member.kind, `${p}.kind`);
      });
    });
    const constants = object(catalog.constants, "constants");
    for (const field of ["laborRecoverTime", "comfortManpowerRecoverFactor", "manpowerDisplayFactor", "tiredApThreshold"]) {
      const p = `constants.${field}`;
      const record = object(constants[field], p);
      finite(record.rawValue, `${p}.rawValue`); text(record.sourcePath, `${p}.sourcePath`);
      if (record.unitStatus !== "unverified") invalid(`${p}.unitStatus`, "Unsupported unit status.");
    }
    return { ok: true, value: value as CatalogDefinition };
  } catch (error) {
    if (error instanceof CatalogValidationError) return { ok: false, error: error.detail };
    throw error;
  }
}

/** 接受 unknown 并检查实际读取字段；未知新增字段不影响既有导入。 */
export function validateUpstreamCatalog(value: unknown): ValidationResult<UpstreamCatalogRaw> {
  try {
    const raw = object(value, "catalog");
    const source = object(raw.source, "source");
    if (source.kind !== "local-archive") invalid("source.kind", "Unsupported source kind.");
    text(source.root, "source.root");
    const files = object(source.files, "source.files");
    for (const name of ["buildingData", "characterTable", "gamedataConst", "handbookTeamTable"]) {
      const path = `source.files.${name}`;
      const file = object(files[name], path);
      text(file.path, `${path}.path`);
      if (!/^[a-f0-9]{64}$/.test(text(file.sha256, `${path}.sha256`))) invalid(`${path}.sha256`, "Expected SHA-256.");
      integer(file.bytes, `${path}.bytes`, 0);
    }
    const building = object(raw.buildingData, "buildingData");
    object(raw.gamedataConst, "gamedataConst");
    const characters = object(raw.characterTable, "characterTable");
    const buffs = object(building.buffs, "buildingData.buffs");
    const rooms = object(building.rooms, "buildingData.rooms");
    const teams = object(raw.handbookTeamTable, "handbookTeamTable");
    recordTable(teams, "handbookTeamTable", (team, path, id) => {
      if (text(team.powerId, `${path}.powerId`) !== id) invalid(`${path}.powerId`, "ID differs from record key.");
      text(team.powerName, `${path}.powerName`);
    });
    for (const field of ["laborRecoverTime", "comfortManpowerRecoverFactor", "manpowerDisplayFactor", "tiredApThreshold"]) {
      finite(building[field], `buildingData.${field}`);
    }
    recordTable(building.buffs, "buildingData.buffs", (buff, path, id) => {
      if (text(buff.buffId, `${path}.buffId`) !== id) invalid(`${path}.buffId`, "ID differs from record key.");
      for (const field of ["buffName", "buffCategory", "roomType", "description"]) text(buff[field], `${path}.${field}`);
      if (!Object.hasOwn(rooms, String(buff.roomType))) invalid(`${path}.roomType`, "Unknown facility ID.", "CATALOG_REFERENCE_MISSING");
      finite(buff.efficiency, `${path}.efficiency`);
      array(buff.targets, `${path}.targets`).forEach((target, index) => text(target, `${path}.targets[${index}]`));
    });
    recordTable(building.rooms, "buildingData.rooms", (room, path, id) => {
      if (text(room.id, `${path}.id`) !== id) invalid(`${path}.id`, "ID differs from record key.");
      for (const field of ["name", "category"]) text(room[field], `${path}.${field}`);
      // 上游特殊设施描述为 null、数量为 -1；保留原值，不猜测哨兵值语义。
      if (room.description !== null && typeof room.description !== "string") invalid(`${path}.description`, "Expected string or null.");
      const maxCount = finite(room.maxCount, `${path}.maxCount`);
      if (!Number.isSafeInteger(maxCount)) invalid(`${path}.maxCount`, "Expected safe integer.");
      const size = object(room.size, `${path}.size`);
      integer(size.row, `${path}.size.row`, 1);
      integer(size.col, `${path}.size.col`, 1);
      array(room.phases, `${path}.phases`).forEach((entry, index) => {
        const p = `${path}.phases[${index}]`;
        const phase = object(entry, p);
        text(phase.unlockCondId, `${p}.unlockCondId`);
        finite(phase.electricity, `${p}.electricity`);
        finite(phase.manpowerCost, `${p}.manpowerCost`);
        integer(phase.maxStationedNum, `${p}.maxStationedNum`, 0);
      });
    });
    const dorm = object(building.dormData, "buildingData.dormData");
    const dormRoom = object(rooms.DORMITORY, "buildingData.rooms.DORMITORY");
    if (array(dorm.phases, "buildingData.dormData.phases").length !== array(dormRoom.phases, "buildingData.rooms.DORMITORY.phases").length) {
      invalid("buildingData.dormData.phases", "Dormitory phase count differs from room phases.");
    }
    array(dorm.phases, "buildingData.dormData.phases").forEach((entry, index) => {
      const p = `buildingData.dormData.phases[${index}]`;
      const phase = object(entry, p);
      finite(phase.manpowerRecover, `${p}.manpowerRecover`);
      integer(phase.decorationLimit, `${p}.decorationLimit`, 0);
    });
    recordTable(building.chars, "buildingData.chars", (character, path, id) => {
      if (text(character.charId, `${path}.charId`) !== id) invalid(`${path}.charId`, "ID differs from record key.");
      finite(character.maxManpower, `${path}.maxManpower`);
      if (!Object.hasOwn(characters, id)) invalid(`${path}.charId`, "Unknown character ID.", "CATALOG_REFERENCE_MISSING");
      const info = object(characters[id], `characterTable.${id}`);
      text(info.name, `characterTable.${id}.name`);
      for (const field of ["nationId", "groupId", "teamId"]) {
        if (info[field] !== null) {
          const factionId = text(info[field], `characterTable.${id}.${field}`);
          if (!Object.hasOwn(teams, factionId)) invalid(`characterTable.${id}.${field}`, "Unknown faction ID.", "CATALOG_REFERENCE_MISSING");
        }
      }
      array(character.buffChar, `${path}.buffChar`).forEach((entry, groupIndex) => {
        const p = `${path}.buffChar[${groupIndex}]`;
        const group = object(entry, p);
        array(group.buffData, `${p}.buffData`).forEach((entry, index) => {
          const q = `${p}.buffData[${index}]`;
          const buff = object(entry, q);
          const buffId = text(buff.buffId, `${q}.buffId`);
          if (!Object.hasOwn(buffs, buffId)) invalid(`${q}.buffId`, "Unknown skill ID.", "CATALOG_REFERENCE_MISSING");
          const condition = object(buff.cond, `${q}.cond`);
          text(condition.phase, `${q}.cond.phase`);
          elitePhase(condition.phase, `${q}.cond.phase`);
          integer(condition.level, `${q}.cond.level`, 1);
        });
      });
    });
    // 此处断言建立在运行时字段检查之后，而不是用 TS 类型替代数据验证。
    return { ok: true, value: value as UpstreamCatalogRaw };
  } catch (error) {
    if (error instanceof CatalogValidationError) return { ok: false, error: error.detail };
    throw error;
  }
}

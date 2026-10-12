export interface CatalogVersion {
  readonly id: string;
  readonly source: string;
  readonly generatedAt: string;
}

export interface OperatorRecord {
  readonly operatorId: string;
  readonly name: string;
  readonly skillIds: readonly string[];
  readonly factionIds: readonly string[];
}

export interface SkillRecord {
  readonly skillId: string;
  readonly sourcePath: string;
}

export interface FacilityTypeRecord {
  readonly facilityTypeId: string;
  readonly sourcePath: string;
}

export interface FactionRecord {
  readonly factionId: string;
  readonly name: string;
  readonly operatorIds: readonly string[];
}

export interface CatalogDefinition {
  readonly version: CatalogVersion;
  readonly operators: readonly OperatorRecord[];
  readonly skills: readonly SkillRecord[];
  readonly facilityTypes: readonly FacilityTypeRecord[];
  readonly factions: readonly FactionRecord[];
}

export interface CatalogSnapshot {
  readonly version: CatalogVersion;
  operator(operatorId: string): OperatorRecord | undefined;
  skill(skillId: string): SkillRecord | undefined;
  facilityType(facilityTypeId: string): FacilityTypeRecord | undefined;
  faction(factionId: string): FactionRecord | undefined;
}

export type CatalogBuildErrorCode =
  | "CATALOG_SOURCE_INVALID"
  | "CATALOG_DUPLICATE_ID"
  | "CATALOG_REFERENCE_MISSING";

export interface CatalogBuildError {
  readonly code: CatalogBuildErrorCode;
  readonly message: string;
  readonly path: string;
}

export type CatalogBuildResult =
  | { readonly ok: true; readonly snapshot: CatalogSnapshot }
  | { readonly ok: false; readonly error: CatalogBuildError };

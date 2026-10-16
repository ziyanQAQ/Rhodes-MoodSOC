/** 定义静态目录的统一记录、来源信息、只读查询接口与结构化错误类型。 */
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
  readonly sourcePath: string;
  readonly maxManpowerRaw: number;
  readonly skillUnlocks: readonly SkillUnlockRecord[];
  readonly factionLinks: readonly FactionLinkRecord[];
}

export interface SkillUnlockRecord {
  readonly skillId: string;
  readonly phase: string;
  readonly level: number;
  readonly groupIndex: number;
  readonly sourcePath: string;
}

export interface FactionLinkRecord {
  readonly factionId: string;
  readonly kind: "nation" | "group" | "team";
  readonly sourcePath: string;
}

export interface SkillRecord {
  readonly skillId: string;
  readonly sourcePath: string;
  readonly name: string;
  readonly category: string;
  readonly roomType: string;
  readonly description: string;
  readonly efficiencyRaw: number;
  readonly targets: readonly string[];
  readonly modelingStatus: "unmodeled";
}

export interface FacilityTypeRecord {
  readonly facilityTypeId: string;
  readonly sourcePath: string;
  readonly name: string;
  readonly description: string | null;
  readonly maxCount: number;
  readonly category: string;
  readonly size: { readonly row: number; readonly col: number };
  readonly phases: readonly FacilityPhaseRecord[];
}

export interface FacilityPhaseRecord {
  readonly level: number;
  readonly sourcePath: string;
  readonly unlockConditionId: string;
  readonly electricity: number;
  readonly maxStationedNum: number;
  readonly manpowerCostRaw: number;
  readonly manpowerRecoverRaw?: number;
  readonly decorationLimit?: number;
  readonly dormSourcePath?: string;
}

export interface FactionRecord {
  readonly factionId: string;
  readonly name: string;
  readonly operatorIds: readonly string[];
  readonly sourcePath: string;
  /** 仅表示本目录内的上游直接关联，不代表技能定义的完整成员集合。 */
  readonly membershipScope: "catalog-direct-links";
  readonly members: readonly { readonly operatorId: string; readonly kind: FactionLinkRecord["kind"]; readonly sourcePath: string }[];
}

export interface ConstantRecord {
  readonly rawValue: number;
  readonly sourcePath: string;
  readonly unitStatus: "unverified";
}

export interface CatalogConstants {
  readonly laborRecoverTime: ConstantRecord;
  readonly comfortManpowerRecoverFactor: ConstantRecord;
  readonly manpowerDisplayFactor: ConstantRecord;
  readonly tiredApThreshold: ConstantRecord;
}

export interface CatalogDefinition {
  readonly version: CatalogVersion;
  readonly operators: readonly OperatorRecord[];
  readonly skills: readonly SkillRecord[];
  readonly facilityTypes: readonly FacilityTypeRecord[];
  readonly factions: readonly FactionRecord[];
  readonly constants: CatalogConstants;
}

/** 目录构建后的只读查询边界，只允许通过稳定 ID 访问记录。 */
export interface CatalogSnapshot {
  readonly version: CatalogVersion;
  readonly constants: CatalogConstants;
  operator(operatorId: string): OperatorRecord | undefined;
  skill(skillId: string): SkillRecord | undefined;
  facilityType(facilityTypeId: string): FacilityTypeRecord | undefined;
  faction(factionId: string): FactionRecord | undefined;
}

export type CatalogBuildErrorCode =
  | "CATALOG_FIELD_INVALID"
  | "CATALOG_VERSION_UNSUPPORTED"
  | "CATALOG_NOT_FOUND"
  | "CATALOG_JSON_INVALID"
  | "CATALOG_INTEGRITY_INVALID"
  | "CATALOG_IO_ERROR"
  | "CATALOG_SOURCE_INVALID"
  | "CATALOG_DUPLICATE_ID"
  | "CATALOG_REFERENCE_MISSING";

export interface CatalogBuildError {
  readonly code: CatalogBuildErrorCode;
  readonly message: string;
  /** 指向 CatalogDefinition 中导致失败的字段，便于调用方展示和定位。 */
  readonly path: string;
}

export type CatalogBuildResult =
  | { readonly ok: true; readonly snapshot: CatalogSnapshot }
  | { readonly ok: false; readonly error: CatalogBuildError };

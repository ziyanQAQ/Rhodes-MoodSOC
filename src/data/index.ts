/** 汇集 data 层的公开接口与类型，不引入存储适配器或模拟业务。 */
export { buildCatalogSnapshot } from "./catalog-builder.js";
export { CATALOG_SCHEMA_VERSION, CATALOG_GENERATOR_VERSION } from "./catalog-generator.js";
export { validateUpstreamCatalog, validateCatalogDefinition, validateCatalogManifest, CatalogValidationError } from "./validator.js";
export { normalizeUpstreamCatalog } from "./upstream-normalizer.js";
export type {
  CatalogBuildError,
  CatalogBuildErrorCode,
  CatalogBuildResult,
  CatalogDefinition,
  CatalogSnapshot,
  CatalogVersion,
  FacilityTypeRecord,
  FactionRecord,
  OperatorRecord,
  SkillRecord,
  SkillUnlockRecord,
  FactionLinkRecord,
  FacilityPhaseRecord,
  CatalogConstants,
  ConstantRecord,
} from "./catalog-types.js";
export type { NormalizedUpstreamCatalog } from "./upstream-normalizer.js";
export type { UpstreamCatalogRaw, UpstreamSourceMetadata } from "./upstream-types.js";

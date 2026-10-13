export { buildCatalogSnapshot } from "./catalog-builder.js";
export { generateCatalogArtifacts } from "./catalog-generator.js";
export { loadUpstreamCatalog } from "./upstream-loader.js";
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
} from "./catalog-types.js";
export type { NormalizedUpstreamCatalog } from "./upstream-normalizer.js";
export type { GenerateCatalogArtifactsOptions } from "./catalog-generator.js";
export type { UpstreamCatalogRaw, UpstreamSourceMetadata } from "./upstream-types.js";

/** 汇集目录基础设施的公开入口：上游加载、产物生成与文件快照读取。 */
export { FileCatalogReader } from "./file-catalog-reader.js";
export { loadUpstreamCatalog } from "./upstream-loader.js";
export { generateCatalogArtifacts } from "./catalog-generator.js";
export type { GenerateCatalogArtifactsOptions } from "./catalog-generator.js";

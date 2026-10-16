/** 定义目录格式版本、清单结构与确定性序列化，不执行文件 I/O 或哈希计算。 */
import type { NormalizedUpstreamCatalog } from "./upstream-normalizer.js";

export const CATALOG_SCHEMA_VERSION = "2";
export const CATALOG_GENERATOR_VERSION = "2.0.0";
export const CATALOG_ARTIFACT_NAMES = ["operators.json", "skills.json", "facilities.json", "factions.json", "constants.json"] as const;
export type CatalogArtifactName = typeof CATALOG_ARTIFACT_NAMES[number];

export interface CatalogFingerprint {
  readonly sha256: string;
  readonly bytes: number;
}

export interface CatalogSourceFile extends CatalogFingerprint {
  readonly fileName: string;
}

export interface CatalogManifest {
  readonly schemaVersion: typeof CATALOG_SCHEMA_VERSION;
  readonly generatorVersion: typeof CATALOG_GENERATOR_VERSION;
  readonly catalogVersion: string;
  readonly generatedAt: string;
  readonly source: { readonly kind: "local-archive"; readonly files: Readonly<Record<string, CatalogSourceFile>> };
  readonly counts: { readonly operators: number; readonly skills: number; readonly facilities: number; readonly factions: number };
  readonly artifacts: Readonly<Record<CatalogArtifactName, CatalogFingerprint>>;
}

export function serialize(value: unknown): string {
  // 缩进和末尾换行属于生成格式；此层只转换值，不访问文件或哈希实现。
  return `${JSON.stringify(value, null, 2)}\n`;
}

export function catalogIdentityInput(files: Readonly<Record<string, CatalogSourceFile>>): string {
  return serialize({ schemaVersion: CATALOG_SCHEMA_VERSION, generatorVersion: CATALOG_GENERATOR_VERSION,
    inputs: ["buildingData", "characterTable", "gamedataConst", "handbookTeamTable"].map((name) => ({
      name, fileName: files[name]?.fileName, sha256: files[name]?.sha256, bytes: files[name]?.bytes,
    })) });
}

export function catalogArtifactContents(catalog: NormalizedUpstreamCatalog): Readonly<Record<CatalogArtifactName, string>> {
  return { "operators.json": serialize(catalog.operators), "skills.json": serialize(catalog.skills),
    "facilities.json": serialize(catalog.facilityTypes), "factions.json": serialize(catalog.factions),
    "constants.json": serialize(catalog.constants) };
}

/** 装配上游加载、规范化与校验，写入目录 JSON，并生成含字节指纹的版本清单。 */
import { mkdir, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";
import { loadUpstreamCatalog } from "./upstream-loader.js";
import { normalizeUpstreamCatalog } from "../../data/upstream-normalizer.js";
import { CATALOG_SCHEMA_VERSION, CATALOG_GENERATOR_VERSION, catalogIdentityInput, serialize, catalogArtifactContents } from "../../data/catalog-generator.js";
import { buildCatalogSnapshot } from "../../data/catalog-builder.js";
import { CatalogValidationError } from "../../data/validator.js";
import { sha256 } from "./file-io.js";

export interface GenerateCatalogArtifactsOptions {
  readonly sourceRoot: string;
  readonly outputRoot: string;
  readonly generatedAt: string;
}

export async function generateCatalogArtifacts(
  options: GenerateCatalogArtifactsOptions,
): Promise<void> {
  if (Number.isNaN(Date.parse(options.generatedAt))) {
    throw new CatalogValidationError({ code: "CATALOG_SOURCE_INVALID", path: "generatedAt", message: "Invalid generation timestamp." });
  }

  const raw = await loadUpstreamCatalog(options.sourceRoot);
  const normalized = normalizeUpstreamCatalog(raw);
  // 先确定实际写入字节，再由同一字符串计算清单中的产物哈希。
  const artifactContents = catalogArtifactContents(normalized);
  const sourceFiles = Object.fromEntries(
    Object.entries(raw.source.files).map(([logicalName, file]) => [
      logicalName,
      // 清单仅保留逻辑文件名，避免把生成机器的绝对路径写入项目数据。
      { fileName: basename(file.path), sha256: file.sha256, bytes: file.bytes },
    ]),
  );
  // 输入指纹与映射版本共同决定目录身份，时间只作为生成元数据。
  const catalogVersion = `agd-${sha256(catalogIdentityInput(sourceFiles))}`;
  const checked = buildCatalogSnapshot({ ...normalized, version: {
    id: catalogVersion, source: "Kengxxiao/ArknightsGameData", generatedAt: options.generatedAt,
  } });
  if (!checked.ok) throw new CatalogValidationError(checked.error);
  const manifest = {
    schemaVersion: CATALOG_SCHEMA_VERSION,
    generatorVersion: CATALOG_GENERATOR_VERSION,
    catalogVersion,
    generatedAt: options.generatedAt,
    source: { kind: raw.source.kind, files: sourceFiles },
    counts: {
      operators: normalized.operators.length,
      skills: normalized.skills.length,
      facilities: normalized.facilityTypes.length,
      factions: normalized.factions.length,
    },
    artifacts: Object.fromEntries(
      Object.entries(artifactContents).map(([fileName, contents]) => [
        fileName,
        { sha256: sha256(contents), bytes: Buffer.byteLength(contents, "utf8") },
      ]),
    ),
  };

  try {
    await mkdir(options.outputRoot, { recursive: true });
    await Promise.all(Object.entries(artifactContents).map(([fileName, contents]) =>
      writeFile(join(options.outputRoot, fileName), contents, "utf8")));
    // 数据文件全部写完后才发布清单；失败时 Reader 的哈希校验仍会拒绝不完整目录。
    await writeFile(join(options.outputRoot, "manifest.json"), serialize(manifest), "utf8");
  } catch {
    throw new CatalogValidationError({ code: "CATALOG_IO_ERROR", path: "artifacts", message: "Cannot write catalog artifacts." });
  }
}

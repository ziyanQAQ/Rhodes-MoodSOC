import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";
import { loadUpstreamCatalog } from "./upstream-loader.js";
import { normalizeUpstreamCatalog } from "./upstream-normalizer.js";

export interface GenerateCatalogArtifactsOptions {
  readonly sourceRoot: string;
  readonly outputRoot: string;
  readonly generatedAt: string;
}

function serialize(value: unknown): string {
  // 统一缩进和结尾换行是生成物可复现的一部分。
  return `${JSON.stringify(value, null, 2)}\n`;
}

function sha256(contents: string): string {
  return createHash("sha256").update(contents, "utf8").digest("hex");
}

export async function generateCatalogArtifacts(
  options: GenerateCatalogArtifactsOptions,
): Promise<void> {
  if (Number.isNaN(Date.parse(options.generatedAt))) {
    throw new Error("CATALOG_SOURCE_INVALID: generatedAt must be an ISO-8601 timestamp");
  }

  const raw = await loadUpstreamCatalog(options.sourceRoot);
  const normalized = normalizeUpstreamCatalog(raw);
  // 先确定实际写入字节，再由同一字符串计算清单中的产物哈希。
  const artifactContents = {
    "operators.json": serialize(normalized.operators),
    "skills.json": serialize(normalized.skills),
    "facilities.json": serialize(normalized.facilityTypes),
    "constants.json": serialize(normalized.constants),
  } as const;
  const sourceFiles = Object.fromEntries(
    Object.entries(raw.source.files).map(([logicalName, file]) => [
      logicalName,
      // 清单仅保留逻辑文件名，避免把生成机器的绝对路径写入项目数据。
      { fileName: basename(file.path), sha256: file.sha256, bytes: file.bytes },
    ]),
  );
  // 固定顺序组合三个输入哈希，得到可复现且不依赖本地目录名的目录版本。
  const sourceDigest = createHash("sha256")
    .update(Object.values(raw.source.files).map((file) => file.sha256).join("\n"), "utf8")
    .digest("hex");
  const manifest = {
    schemaVersion: "1",
    catalogVersion: `agd-${sourceDigest.slice(0, 16)}`,
    generatedAt: options.generatedAt,
    source: { kind: raw.source.kind, files: sourceFiles },
    counts: {
      operators: normalized.operators.length,
      skills: normalized.skills.length,
      facilities: normalized.facilityTypes.length,
    },
    artifacts: Object.fromEntries(
      Object.entries(artifactContents).map(([fileName, contents]) => [
        fileName,
        { sha256: sha256(contents), bytes: Buffer.byteLength(contents, "utf8") },
      ]),
    ),
  };

  await mkdir(options.outputRoot, { recursive: true });
  await Promise.all([
    ...Object.entries(artifactContents).map(([fileName, contents]) =>
      writeFile(join(options.outputRoot, fileName), contents, "utf8"),
    ),
    writeFile(join(options.outputRoot, "manifest.json"), serialize(manifest), "utf8"),
  ]);
}

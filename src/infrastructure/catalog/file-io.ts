/** 提供目录文件读取、JSON 解析及 SHA-256 计算，并将 I/O 错误转为逻辑路径错误。 */
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { join } from "node:path";
import { CatalogValidationError } from "../../data/validator.js";

export function sha256(contents: string | Uint8Array): string {
  return createHash("sha256").update(contents).digest("hex");
}

export async function readCatalogBytes(root: string, fileName: string): Promise<Buffer> {
  try { return await readFile(join(root, fileName)); }
  catch (error) {
    const code = error instanceof Error && "code" in error ? error.code : undefined;
    // 公开错误仅含逻辑文件名，不保留原生错误 cause，防止泄漏机器路径。
    throw new CatalogValidationError({ code: code === "ENOENT" ? "CATALOG_NOT_FOUND" : "CATALOG_IO_ERROR",
      path: fileName, message: "Catalog file cannot be read." });
  }
}

export function parseCatalogJson(contents: Buffer, fileName: string): unknown {
  try { return JSON.parse(contents.toString("utf8")) as unknown; }
  catch {
    throw new CatalogValidationError({ code: "CATALOG_JSON_INVALID", path: fileName, message: "Invalid JSON." });
  }
}

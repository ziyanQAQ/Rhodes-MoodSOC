/** 实现文件目录读取端口，校验清单、字节完整性及记录一致性后返回只读快照。 */
import type { CatalogReader } from "../../application/ports.js";
import type { CatalogSnapshot } from "../../data/catalog-types.js";
import { buildCatalogSnapshot } from "../../data/catalog-builder.js";
import { CatalogValidationError, validateCatalogManifest } from "../../data/validator.js";
import { readCatalogBytes, parseCatalogJson, sha256 } from "./file-io.js";
import { catalogIdentityInput, CATALOG_ARTIFACT_NAMES } from "../../data/catalog-generator.js";

/** 文件 Adapter 只装配目录；应用层和领域层无需知道磁盘格式。 */
export class FileCatalogReader implements CatalogReader {
  constructor(private readonly root: string) {}

  async load(): Promise<CatalogSnapshot> {
    const json = async (name: string): Promise<unknown> => parseCatalogJson(await readCatalogBytes(this.root, name), name);
    const checked = validateCatalogManifest(await json("manifest.json"));
    if (!checked.ok) throw new CatalogValidationError(checked.error);
    const manifest = checked.value;
    if (manifest.catalogVersion !== `agd-${sha256(catalogIdentityInput(manifest.source.files))}`) {
      throw new CatalogValidationError({ code: "CATALOG_INTEGRITY_INVALID", path: "manifest.catalogVersion", message: "Source identity mismatch." });
    }
    const values: Record<string, unknown> = {};
    // 只读取固定逻辑文件名，不接受清单指定任意磁盘路径。
    for (const name of CATALOG_ARTIFACT_NAMES) {
      const bytes = await readCatalogBytes(this.root, name);
      const value = parseCatalogJson(bytes, name);
      const fingerprint = manifest.artifacts[name];
      if (bytes.byteLength !== fingerprint.bytes || sha256(bytes) !== fingerprint.sha256) {
        throw new CatalogValidationError({ code: "CATALOG_INTEGRITY_INVALID", path: name, message: "Artifact fingerprint mismatch." });
      }
      values[name] = value;
    }
    for (const [field, name] of [["operators", "operators.json"], ["skills", "skills.json"],
      ["facilities", "facilities.json"], ["factions", "factions.json"]] as const) {
      const value = values[name];
      if (!Array.isArray(value)) throw new CatalogValidationError({ code: "CATALOG_FIELD_INVALID", path: name, message: "Expected array." });
      if (value.length !== manifest.counts[field]) throw new CatalogValidationError({
        code: "CATALOG_INTEGRITY_INVALID", path: `manifest.counts.${field}`, message: "Artifact count mismatch.",
      });
    }
    const result = buildCatalogSnapshot({ version: {
      id: manifest.catalogVersion, source: "Kengxxiao/ArknightsGameData", generatedAt: manifest.generatedAt,
    }, operators: values["operators.json"], skills: values["skills.json"], facilityTypes: values["facilities.json"],
      factions: values["factions.json"], constants: values["constants.json"] });
    if (!result.ok) throw new CatalogValidationError(result.error);
    return result.snapshot;
  }
}

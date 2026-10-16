/** 读取四份本地上游 JSON，记录原始字节指纹，并校验后交给 data 层处理。 */
import { resolve } from "node:path";
import type { SourceFileFingerprint, UpstreamCatalogRaw } from "../../data/upstream-types.js";
import { CatalogValidationError, validateUpstreamCatalog } from "../../data/validator.js";
import { readCatalogBytes, parseCatalogJson, sha256 } from "./file-io.js";

async function readJson(root: string, fileName: string): Promise<{ value: unknown; fingerprint: SourceFileFingerprint }> {
  const contents = await readCatalogBytes(root, fileName);
  return {
    value: parseCatalogJson(contents, fileName),
    fingerprint: {
      path: `zh_CN/gamedata/excel/${fileName}`,
      // 对原始字节计算哈希，避免 JSON 解析或重新序列化改变来源指纹。
      sha256: sha256(contents),
      bytes: contents.byteLength,
    },
  };
}

export async function loadUpstreamCatalog(root: string): Promise<UpstreamCatalogRaw> {
  // Loader 只负责读取和登记来源，不在此解释技能、设施或心情规则。
  const sourceRoot = resolve(root);
  const [buildingData, characterTable, gamedataConst, handbookTeamTable] = await Promise.all([
    readJson(sourceRoot, "building_data.json"),
    readJson(sourceRoot, "character_table.json"),
    readJson(sourceRoot, "gamedata_const.json"),
    readJson(sourceRoot, "handbook_team_table.json"),
  ]);

  const raw: unknown = {
    source: {
      kind: "local-archive",
      root: sourceRoot,
      files: {
        buildingData: buildingData.fingerprint,
        characterTable: characterTable.fingerprint,
        gamedataConst: gamedataConst.fingerprint,
        handbookTeamTable: handbookTeamTable.fingerprint,
      },
    },
    buildingData: buildingData.value,
    characterTable: characterTable.value,
    gamedataConst: gamedataConst.value,
    handbookTeamTable: handbookTeamTable.value,
  };
  const result = validateUpstreamCatalog(raw);
  if (!result.ok) throw new CatalogValidationError(result.error);
  return result.value;
}

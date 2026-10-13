import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import type { SourceFileFingerprint, UpstreamCatalogRaw } from "./upstream-types.js";

async function readJson<T>(path: string): Promise<{ value: T; fingerprint: SourceFileFingerprint }> {
  const contents = await readFile(path);
  return {
    value: JSON.parse(contents.toString("utf8")) as T,
    fingerprint: {
      path,
      // 对原始字节计算哈希，避免 JSON 解析或重新序列化改变来源指纹。
      sha256: createHash("sha256").update(contents).digest("hex"),
      bytes: contents.byteLength,
    },
  };
}

export async function loadUpstreamCatalog(root: string): Promise<UpstreamCatalogRaw> {
  // Loader 只负责读取和登记来源，不在此解释技能、设施或心情规则。
  const sourceRoot = resolve(root);
  const [buildingData, characterTable, gamedataConst] = await Promise.all([
    readJson<UpstreamCatalogRaw["buildingData"]>(join(sourceRoot, "building_data.json")),
    readJson<UpstreamCatalogRaw["characterTable"]>(join(sourceRoot, "character_table.json")),
    readJson<UpstreamCatalogRaw["gamedataConst"]>(join(sourceRoot, "gamedata_const.json")),
  ]);

  return {
    source: {
      kind: "local-archive",
      root: sourceRoot,
      files: {
        buildingData: buildingData.fingerprint,
        characterTable: characterTable.fingerprint,
        gamedataConst: gamedataConst.fingerprint,
      },
    },
    buildingData: buildingData.value,
    characterTable: characterTable.value,
    gamedataConst: gamedataConst.value,
  };
}

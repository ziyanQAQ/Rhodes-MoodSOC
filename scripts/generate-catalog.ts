/** 解析目录生成命令的参数与环境变量，调用基础设施生成器更新静态目录。 */
import { resolve } from "node:path";
import { generateCatalogArtifacts } from "../src/infrastructure/catalog/catalog-generator.js";

function option(name: string): string | undefined {
  const position = process.argv.indexOf(name);
  return position >= 0 ? process.argv[position + 1] : undefined;
}

// 命令行参数优先于环境变量，便于 CI 或人工更新时明确指定数据来源。
const sourceRoot = option("--source") ?? process.env.ARKNIGHTS_DATA_ROOT;
if (!sourceRoot) {
  throw new Error("Missing --source <zh_CN/gamedata/excel directory> or ARKNIGHTS_DATA_ROOT");
}

await generateCatalogArtifacts({
  sourceRoot: resolve(sourceRoot),
  outputRoot: resolve(option("--output") ?? "data/generated"),
  // 自动时间适合日常更新；可复现构建应显式传入 --generated-at。
  generatedAt: option("--generated-at") ?? new Date().toISOString(),
});

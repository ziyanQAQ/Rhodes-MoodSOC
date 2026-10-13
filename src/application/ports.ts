import type { CatalogSnapshot } from "../data/catalog-types.js";

/** 由 application 定义、infrastructure 实现的静态目录读取端口。 */
export interface CatalogReader {
  load(): Promise<CatalogSnapshot>;
}

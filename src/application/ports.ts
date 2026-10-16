/** 定义应用层的目录读取端口，将用例与具体文件存储实现解耦。 */
import type { CatalogSnapshot } from "../data/catalog-types.js";

/** 由 application 定义、infrastructure 实现的静态目录读取端口。 */
export interface CatalogReader {
  load(): Promise<CatalogSnapshot>;
}

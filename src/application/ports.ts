import type { CatalogSnapshot } from "../data/catalog-types.js";

export interface CatalogReader {
  load(): Promise<CatalogSnapshot>;
}

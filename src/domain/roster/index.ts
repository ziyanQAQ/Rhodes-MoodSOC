/** 构建与布局独立的不可变名册，校验目录身份和显式练度，不推断技能生效情况。 */
import type { CatalogSnapshot } from "../../data/index.js";
import { DomainError } from "../errors.js";

export interface RosterEntry {
  readonly operatorId: string;
  /** 保留上游精英阶段标识；等级上限尚未进入目录，不在此猜测。 */
  readonly elite: string;
  readonly level: number;
}

export interface Roster {
  entries(): readonly RosterEntry[];
  operator(operatorId: string): RosterEntry | undefined;
}

export function createRoster(input: readonly RosterEntry[], catalog: CatalogSnapshot): Roster {
  const ids = new Map<string, RosterEntry>();
  const entries = Object.freeze(input.map((entry, index) => {
    const path = `roster[${index}]`;
    if (!catalog.operator(entry.operatorId)) throw new DomainError({
      code: "DOMAIN_UNKNOWN_ID", path: `${path}.operatorId`, message: "干员不在本次目录中",
      details: { operatorId: entry.operatorId },
    });
    if (ids.has(entry.operatorId) || !/^PHASE_[0-9]+$/.test(entry.elite)
      || !Number.isSafeInteger(entry.level) || entry.level < 1) throw new DomainError({
      code: "DOMAIN_INVARIANT_VIOLATION", path, message: "名册 ID 不得重复，且必须提供阶段标识和正整数等级",
    });
    const copy = Object.freeze({ operatorId: entry.operatorId, elite: entry.elite, level: entry.level });
    ids.set(copy.operatorId, copy);
    return copy;
  }));
  return Object.freeze({ entries: () => entries, operator: (id: string) => ids.get(id) });
}

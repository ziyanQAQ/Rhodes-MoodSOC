/** 汇集当前已实现的领域基础接口，不暴露内部适配器或协议 DTO。 */
export { DomainError } from "./errors.js";
export type { DomainErrorCode, DomainErrorOptions } from "./errors.js";
export {
  addDecimal,
  clampDecimal,
  compareDecimal,
  multiplyDecimal,
  subtractDecimal,
} from "./mood/index.js";
export { createTimeline } from "./timeline/index.js";
export type { ExecutionPoint, ScheduleShift, Timeline } from "./timeline/index.js";
export { createRoster } from "./roster/index.js";
export type { Roster, RosterEntry } from "./roster/index.js";
export { createScheduleBaseline } from "./layout/index.js";
export type { Position, ManualLedger, FacilityLayout, ShiftLayout, SimulationLayout, SimulationCommand, ScheduleBaseline, LayoutCommand, LayoutNotice } from "./layout/index.js";

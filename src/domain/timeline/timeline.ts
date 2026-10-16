/** 根据顺序班次生成班初和严格位于班内的十二小时执行点。 */
import { DomainError } from "../errors.js";
import type { DomainErrorCode } from "../errors.js";
import { addDecimal, compareDecimal } from "../mood/decimal.js";

export interface ScheduleShift {
  readonly shiftId: string;
  readonly durationHours: string;
}

export interface ExecutionPoint {
  readonly shiftId: string;
  readonly shiftIndex: number;
  readonly kind: "shift-start" | "internal";
  readonly offsetHours: string;
  readonly shiftOffsetHours: string;
}

export interface Timeline {
  points(schedule: readonly ScheduleShift[]): readonly ExecutionPoint[];
}

function fail(code: DomainErrorCode, path: string, message: string): never {
  throw new DomainError({ code, path, message });
}

function validateDuration(value: string, path: string): void {
  try {
    if (compareDecimal(value, "0") <= 0) fail("DOMAIN_INVALID_TRANSITION", path, "Shift duration must be positive.");
  } catch (error) {
    if (error instanceof DomainError && error.code === "DOMAIN_DECIMAL_INVALID") {
      throw new DomainError({ code: error.code, path, message: error.message,
        ...(error.details === undefined ? {} : { details: error.details }) });
    }
    throw error;
  }
}

export function createTimeline(): Timeline {
  return Object.freeze({
    points(schedule: readonly ScheduleShift[]): readonly ExecutionPoint[] {
      if (schedule.length === 0) fail("DOMAIN_INVARIANT_VIOLATION", "schedule", "At least one shift is required.");
      const shiftIds = new Set<string>();
      const points: ExecutionPoint[] = [];
      let shiftStart = "0";

      schedule.forEach((shift, shiftIndex) => {
        const path = `schedule[${shiftIndex}]`;
        if (!shift.shiftId.trim()) fail("DOMAIN_UNKNOWN_ID", `${path}.shiftId`, "Shift ID must not be empty.");
        if (shiftIds.has(shift.shiftId)) fail("DOMAIN_INVARIANT_VIOLATION", `${path}.shiftId`, "Shift ID must be unique.");
        shiftIds.add(shift.shiftId);
        validateDuration(shift.durationHours, `${path}.durationHours`);

        points.push(Object.freeze({ shiftId: shift.shiftId, shiftIndex, kind: "shift-start",
          offsetHours: shiftStart, shiftOffsetHours: "0" }));
        for (let internalOffset = "12"; compareDecimal(internalOffset, shift.durationHours) < 0;
          internalOffset = addDecimal(internalOffset, "12")) {
          points.push(Object.freeze({ shiftId: shift.shiftId, shiftIndex, kind: "internal",
            offsetHours: addDecimal(shiftStart, internalOffset), shiftOffsetHours: internalOffset }));
        }
        shiftStart = addDecimal(shiftStart, shift.durationHours);
      });

      return Object.freeze(points);
    },
  });
}

/** 管理不可变班次布局、稳定设施位次和每次从基线重建的模拟副本。 */
import type { CatalogSnapshot } from "../../data/index.js";
import type { Roster } from "../roster/index.js";
import { DomainError } from "../errors.js";

export interface Position {
  readonly facilityId: string;
  readonly slotIndex: number;
}
export interface ManualLedger {
  readonly slots: readonly number[];
  readonly operatorIds: readonly string[];
}
export interface FacilityLayout {
  readonly facilityId: string;
  readonly facilityType: string;
  readonly level: number;
  readonly enabled: boolean;
  readonly slots: readonly (string | null)[];
  readonly manual?: ManualLedger;
}
export interface ShiftLayout {
  readonly shiftId: string;
  readonly facilities: readonly FacilityLayout[];
}
export interface SimulationLayout {
  snapshot(): ShiftLayout;
  /** 默认 0；仅限制自动换出，不妨碍填充锁定区空位。 */
  lockedPositions(count?: number): readonly Position[];
  apply(command: SimulationCommand, lockPositionCount?: number): SimulationLayout;
}
export type SimulationCommand = Position & (
  | { readonly kind: "place"; readonly operatorId: string }
  | { readonly kind: "clear" }
);
export interface ScheduleBaseline {
  readonly revision: number;
  snapshot(): readonly ShiftLayout[];
  simulation(shiftId: string): SimulationLayout;
  apply(command: LayoutCommand): ScheduleBaseline;
  detached(): readonly string[];
  notices(): readonly LayoutNotice[];
}

export interface LayoutNotice extends Position {
  readonly code: "DETACHED_OCCUPANT_REMOVED";
  readonly shiftId: string;
  readonly operatorId: string;
}

export type LayoutCommand =
  | ({ readonly kind: "place"; readonly operatorId: string; readonly lock?: boolean } & Position & { readonly shiftId: string })
  | ({ readonly kind: "clear" | "lock" } & Position & { readonly shiftId: string })
  | { readonly kind: "unlock-all"; readonly shiftId?: string }
  | { readonly kind: "detach"; readonly operatorId: string };

/** 去掉空台账，保留预留空位；人员锁随离开房间的人员解除。 */
function withLedger(room: FacilityLayout, slots: readonly (string | null)[], marked: readonly number[], people: readonly string[]): FacilityLayout {
  const { manual: _manual, ...base } = room;
  const ids = [...new Set(people)].filter(id => slots.includes(id));
  const indices = [...new Set(marked)].sort((a, b) => a - b);
  return indices.length || ids.length ? { ...base, slots, manual: { slots: indices, operatorIds: ids } } : { ...base, slots };
}

function edit(shifts: readonly ShiftLayout[], command: Exclude<LayoutCommand, { kind: "detach" }>): readonly ShiftLayout[] {
  if (command.kind === "unlock-all") {
    if (command.shiftId !== undefined && !shifts.some(s => s.shiftId === command.shiftId)) fail("shiftId", "未知班次", true);
    return shifts.map(s => command.shiftId === undefined || command.shiftId === s.shiftId ? {
      ...s, facilities: s.facilities.map(r => { const { manual: _manual, ...room } = r; return room; }),
    } : s);
  }
  const shift = shifts.find(s => s.shiftId === command.shiftId);
  const room = shift?.facilities.find(r => r.facilityId === command.facilityId);
  const path = `shifts.${command.shiftId}.facilities.${command.facilityId}.slots[${command.slotIndex}]`;
  if (!shift || !room) fail(path, "未知班次或设施", true);
  if (!Number.isSafeInteger(command.slotIndex) || command.slotIndex < 0 || command.slotIndex >= room.slots.length) fail(path, "位次越界");
  return shifts.map(s => s !== shift ? s : { ...s, facilities: s.facilities.map(r => {
    let slots = [...r.slots];
    let marked = [...(r.manual?.slots ?? [])];
    let people = [...(r.manual?.operatorIds ?? [])];
    // 手动移动只清空来源位，不挤压其他人；旧人的手动身份不转给自动布局。
    if (command.kind === "place") slots = slots.map((id, index) => {
      if (id !== command.operatorId || (r === room && index === command.slotIndex)) return id;
      marked = marked.filter(slot => slot !== index);
      people = people.filter(person => person !== id);
      return null;
    });
    if (r === room) {
      const old = slots[command.slotIndex];
      if (command.kind === "clear" || command.kind === "place") {
        people = people.filter(id => id !== old);
        marked = marked.filter(index => index !== command.slotIndex);
        slots[command.slotIndex] = command.kind === "place" ? command.operatorId : null;
      }
      if (command.kind === "lock" || (command.kind === "place" && command.lock !== false)) {
        marked.push(command.slotIndex);
        const id = slots[command.slotIndex];
        if (id != null) people.push(id);
      }
    }
    return withLedger(r, slots, marked, people);
  }) });
}

function fail(path: string, message: string, unknown = false): never {
  throw new DomainError({ code: unknown ? "DOMAIN_UNKNOWN_ID" : "DOMAIN_INVARIANT_VIOLATION", path, message });
}

function freezeShift(shift: ShiftLayout): ShiftLayout {
  return Object.freeze({ shiftId: shift.shiftId, facilities: Object.freeze(shift.facilities.map(room => {
    const base = { facilityId: room.facilityId, facilityType: room.facilityType,
      level: room.level, enabled: room.enabled, slots: Object.freeze([...room.slots]) };
    return Object.freeze(room.manual ? { ...base, manual: Object.freeze({
      slots: Object.freeze([...room.manual.slots]), operatorIds: Object.freeze([...room.manual.operatorIds]),
    }) } : base);
  })) });
}

function simulation(shift: ShiftLayout, roster: Roster): SimulationLayout {
  const snapshot = freezeShift(shift);
  const lockedPositions = (count = 0): readonly Position[] => {
    if (!Number.isSafeInteger(count)) fail("lockPositionCount", "锁定位置数必须是安全整数");
    const rooms = snapshot.facilities.filter(room => room.enabled && room.facilityType === "DORMITORY");
    const positions: Position[] = [];
    const capacity = rooms.reduce((max, room) => Math.max(max, room.slots.length), 0);
    for (let slotIndex = 0; slotIndex < capacity; slotIndex++) {
      for (const room of rooms) if (slotIndex < room.slots.length) positions.push(Object.freeze({ facilityId: room.facilityId, slotIndex }));
    }
    return Object.freeze(positions.slice(0, Math.max(0, count)));
  };
  return Object.freeze({ snapshot: () => snapshot, lockedPositions, apply: (command: SimulationCommand, count = 0) => {
    const locked = lockedPositions(count);
    const target = snapshot.facilities.find(room => room.facilityId === command.facilityId);
    const path = `shifts.${snapshot.shiftId}.facilities.${command.facilityId}.slots[${command.slotIndex}]`;
    if (!target) fail(path, "未知设施", true);
    if (!target.enabled || !Number.isSafeInteger(command.slotIndex) || command.slotIndex < 0 || command.slotIndex >= target.slots.length) fail(path, "模拟写入目标必须是启用设施的有效位次");
    if (command.kind === "place" && !roster.operator(command.operatorId)) fail(path, "干员不在名册中", true);
    const protectedPeople = new Set(snapshot.facilities.flatMap(room => room.manual?.operatorIds ?? []));
    const facilities = snapshot.facilities.map(room => {
      const slots = room.slots.map((id, slotIndex) => {
        let next = id;
        if (command.kind === "place" && id === command.operatorId) next = null;
        if (room === target && slotIndex === command.slotIndex) next = command.kind === "clear" ? null : command.operatorId;
        if (next === id) return id;
        const regionProtected = locked.some(position => position.facilityId === room.facilityId && position.slotIndex === slotIndex);
        // 手动空位也受保护；配置锁区只保护现有住户，保留填空能力。
        if (!room.enabled || room.manual?.slots.includes(slotIndex)
          || (id !== null && (protectedPeople.has(id) || regionProtected))) fail(
          `shifts.${snapshot.shiftId}.facilities.${room.facilityId}.slots[${slotIndex}]`, "自动布局不能移动手动锁、锁定区住户或停用设施占位",
        );
        return next;
      });
      return { ...room, slots };
    });
    return simulation({ ...snapshot, facilities }, roster);
  } });
}

export function createScheduleBaseline(
  input: readonly ShiftLayout[], roster: Roster, catalog: CatalogSnapshot, detached: readonly string[] = [],
): ScheduleBaseline {
  const shiftIds = new Set<string>();
  for (const shift of input) {
    if (!shift.shiftId.trim() || shiftIds.has(shift.shiftId)) fail("shiftId", "班次 ID 必须非空且唯一");
    shiftIds.add(shift.shiftId);
    const roomIds = new Set<string>();
    const occupants = new Set<string>();
    for (const room of shift.facilities) {
      const path = `shifts.${shift.shiftId}.facilities.${room.facilityId}`;
      if (!room.facilityId.trim() || roomIds.has(room.facilityId)) fail(path, "设施 ID 必须在班内唯一且非空");
      roomIds.add(room.facilityId);
      const type = catalog.facilityType(room.facilityType);
      if (!type) fail(path, "未知设施类型", true);
      const phase = type.phases.find(item => item.level === room.level);
      if (!phase || room.slots.length !== phase.maxStationedNum) fail(path, "等级或位次数与目录容量不符");
      for (const id of room.slots) if (id !== null) {
        if (!roster.operator(id)) fail(path, "占位干员不在名册中", true);
        if (occupants.has(id)) fail(path, "同班次干员不能重复占位");
        occupants.add(id);
      }
      if (room.manual) {
        for (const index of room.manual.slots) if (!Number.isSafeInteger(index) || index < 0 || index >= room.slots.length) fail(path, "手动台账位次越界");
        for (const id of room.manual.operatorIds) if (!room.slots.includes(id)) fail(path, "手动人员台账必须关联房间住户");
      }
    }
  }
  for (const id of detached) if (!roster.operator(id)) fail("detached", "名单干员不在名册中", true);
  const notices: LayoutNotice[] = [];
  // 导入层名单优先：冲突住户退回名册，原位留洞，清掉对应手动身份并显式报告。
  const normalized = input.map(shift => ({ ...shift, facilities: shift.facilities.map(room => {
    const removed: number[] = [];
    const slots = room.slots.map((id, slotIndex) => {
      if (id === null || !detached.includes(id)) return id;
      removed.push(slotIndex);
      notices.push(Object.freeze({ code: "DETACHED_OCCUPANT_REMOVED", shiftId: shift.shiftId, facilityId: room.facilityId, slotIndex, operatorId: id }));
      return null;
    });
    return withLedger(room, slots, (room.manual?.slots ?? []).filter(index => !removed.includes(index)), room.manual?.operatorIds ?? []);
  }) }));
  return buildBaseline(normalized, roster, catalog, 0, detached, Object.freeze(notices));
}

function buildBaseline(input: readonly ShiftLayout[], roster: Roster, catalog: CatalogSnapshot, revision: number,
  detached: readonly string[], notices: readonly LayoutNotice[]): ScheduleBaseline {
  const shifts = Object.freeze(input.map(freezeShift));
  const absent = Object.freeze([...new Set(detached)]);
  return Object.freeze({ revision, snapshot: () => shifts, detached: () => absent, notices: () => notices, apply: (command: LayoutCommand) => {
    if ((command.kind === "place" || command.kind === "detach") && !roster.operator(command.operatorId)) fail("operatorId", "干员不在名册中", true);
    if (command.kind === "detach") {
      for (const shift of shifts) for (const room of shift.facilities) {
        if (room.manual?.operatorIds.includes(command.operatorId)
          || room.manual?.slots.some(index => room.slots[index] === command.operatorId)) {
          fail(`shifts.${shift.shiftId}.facilities.${room.facilityId}`, `被手动锁定的干员 ${command.operatorId} 不能加入不在基建名单`);
        }
      }
      return buildBaseline(shifts, roster, catalog, revision + 1, [...absent, command.operatorId], notices);
    }
    if (command.kind === "lock") {
      const id = shifts.find(s => s.shiftId === command.shiftId)?.facilities.find(r => r.facilityId === command.facilityId)?.slots[command.slotIndex];
      if (id != null && absent.includes(id)) fail(`shifts.${command.shiftId}.facilities.${command.facilityId}.slots[${command.slotIndex}]`, "不在基建名单中的住户不能上锁");
    }
    const nextAbsent = command.kind === "place" && command.lock !== false ? absent.filter(id => id !== command.operatorId) : absent;
    return buildBaseline(edit(shifts, command), roster, catalog, revision + 1, nextAbsent, notices);
  }, simulation: (id: string) => {
    const shift = shifts.find(item => item.shiftId === id);
    if (!shift) fail("shiftId", "未知班次", true);
    return simulation(shift, roster);
  } });
}

/** 提供领域统一的六位小数定点运算、半入舍入及范围校验。 */
import { DomainError } from "../errors.js";

const SCALE_DIGITS = 6;
const SCALE = 1_000_000n;
const MAX_INTEGER_DIGITS = 18;
const MAX_SCALED = 10n ** BigInt(MAX_INTEGER_DIGITS + SCALE_DIGITS) - 1n;
const DECIMAL_PATTERN = /^-?(?:0|[1-9]\d*)(?:\.\d{1,6})?$/;

function invalid(value: string, message: string): never {
  throw new DomainError({
    code: "DOMAIN_DECIMAL_INVALID",
    path: "value",
    message,
    details: { value },
  });
}

function parseDecimal(value: string): bigint {
  if (!DECIMAL_PATTERN.test(value)) invalid(value, "Expected a canonical decimal with at most six fractional digits.");
  const negative = value.startsWith("-");
  const unsigned = negative ? value.slice(1) : value;
  const [integer = "0", fraction = ""] = unsigned.split(".");
  if (integer.length > MAX_INTEGER_DIGITS) invalid(value, "Decimal integer part exceeds 18 digits.");
  const scaled = BigInt(integer) * SCALE + BigInt(fraction.padEnd(SCALE_DIGITS, "0") || "0");
  return negative ? -scaled : scaled;
}

function formatDecimal(scaled: bigint): string {
  if (scaled > MAX_SCALED || scaled < -MAX_SCALED) invalid(scaled.toString(), "Decimal result exceeds the supported range.");
  if (scaled === 0n) return "0";
  const negative = scaled < 0n;
  const absolute = negative ? -scaled : scaled;
  const integer = absolute / SCALE;
  const fraction = (absolute % SCALE).toString().padStart(SCALE_DIGITS, "0").replace(/0+$/, "");
  return `${negative ? "-" : ""}${integer}${fraction ? `.${fraction}` : ""}`;
}

export function addDecimal(left: string, right: string): string {
  return formatDecimal(parseDecimal(left) + parseDecimal(right));
}

export function subtractDecimal(left: string, right: string): string {
  return formatDecimal(parseDecimal(left) - parseDecimal(right));
}

export function multiplyDecimal(left: string, right: string): string {
  const product = parseDecimal(left) * parseDecimal(right);
  const negative = product < 0n;
  const absolute = negative ? -product : product;
  // 中间值按第七位小数执行 half-up，再恢复符号。
  const rounded = (absolute + SCALE / 2n) / SCALE;
  return formatDecimal(negative ? -rounded : rounded);
}

export function compareDecimal(left: string, right: string): -1 | 0 | 1 {
  const difference = parseDecimal(left) - parseDecimal(right);
  return difference < 0n ? -1 : difference > 0n ? 1 : 0;
}

export function clampDecimal(value: string, minimum: string, maximum: string): string {
  const parsed = parseDecimal(value);
  const lower = parseDecimal(minimum);
  const upper = parseDecimal(maximum);
  if (lower > upper) invalid(`${minimum}..${maximum}`, "Decimal clamp minimum exceeds maximum.");
  return formatDecimal(parsed < lower ? lower : parsed > upper ? upper : parsed);
}

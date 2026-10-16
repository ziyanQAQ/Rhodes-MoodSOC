/** 定义领域公开接口使用的稳定错误码和可定位错误对象。 */
export type DomainErrorCode =
  | "DOMAIN_INVARIANT_VIOLATION"
  | "DOMAIN_INVALID_TRANSITION"
  | "DOMAIN_UNKNOWN_ID"
  | "DOMAIN_DECIMAL_INVALID";

export interface DomainErrorOptions {
  readonly code: DomainErrorCode;
  readonly path: string;
  readonly message: string;
  readonly details?: Readonly<Record<string, string>>;
}

export class DomainError extends Error {
  readonly code: DomainErrorCode;
  readonly path: string;
  readonly details?: Readonly<Record<string, string>>;

  constructor(options: DomainErrorOptions) {
    super(options.message);
    this.name = "DomainError";
    this.code = options.code;
    this.path = options.path;
    if (options.details !== undefined) this.details = Object.freeze({ ...options.details });
  }
}

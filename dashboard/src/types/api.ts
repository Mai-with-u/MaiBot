/**
 * Unified API response type definition
 * Discriminated union for type-safe error handling
 */

export type ApiResponse<T> =
  | { success: true; data: T }
  | { success: false; error: string }
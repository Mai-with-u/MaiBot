/**
 * API response parsing and error handling helpers
 * Provides unified error handling across API modules
 */

import type { ApiResponse } from '@/types/api'

/**
 * Parse an HTTP response into a typed ApiResponse
 * Handles JSON parsing, error extraction, and HTTP status codes
 */
export async function parseResponse<T>(response: Response): Promise<ApiResponse<T>> {
  if (response.ok) {
    try {
      const data = await response.json()
      return { success: true, data }
    } catch {
      return {
        success: false,
        error: 'Failed to parse response body',
      }
    }
  }

  try {
    const errorData = await response.json()
    const errorMessage =
      errorData.error?.detail ??
      errorData.error?.message ??
      errorData.detail ??
      errorData.message ??
      response.statusText

    return {
      success: false,
      error: String(errorMessage),
    }
  } catch {
    return {
      success: false,
      error: response.statusText || 'Unknown error',
    }
  }
}

/**
 * Extract data from successful ApiResponse or throw error
 * Simplifies error handling in async functions
 */
export function throwIfError<T>(result: ApiResponse<T>): T {
  if (result.success) {
    return result.data
  }
  throw new Error(result.error)
}
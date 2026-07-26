import type { ReactNode } from 'react'

import type { ConfigSchema, FieldSchema } from '@/types/config-schema'

/**
 * Hook type for field-level customization
 */
export type FieldHookType = 'replace' | 'wrapper' | 'hidden'

/**
 * Props passed to a FieldHookComponent
 */
export interface FieldHookComponentProps {
  fieldPath: string
  value: unknown
  onChange?: (value: unknown) => void
  onParentChange?: (field: string, value: unknown) => void
  children?: ReactNode
  schema?: ConfigSchema | FieldSchema
  parentValues?: Record<string, unknown>
  advancedVisible?: boolean
  /**
   * 如果当前字段是 `List[ConfigBase]` 或嵌套 ConfigBase，
   * 这里会传入对应子配置类的 ConfigSchema，便于自定义编辑器
   * 直接渲染列表项的字段。
   */
  nestedSchema?: ConfigSchema
}

/**
 * A React component that can be registered as a field hook
 */
export type FieldHookComponent = React.FC<FieldHookComponentProps>

/**
 * Registry entry for a field hook
 */
interface FieldHookEntry {
  component: FieldHookComponent
  type: FieldHookType
}

/**
 * Registry for managing field-level hooks
 * Supports two types of hooks:
 * - replace: Completely replaces the default field renderer
 * - wrapper: Wraps the default field renderer with additional functionality
 */
export class FieldHookRegistry {
  private hooks: Map<string, FieldHookEntry> = new Map()

  /**
   * Register a hook for a specific field path
   * @param fieldPath The field path (e.g., 'chat.reply_timing.talk_value')
   * @param component The React component to register
   * @param type The hook type ('replace' or 'wrapper')
   */
  register(
    fieldPath: string,
    component: FieldHookComponent,
    type: FieldHookType = 'replace'
  ): void {
    this.hooks.set(fieldPath, { component, type })
  }

  /**
   * Get a registered hook for a specific field path
   * @param fieldPath The field path to look up
   * @returns The hook entry if found, undefined otherwise
   */
  get(fieldPath: string): FieldHookEntry | undefined {
    return this.hooks.get(fieldPath)
  }

  /**
   * Check if a hook is registered for a specific field path
   * @param fieldPath The field path to check
   * @returns True if a hook is registered, false otherwise
   */
  has(fieldPath: string): boolean {
    return this.hooks.has(fieldPath)
  }

  /**
   * Unregister a hook for a specific field path
   * @param fieldPath The field path to unregister
   */
  unregister(fieldPath: string): void {
    this.hooks.delete(fieldPath)
  }

  /**
   * Clear all registered hooks
   */
  clear(): void {
    this.hooks.clear()
  }

  /**
   * Get all registered field paths
   * @returns Array of registered field paths
   */
  getAllPaths(): string[] {
    return Array.from(this.hooks.keys())
  }
}

/**
 * Singleton instance of the field hook registry
 */
export const fieldHooks = new FieldHookRegistry()

import { describe, it, expect, beforeEach } from 'vitest'

import { FieldHookRegistry } from '../field-hooks'
import type { FieldHookComponent } from '../field-hooks'

describe('FieldHookRegistry', () => {
  let registry: FieldHookRegistry

  beforeEach(() => {
    registry = new FieldHookRegistry()
  })

  describe('register', () => {
    it('registers a hook with replace type', () => {
      const component: FieldHookComponent = () => null

      registry.register('test.field', component, 'replace')

      expect(registry.has('test.field')).toBe(true)
    })

    it('registers a hook with wrapper type', () => {
      const component: FieldHookComponent = () => null

      registry.register('test.field', component, 'wrapper')

      expect(registry.has('test.field')).toBe(true)
      const entry = registry.get('test.field')
      expect(entry?.type).toBe('wrapper')
    })

    it('defaults to replace type when not specified', () => {
      const component: FieldHookComponent = () => null

      registry.register('test.field', component)

      const entry = registry.get('test.field')
      expect(entry?.type).toBe('replace')
    })

    it('overwrites existing hook for same field path', () => {
      const component1: FieldHookComponent = () => null
      const component2: FieldHookComponent = () => null

      registry.register('test.field', component1, 'replace')
      registry.register('test.field', component2, 'wrapper')

      const entry = registry.get('test.field')
      expect(entry?.component).toBe(component2)
      expect(entry?.type).toBe('wrapper')
    })
  })

  describe('get', () => {
    it('returns hook entry for registered field path', () => {
      const component: FieldHookComponent = () => null

      registry.register('test.field', component, 'replace')

      const entry = registry.get('test.field')
      expect(entry).toBeDefined()
      expect(entry?.component).toBe(component)
      expect(entry?.type).toBe('replace')
    })

    it('returns undefined for unregistered field path', () => {
      const entry = registry.get('nonexistent.field')
      expect(entry).toBeUndefined()
    })

    it('returns correct entry for nested field paths', () => {
      const component: FieldHookComponent = () => null

      registry.register('config.section.field', component, 'wrapper')

      const entry = registry.get('config.section.field')
      expect(entry).toBeDefined()
      expect(entry?.type).toBe('wrapper')
    })
  })

  describe('has', () => {
    it('returns true for registered field path', () => {
      const component: FieldHookComponent = () => null

      registry.register('test.field', component)

      expect(registry.has('test.field')).toBe(true)
    })

    it('returns false for unregistered field path', () => {
      expect(registry.has('nonexistent.field')).toBe(false)
    })

    it('returns false after unregistering', () => {
      const component: FieldHookComponent = () => null

      registry.register('test.field', component)
      registry.unregister('test.field')

      expect(registry.has('test.field')).toBe(false)
    })
  })

  describe('unregister', () => {
    it('removes a registered hook', () => {
      const component: FieldHookComponent = () => null

      registry.register('test.field', component)
      expect(registry.has('test.field')).toBe(true)

      registry.unregister('test.field')
      expect(registry.has('test.field')).toBe(false)
    })

    it('does not throw when unregistering non-existent hook', () => {
      expect(() => registry.unregister('nonexistent.field')).not.toThrow()
    })

    it('only removes specified hook, not others', () => {
      const component1: FieldHookComponent = () => null
      const component2: FieldHookComponent = () => null

      registry.register('field1', component1)
      registry.register('field2', component2)

      registry.unregister('field1')

      expect(registry.has('field1')).toBe(false)
      expect(registry.has('field2')).toBe(true)
    })
  })

  describe('clear', () => {
    it('removes all registered hooks', () => {
      const component1: FieldHookComponent = () => null
      const component2: FieldHookComponent = () => null
      const component3: FieldHookComponent = () => null

      registry.register('field1', component1)
      registry.register('field2', component2)
      registry.register('field3', component3)

      expect(registry.getAllPaths()).toHaveLength(3)

      registry.clear()

      expect(registry.getAllPaths()).toHaveLength(0)
      expect(registry.has('field1')).toBe(false)
      expect(registry.has('field2')).toBe(false)
      expect(registry.has('field3')).toBe(false)
    })

    it('works correctly on empty registry', () => {
      expect(() => registry.clear()).not.toThrow()
      expect(registry.getAllPaths()).toHaveLength(0)
    })
  })

  describe('getAllPaths', () => {
    it('returns empty array when no hooks registered', () => {
      expect(registry.getAllPaths()).toEqual([])
    })

    it('returns all registered field paths', () => {
      const component: FieldHookComponent = () => null

      registry.register('field1', component)
      registry.register('field2', component)
      registry.register('field3', component)

      const paths = registry.getAllPaths()
      expect(paths).toHaveLength(3)
      expect(paths).toContain('field1')
      expect(paths).toContain('field2')
      expect(paths).toContain('field3')
    })

    it('returns updated paths after unregister', () => {
      const component: FieldHookComponent = () => null

      registry.register('field1', component)
      registry.register('field2', component)
      registry.register('field3', component)

      registry.unregister('field2')

      const paths = registry.getAllPaths()
      expect(paths).toHaveLength(2)
      expect(paths).toContain('field1')
      expect(paths).toContain('field3')
      expect(paths).not.toContain('field2')
    })

    it('handles nested field paths correctly', () => {
      const component: FieldHookComponent = () => null

      registry.register('config.chat.enabled', component)
      registry.register('config.chat.model', component)
      registry.register('config.api.key', component)

      const paths = registry.getAllPaths()
      expect(paths).toHaveLength(3)
      expect(paths).toContain('config.chat.enabled')
      expect(paths).toContain('config.chat.model')
      expect(paths).toContain('config.api.key')
    })
  })

  describe('integration scenarios', () => {
    it('supports full lifecycle of multiple hooks', () => {
      const replaceComponent: FieldHookComponent = () => null
      const wrapperComponent: FieldHookComponent = () => null

      registry.register('field1', replaceComponent, 'replace')
      registry.register('field2', wrapperComponent, 'wrapper')

      expect(registry.getAllPaths()).toHaveLength(2)

      const entry1 = registry.get('field1')
      expect(entry1?.type).toBe('replace')
      expect(entry1?.component).toBe(replaceComponent)

      const entry2 = registry.get('field2')
      expect(entry2?.type).toBe('wrapper')
      expect(entry2?.component).toBe(wrapperComponent)

      registry.unregister('field1')
      expect(registry.getAllPaths()).toHaveLength(1)
      expect(registry.has('field2')).toBe(true)

      registry.clear()
      expect(registry.getAllPaths()).toHaveLength(0)
    })

    it('handles rapid register/unregister cycles', () => {
      const component: FieldHookComponent = () => null

      for (let i = 0; i < 100; i++) {
        registry.register(`field${i}`, component)
      }
      expect(registry.getAllPaths()).toHaveLength(100)

      for (let i = 0; i < 50; i++) {
        registry.unregister(`field${i}`)
      }
      expect(registry.getAllPaths()).toHaveLength(50)

      registry.clear()
      expect(registry.getAllPaths()).toHaveLength(0)
    })
  })
})

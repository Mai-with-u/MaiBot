"use client"

import { useState, useCallback } from "react"
import { Plus, Trash2, ChevronRight, ChevronDown } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

// 生成唯一 ID
function generateId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).substring(2, 11)}`
}

type ValueType = 'string' | 'number' | 'boolean' | 'object' | 'array' | 'null'

interface TreeNode {
  id: string
  key: string
  value: unknown
  type: ValueType
  expanded?: boolean
  children?: TreeNode[]
}

interface NestedKeyValueEditorProps {
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
  placeholder?: string
}

// 推断值的类型
function inferType(value: unknown): ValueType {
  if (value === null) return 'null'
  if (Array.isArray(value)) return 'array'
  if (typeof value === 'object') return 'object'
  if (typeof value === 'boolean') return 'boolean'
  if (typeof value === 'number') return 'number'
  return 'string'
}

// 将 Record 转换为树节点数组
function recordToTree(record: Record<string, unknown>): TreeNode[] {
  return Object.entries(record).map(([key, value]) => {
    const type = inferType(value)
    const node: TreeNode = {
      id: generateId(),
      key,
      value,
      type,
      expanded: true,
    }

    if (type === 'object' && value && typeof value === 'object') {
      node.children = recordToTree(value as Record<string, unknown>)
    } else if (type === 'array' && Array.isArray(value)) {
      node.children = value.map((item, index) => {
        const itemType = inferType(item)
        const childNode: TreeNode = {
          id: generateId(),
          key: String(index),
          value: item,
          type: itemType,
          expanded: true,
        }
        if (itemType === 'object' && item && typeof item === 'object') {
          childNode.children = recordToTree(item as Record<string, unknown>)
        } else if (itemType === 'array' && Array.isArray(item)) {
          childNode.children = item.map((subItem, subIndex) => ({
            id: generateId(),
            key: String(subIndex),
            value: subItem,
            type: inferType(subItem),
            expanded: true,
          }))
        }
        return childNode
      })
    }

    return node
  })
}

// 将树节点数组转换为 Record
function treeToRecord(nodes: TreeNode[]): Record<string, unknown> {
  const record: Record<string, unknown> = {}
  for (const node of nodes) {
    if (!node.key.trim()) continue

    if (node.type === 'object' && node.children) {
      record[node.key] = treeToRecord(node.children)
    } else if (node.type === 'array' && node.children) {
      record[node.key] = node.children.map(child => {
        if (child.type === 'object' && child.children) {
          return treeToRecord(child.children)
        } else if (child.type === 'array' && child.children) {
          return child.children.map(c => c.value)
        }
        return child.value
      })
    } else if (node.type === 'null') {
      record[node.key] = null
    } else {
      record[node.key] = node.value
    }
  }
  return record
}

// 转换简单值
function convertSimpleValue(value: string, type: ValueType): unknown {
  switch (type) {
    case 'boolean':
      return value === 'true'
    case 'number': {
      const num = parseFloat(value)
      return isNaN(num) ? 0 : num
    }
    case 'null':
      return null
    default:
      return value
  }
}

// 树节点组件
function TreeNodeItem({
  node,
  level,
  onUpdate,
  onRemove,
  onAddChild,
  onToggleExpand,
}: {
  node: TreeNode
  level: number
  onUpdate: (id: string, field: 'key' | 'value' | 'type', value: unknown) => void
  onRemove: (id: string) => void
  onAddChild: (parentId: string) => void
  onToggleExpand: (id: string) => void
}) {
  const isContainer = node.type === 'object' || node.type === 'array'
  const hasChildren = node.children && node.children.length > 0

  return (
    <div className="space-y-1">
      <div
        className="grid gap-2 items-center"
        style={{
          gridTemplateColumns: isContainer
            ? '32px 1fr 90px 64px'
            : '32px 1fr 1fr 90px 32px',
          paddingLeft: `${level * 20}px`,
        }}
      >
        {/* 展开/折叠按钮 */}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={() => onToggleExpand(node.id)}
          disabled={!isContainer || !hasChildren}
        >
          {isContainer && hasChildren ? (
            node.expanded ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )
          ) : (
            <span className="w-4" />
          )}
        </Button>

        {/* 键名 */}
        <Input
          value={node.key}
          onChange={(e) => onUpdate(node.id, 'key', e.target.value)}
          placeholder="key"
          className="h-8 text-sm"
        />

        {/* 值（仅简单类型显示） */}
        {!isContainer && (
          <>
            {node.type === 'boolean' ? (
              <div className="flex items-center h-8 px-3 border rounded-md bg-background">
                <Switch
                  checked={node.value === true}
                  onCheckedChange={(checked) => onUpdate(node.id, 'value', checked)}
                />
                <span className="ml-2 text-sm text-muted-foreground">
                  {node.value ? 'true' : 'false'}
                </span>
              </div>
            ) : node.type === 'null' ? (
              <div className="flex items-center h-8 px-3 border rounded-md bg-muted text-sm text-muted-foreground">
                null
              </div>
            ) : (
              <Input
                type={node.type === 'number' ? 'number' : 'text'}
                value={node.value as string | number}
                onChange={(e) => onUpdate(node.id, 'value', e.target.value)}
                placeholder="value"
                className="h-8 text-sm"
                step={node.type === 'number' ? 'any' : undefined}
              />
            )}
          </>
        )}

        {/* 类型选择 */}
        <Select
          value={node.type}
          onValueChange={(v) => onUpdate(node.id, 'type', v as ValueType)}
        >
          <SelectTrigger className="h-8 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="string">字符串</SelectItem>
            <SelectItem value="number">数字</SelectItem>
            <SelectItem value="boolean">布尔</SelectItem>
            <SelectItem value="null">Null</SelectItem>
            <SelectItem value="object">对象</SelectItem>
            <SelectItem value="array">数组</SelectItem>
          </SelectContent>
        </Select>

        {/* 操作按钮 */}
        <div className="flex gap-1 justify-end">
          {isContainer && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-muted-foreground hover:text-primary"
              onClick={() => onAddChild(node.id)}
              title="添加子项"
            >
              <Plus className="h-4 w-4" />
            </Button>
          )}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8 text-muted-foreground hover:text-destructive"
            onClick={() => onRemove(node.id)}
            title="删除"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* 子节点 */}
      {isContainer && node.expanded && node.children && node.children.length > 0 && (
        <div className="space-y-1">
          {node.children.map((child) => (
            <TreeNodeItem
              key={child.id}
              node={child}
              level={level + 1}
              onUpdate={onUpdate}
              onRemove={onRemove}
              onAddChild={onAddChild}
              onToggleExpand={onToggleExpand}
            />
          ))}
        </div>
      )}
    </div>
  )
}

export function NestedKeyValueEditor({
  value,
  onChange,
  placeholder = "添加参数...",
}: NestedKeyValueEditorProps) {
  const [nodes, setNodes] = useState<TreeNode[]>(() => recordToTree(value || {}))

  // 同步到父组件
  const syncToParent = useCallback(
    (newNodes: TreeNode[]) => {
      setNodes(newNodes)
      onChange(treeToRecord(newNodes))
    },
    [onChange]
  )

  // 添加根节点
  const addRootNode = useCallback(() => {
    const newNode: TreeNode = {
      id: generateId(),
      key: '',
      value: '',
      type: 'string',
      expanded: false,
    }
    syncToParent([...nodes, newNode])
  }, [nodes, syncToParent])

  // 更新节点
  const updateNode = useCallback(
    (id: string, field: 'key' | 'value' | 'type', newValue: unknown) => {
      const updateRecursive = (nodes: TreeNode[]): TreeNode[] => {
        return nodes.map((node) => {
          if (node.id === id) {
            if (field === 'type') {
              const newType = newValue as ValueType
              if (newType === 'object') {
                return { ...node, type: newType, value: {}, children: [] }
              } else if (newType === 'array') {
                return { ...node, type: newType, value: [], children: [] }
              } else if (newType === 'null') {
                return { ...node, type: newType, value: null }
              } else {
                const converted = convertSimpleValue(String(node.value), newType)
                return { ...node, type: newType, value: converted, children: undefined }
              }
            } else if (field === 'value') {
              const converted = convertSimpleValue(String(newValue), node.type)
              return { ...node, value: converted }
            } else {
              return { ...node, [field]: String(newValue) }
            }
          }
          if (node.children) {
            return { ...node, children: updateRecursive(node.children) }
          }
          return node
        })
      }
      syncToParent(updateRecursive(nodes))
    },
    [nodes, syncToParent]
  )

  // 删除节点
  const removeNode = useCallback(
    (id: string) => {
      const removeRecursive = (nodes: TreeNode[]): TreeNode[] => {
        return nodes
          .filter((node) => node.id !== id)
          .map((node) => {
            if (node.children) {
              return { ...node, children: removeRecursive(node.children) }
            }
            return node
          })
      }
      syncToParent(removeRecursive(nodes))
    },
    [nodes, syncToParent]
  )

  // 添加子节点
  const addChildNode = useCallback(
    (parentId: string) => {
      const addRecursive = (nodes: TreeNode[]): TreeNode[] => {
        return nodes.map((node) => {
          if (node.id === parentId) {
            const newChild: TreeNode = {
              id: generateId(),
              key: node.type === 'array' ? String(node.children?.length || 0) : '',
              value: '',
              type: 'string',
              expanded: true,
            }
            return {
              ...node,
              children: [...(node.children || []), newChild],
            }
          }
          if (node.children) {
            return { ...node, children: addRecursive(node.children) }
          }
          return node
        })
      }
      syncToParent(addRecursive(nodes))
    },
    [nodes, syncToParent]
  )

  // 切换展开/折叠
  const toggleExpand = useCallback(
    (id: string) => {
      const toggleRecursive = (nodes: TreeNode[]): TreeNode[] => {
        return nodes.map((node) => {
          if (node.id === id) {
            return { ...node, expanded: !node.expanded }
          }
          if (node.children) {
            return { ...node, children: toggleRecursive(node.children) }
          }
          return node
        })
      }
      setNodes(toggleRecursive(nodes))
    },
    [nodes]
  )

  return (
    <div className="h-full flex flex-col gap-2">
      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">
          {nodes.length} 个参数
        </span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={addRootNode}
          className="h-7 text-xs"
        >
          <Plus className="h-3 w-3 mr-1" />
          添加参数
        </Button>
      </div>

      {/* 内容区域 */}
      <div className="flex-1 overflow-y-auto space-y-1">
        {nodes.length === 0 ? (
          <div className="text-sm text-muted-foreground text-center py-4 border border-dashed rounded-md">
            {placeholder}
          </div>
        ) : (
          <div className="space-y-1">
            {/* 表头 */}
            <div
              className="grid gap-2 text-xs text-muted-foreground px-1 sticky top-0 bg-background z-10"
              style={{
                gridTemplateColumns: '32px 1fr 1fr 90px 32px',
              }}
            >
              <span></span>
              <span>键名</span>
              <span>值</span>
              <span>类型</span>
              <span></span>
            </div>
            {nodes.map((node) => (
              <TreeNodeItem
                key={node.id}
                node={node}
                level={0}
                onUpdate={updateNode}
                onRemove={removeNode}
                onAddChild={addChildNode}
                onToggleExpand={toggleExpand}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

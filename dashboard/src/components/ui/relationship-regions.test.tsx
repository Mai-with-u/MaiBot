import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import { RelationshipGraph } from './relationship-graph'

function contains(path: string, x: number, y: number) {
  const points = [...path.matchAll(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g)].map((match) => [
    Number(match[1]),
    Number(match[2]),
  ])
  let inside = false
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [xi, yi] = points[i]
    const [xj, yj] = points[j]
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}
it('合并视图每组只有一个连续容器，共同成员位于真实交集', async () => {
  const user = userEvent.setup()
  const { container } = render(
    <RelationshipGraph
      layout="overview"
      columns={['组', '成员']}
      nodes={[
        { id: 'a', label: '甲组', column: 0, colors: ['#39845A'] },
        { id: 'b', label: '乙组', column: 0, colors: ['#397FAE'] },
        { id: 'one', label: '甲独有', column: 1 },
        { id: 'two', label: '乙独有', column: 1 },
        { id: 'shared', label: '共同聊天', column: 1 },
      ]}
      edges={[
        { from: 'a', to: 'one' },
        { from: 'b', to: 'two' },
        { from: 'a', to: 'shared' },
        { from: 'b', to: 'shared' },
      ]}
      onSelect={() => {}}
    />
  )
  await user.click(screen.getByRole('switch', { name: '合并共同区' }))
  expect(screen.queryByRole('region', { name: '共同成员' })).not.toBeInTheDocument()
  const paths = ['a', 'b'].map(
    (id) => container.querySelector(`[data-group-region="${id}"]`)!.getAttribute('d')!
  )
  for (const path of paths) expect(path.match(/M /g)).toHaveLength(1)
  for (const [name, membership] of [
    ['甲独有', [true, false]],
    ['乙独有', [false, true]],
    ['共同聊天', [true, true]],
  ] as const) {
    const node = screen.getByRole('button', { name })
    const x = parseFloat(node.style.left) + 115
    const y = parseFloat(node.style.top) + 30
    expect(paths.map((path) => contains(path, x, y))).toEqual(membership)
  }
  expect(screen.getAllByRole('button', { name: '甲组' })).toHaveLength(1)
  expect(screen.getAllByRole('button', { name: '乙组' })).toHaveLength(1)
})

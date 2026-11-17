import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import * as api from '@/lib/person-api'
import type { IndividualDirectoryEntry, PersonInfo } from '@/types/person'
import { PersonManagementPage } from '../person'

vi.mock('@/lib/person-api', () => ({
  getIndividualDirectory: vi.fn(), getPersonStats: vi.fn(), getIndividual: vi.fn(),
  getPersonList: vi.fn(), getPersonDetail: vi.fn(), updatePerson: vi.fn(),
  deletePerson: vi.fn(), batchDeletePersons: vi.fn(), mergeIndividuals: vi.fn(), previewIndividualMerge: vi.fn(),
}))

function person(id: number, owner: string, platform: string): PersonInfo {
  return {
    id, source_person_name: `账号${id}`, person_id: `p${id}`, individual_id: owner, person_name: `账号${id}`, nickname: null,
    platform, user_id: `100${id}`, is_known: true, group_nick_name: null,
    know_times: 1, know_since: null, last_know: null,
  }
}

const groups: IndividualDirectoryEntry[] = [
  { individual_id: 'i1', display_name: '千石', is_known: true, profile_sources: [], member_count: 2, members: [person(1, 'i1', 'qq'), person(2, 'i1', 'telegram')] },
  { individual_id: 'i2', display_name: '小麦', is_known: true, profile_sources: [], member_count: 1, members: [person(3, 'i2', 'qq')] },
]

beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.setPointerCapture = () => {}
  Element.prototype.releasePointerCapture = () => {}
  Element.prototype.scrollIntoView = () => {}
  vi.mocked(api.getPersonStats).mockResolvedValue({ total: 3, individuals: 2, linked_individuals: 1, known: 3, unknown: 0, platforms: { qq: 2, telegram: 1 } })
  vi.mocked(api.getIndividualDirectory).mockImplementation(async params => {
    const entries = params.search === 'i1' ? groups.slice(0, 1) : groups
    const data = params.view === 'persons' ? entries.flatMap(entry => entry.members.map(member => ({ ...entry, members: [member] }))) : entries
    return { data, total: data.length, page: 1, page_size: 20 }
  })
  vi.mocked(api.getIndividual).mockImplementation(async id => groups.find(group => group.individual_id === id)!)
  vi.mocked(api.previewIndividualMerge).mockImplementation(async (target, ids) => ({
    target_individual_id: target, revision: 'revision-1',
    profile: { display_name: '千石', is_known: true }, conflicts: ids.length > 1 ? ['display_name'] : [],
    sources: groups.filter(group => ids.includes(group.individual_id)).map(group => ({ ...group, origins: [] })),
    members: Object.fromEntries(groups.filter(group => ids.includes(group.individual_id)).flatMap(group => group.members.map(member => [member.person_id, member.individual_id]))),
  }))
  vi.mocked(api.mergeIndividuals).mockResolvedValue({ ...groups[0], members: groups.flatMap(group => group.members) })
})

afterEach(cleanup)

async function openPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><PersonManagementPage /></QueryClientProvider>)
  await screen.findByText('账号2')
}

it('默认展示完整成员关系，账号视图可以返回所属个体', async () => {
  await openPage()
  expect(screen.getByText('千石').closest('td')).toHaveAttribute('rowspan', '2')
  expect(screen.getByText('telegram')).toBeVisible()
  await userEvent.click(screen.getByRole('tab', { name: '全部账号' }))
  await screen.findAllByRole('button', { name: '2 个账号 · 查看关系' })
  expect(api.getIndividualDirectory).toHaveBeenLastCalledWith(expect.objectContaining({ view: 'persons' }))
  await userEvent.click(screen.getAllByRole('button', { name: '2 个账号 · 查看关系' })[0])
  await waitFor(() => expect(api.getIndividualDirectory).toHaveBeenLastCalledWith(expect.objectContaining({ view: 'individuals', search: 'i1' })))
  expect(screen.getByRole('textbox', { name: '搜索个体或账号' })).toHaveValue('i1')
})

it('选择个体后按全部成员提交合并快照，不把个体选择键当作 person_id', async () => {
  await openPage()
  await userEvent.click(screen.getByRole('checkbox', { name: '选择个体 千石' }))
  await userEvent.click(screen.getByRole('checkbox', { name: '选择个体 小麦' }))
  expect(screen.getByText('已选 2 个体 · 3 账号')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: '合并个体' }))
  const confirm = await screen.findByRole('button', { name: '确认合并全部账号' })
  expect(confirm).toBeDisabled()
  await userEvent.click(await screen.findByRole('button', { name: '采用 小麦：小麦' }))
  expect(screen.getByRole('textbox', { name: '人物名称' })).toHaveValue('小麦')
  await userEvent.click(confirm)
  await waitFor(() => expect(api.mergeIndividuals).toHaveBeenCalledWith(expect.objectContaining({ revision: 'revision-1', members: { p1: 'i1', p2: 'i1', p3: 'i2' } }), { display_name: '小麦', is_known: true }, ['display_name']))
})


it('人物和账号编辑分别提交共享资料与昵称', async () => {
  vi.mocked(api.getPersonDetail).mockResolvedValue({ ...groups[0].members[0], person_name: '千石' })
  vi.mocked(api.updatePerson).mockResolvedValue(groups[0].members[0])
  await openPage()
  await userEvent.click(screen.getByRole('button', { name: '编辑人物 千石' }))
  await screen.findByRole('heading', { name: '编辑人物资料' })
  expect(screen.queryByRole('textbox', { name: '账号昵称' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(api.updatePerson).toHaveBeenCalled())
  expect(vi.mocked(api.updatePerson).mock.calls[0][1]).not.toHaveProperty('nickname')
  await userEvent.click(screen.getByRole('button', { name: '编辑账号 1001' }))
  const nickname = await screen.findByRole('textbox', { name: '账号昵称' })
  expect(screen.queryByRole('textbox', { name: '人物名称' })).not.toBeInTheDocument()
  await userEvent.type(nickname, '平台名字')
  await userEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(api.updatePerson).toHaveBeenLastCalledWith('p1', { nickname: '平台名字' }))
})

it('账号视图以账号为首列，平台入口只筛选账号', async () => {
  await openPage()
  await userEvent.click(screen.getByRole('tab', { name: '全部账号' }))
  const headers = screen.getAllByRole('columnheader').map(header => header.textContent)
  expect(headers.slice(1, 5)).toEqual(['账号', '平台', 'ID', '所属人物'])
  expect(screen.queryByRole('combobox', { name: '平台筛选' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'telegram · 1' }))
  await waitFor(() => expect(api.getIndividualDirectory).toHaveBeenLastCalledWith(expect.objectContaining({ view: 'persons', platform: 'telegram' })))
})

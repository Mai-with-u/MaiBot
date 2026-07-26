import { beforeEach, describe, expect, it, vi } from 'vitest'
import { openDB, type IDBPDatabase } from 'idb'

import { generateId } from '@/lib/id'

import {
  addAsset,
  deleteAsset,
  getAsset,
  listAssets,
  openAssetDB,
  type AssetRecord,
} from '../asset-store'

vi.mock('idb', () => ({
  openDB: vi.fn(),
}))

vi.mock('@/lib/id', () => ({
  generateId: vi.fn(),
}))

const openDBMock = vi.mocked(openDB)
const generateIdMock = vi.mocked(generateId)

/** 只包含被测模块用到的数据库方法的假实现 */
type FakeDB = {
  add: ReturnType<typeof vi.fn>
  get: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
  getAll: ReturnType<typeof vi.fn>
}

/** upgrade 回调实际用到的最小 db 形状（避免构造完整 IDBPDatabase） */
type UpgradeFn = (db: {
  objectStoreNames: { contains: (name: string) => boolean }
  createObjectStore: (name: string, options: { keyPath: string }) => void
}) => void

function makeAssetRecord(overrides: Partial<AssetRecord> = {}): AssetRecord {
  return {
    id: 'asset-1',
    filename: 'pic.png',
    type: 'image',
    mimeType: 'image/png',
    blob: new Blob(['x']),
    size: 1,
    createdAt: 100,
    ...overrides,
  }
}

let fakeDB: FakeDB

beforeEach(() => {
  fakeDB = {
    add: vi.fn(),
    get: vi.fn(),
    delete: vi.fn(),
    getAll: vi.fn(),
  }
  openDBMock.mockResolvedValue(fakeDB as unknown as IDBPDatabase<unknown>)
})

describe('openAssetDB', () => {
  it('用固定库名与版本号打开数据库并注册升级回调', async () => {
    await openAssetDB()

    expect(openDBMock).toHaveBeenCalledWith(
      'maibot-assets',
      1,
      expect.objectContaining({ upgrade: expect.any(Function) })
    )
  })

  it('升级回调在 store 不存在时创建以 id 为主键的 object store', async () => {
    await openAssetDB()
    const upgrade = openDBMock.mock.calls[0][2]?.upgrade as unknown as UpgradeFn

    const createObjectStore = vi.fn()
    upgrade({
      objectStoreNames: { contains: () => false },
      createObjectStore,
    })

    expect(createObjectStore).toHaveBeenCalledWith('assets', { keyPath: 'id' })
  })

  it('升级回调在 store 已存在时不重复创建', async () => {
    await openAssetDB()
    const upgrade = openDBMock.mock.calls[0][2]?.upgrade as unknown as UpgradeFn

    const createObjectStore = vi.fn()
    upgrade({
      objectStoreNames: { contains: () => true },
      createObjectStore,
    })

    expect(createObjectStore).not.toHaveBeenCalled()
  })
})

describe('addAsset', () => {
  it('存储图片文件：生成 UUID 作为 ID 并写入完整资源记录', async () => {
    generateIdMock.mockReturnValue('generated-id-1')
    const file = new File(['abc'], 'pic.png', { type: 'image/png' })

    await expect(addAsset(file)).resolves.toBe('generated-id-1')

    expect(fakeDB.add).toHaveBeenCalledWith('assets', {
      id: 'generated-id-1',
      filename: 'pic.png',
      type: 'image',
      mimeType: 'image/png',
      blob: file,
      size: 3,
      createdAt: expect.any(Number),
    })
  })

  it('video/ 开头的 MIME 类型识别为视频资源', async () => {
    generateIdMock.mockReturnValue('generated-id-2')
    const file = new File(['movie'], 'clip.mp4', { type: 'video/mp4' })

    await addAsset(file)

    const record = fakeDB.add.mock.calls[0][1] as AssetRecord
    expect(record.type).toBe('video')
    expect(record.mimeType).toBe('video/mp4')
  })

  it('非视频 MIME 类型一律按图片处理', async () => {
    generateIdMock.mockReturnValue('generated-id-3')
    const file = new File(['doc'], 'file.gif', { type: 'image/gif' })

    await addAsset(file)

    const record = fakeDB.add.mock.calls[0][1] as AssetRecord
    expect(record.type).toBe('image')
  })
})

describe('getAsset', () => {
  it('按 ID 从 assets store 读取资源记录', async () => {
    const record = makeAssetRecord({ id: 'asset-42' })
    fakeDB.get.mockResolvedValue(record)

    await expect(getAsset('asset-42')).resolves.toBe(record)
    expect(fakeDB.get).toHaveBeenCalledWith('assets', 'asset-42')
  })

  it('资源不存在时返回 undefined', async () => {
    fakeDB.get.mockResolvedValue(undefined)

    await expect(getAsset('missing-id')).resolves.toBeUndefined()
  })
})

describe('deleteAsset', () => {
  it('按 ID 从 assets store 删除资源', async () => {
    fakeDB.delete.mockResolvedValue(undefined)

    await deleteAsset('asset-7')

    expect(fakeDB.delete).toHaveBeenCalledWith('assets', 'asset-7')
  })
})

describe('listAssets', () => {
  it('返回按创建时间倒序排列的资源列表（最新的在前）', async () => {
    const oldest = makeAssetRecord({ id: 'a', createdAt: 100 })
    const newest = makeAssetRecord({ id: 'b', createdAt: 300 })
    const middle = makeAssetRecord({ id: 'c', createdAt: 200 })
    fakeDB.getAll.mockResolvedValue([oldest, newest, middle])

    await expect(listAssets()).resolves.toEqual([newest, middle, oldest])
    expect(fakeDB.getAll).toHaveBeenCalledWith('assets')
  })

  it('没有任何资源时返回空数组', async () => {
    fakeDB.getAll.mockResolvedValue([])

    await expect(listAssets()).resolves.toEqual([])
  })
})

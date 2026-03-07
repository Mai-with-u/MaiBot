/**
 * IndexedDB 资源存储模块
 * 使用 idb 库封装所有 IndexedDB 操作，用于存储图片和视频资源
 */

import { openDB, type IDBPDatabase } from 'idb'

/**
 * 资源记录的类型定义
 */
export type AssetRecord = {
  /** 资源唯一标识符 (UUID v4) */
  id: string
  /** 文件名 */
  filename: string
  /** 资源类型 */
  type: 'image' | 'video'
  /** MIME 类型 */
  mimeType: string
  /** 文件内容 */
  blob: Blob
  /** 文件大小（字节） */
  size: number
  /** 创建时间戳 */
  createdAt: number
}

// 常量定义
const DB_NAME = 'maibot-assets'
const STORE_NAME = 'assets'
const DB_VERSION = 1

/**
 * 打开或创建资源数据库
 * 初始化 IndexedDB 数据库，如需要则创建 object store
 *
 * @returns 打开的数据库实例
 */
export async function openAssetDB(): Promise<IDBPDatabase<unknown>> {
  return openDB(DB_NAME, DB_VERSION, {
    upgrade(db) {
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'id' })
      }
    },
  })
}

/**
 * 存储文件到 IndexedDB
 * 根据文件 MIME 类型自动判断资源类型，使用 UUID v4 作为资源 ID
 *
 * @param file - 要存储的文件
 * @returns 生成的资源 ID (UUID v4)
 */
export async function addAsset(file: File): Promise<string> {
  const db = await openAssetDB()
  const id = crypto.randomUUID()

  // 根据 file.type 判断资源类型
  const type: 'image' | 'video' = file.type.startsWith('video/') ? 'video' : 'image'

  const asset: AssetRecord = {
    id,
    filename: file.name,
    type,
    mimeType: file.type,
    blob: file,
    size: file.size,
    createdAt: Date.now(),
  }

  await db.add(STORE_NAME, asset)
  return id
}

/**
 * 获取指定 ID 的资源记录
 * 如果资源不存在，返回 undefined
 *
 * @param id - 资源 ID
 * @returns 资源记录或 undefined
 */
export async function getAsset(id: string): Promise<AssetRecord | undefined> {
  const db = await openAssetDB()
  return (await db.get(STORE_NAME, id)) as AssetRecord | undefined
}

/**
 * 删除指定 ID 的资源
 * 如果资源不存在，该操作不会抛出错误
 *
 * @param id - 资源 ID
 */
export async function deleteAsset(id: string): Promise<void> {
  const db = await openAssetDB()
  await db.delete(STORE_NAME, id)
}

/**
 * 获取所有资源记录列表
 * 返回按创建时间倒序排列的资源列表
 *
 * @returns 资源记录数组
 */
export async function listAssets(): Promise<AssetRecord[]> {
  const db = await openAssetDB()
  const assets = (await db.getAll(STORE_NAME)) as AssetRecord[]
  // 按创建时间倒序排列（最新的在前）
  return assets.sort((a, b) => b.createdAt - a.createdAt)
}

import { afterEach, describe, expect, it, vi } from 'vitest'
import { prepareUploadImage } from '@/lib/upload-image'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function bitmap(width: number, height: number) {
  const image = { width, height, close: vi.fn() }
  vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue(image))
  return image
}

function encoder() {
  const context = { fillStyle: '', fillRect: vi.fn(), drawImage: vi.fn() }
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  const encode = vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function (callback) {
    callback(new Blob(['encoded'], { type: 'image/jpeg' }))
  })
  return { context, encode }
}

describe('upload image preprocessing', () => {
  it('rejects excessive source pixels before allocating a canvas and releases the bitmap', async () => {
    const image = bitmap(12000, 12000)
    const create = vi.spyOn(document, 'createElement')
    await expect(prepareUploadImage(new File(['source'], 'huge.png'), 4000)).rejects.toThrow('1 亿像素')
    expect(create).not.toHaveBeenCalled()
    expect(image.close).toHaveBeenCalledOnce()
  })
  it('preserves small files with acceptable dimensions', async () => {
    const image = bitmap(320, 720)
    const file = new File(['source'], 'x.png', { type: 'image/png' })
    expect((await prepareUploadImage(file, 4000)).file).toBe(file)
    expect(image.close).toHaveBeenCalledOnce()
    vi.unstubAllGlobals()
  })

  it('shrinks a small file with oversized dimensions and preserves aspect ratio', async () => {
    bitmap(8000, 6000)
    const { context, encode } = encoder()
    const source = new File(['source'], 'x.png', { type: 'image/png' })
    const result = await prepareUploadImage(source, 4000)
    expect(result.file.name).toBe('x.jpg')
    expect(result.file.type).toBe('image/jpeg')
    expect(context.drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 4000, 3000)
    expect(encode).toHaveBeenCalledWith(expect.any(Function), 'image/jpeg', 0.9)
    expect(source.name).toBe('x.png')
    vi.unstubAllGlobals()
  })

  it('compresses files over 20MiB without unnecessarily shrinking acceptable dimensions', async () => {
    bitmap(3000, 2000)
    const { context } = encoder()
    const source = new File([new Uint8Array(20 * 1024 * 1024 + 1)], 'large.png', { type: 'image/png' })
    const result = await prepareUploadImage(source, 4000)
    expect(context.drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 3000, 2000)
    expect(result.file.size).toBeLessThan(20 * 1024 * 1024)
    vi.unstubAllGlobals()
  })
})

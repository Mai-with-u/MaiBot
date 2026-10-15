const MAX_UPLOAD_BYTES = 20 * 1024 * 1024

export interface PreparedImage {
  file: File
  note?: string
}

/** Prepare a separate upload copy, preserving the whole image and local source. */
export async function prepareUploadImage(file: File, maxEdge: number): Promise<PreparedImage> {
  if (!Number.isInteger(maxEdge) || maxEdge < 1) throw new Error('Invalid image size limit')
  const bitmap = await createImageBitmap(file)
  try {
    if (bitmap.width * bitmap.height > 100_000_000) throw new Error('原图超过 1 亿像素，请缩小后重试')
    if (bitmap.width <= maxEdge && bitmap.height <= maxEdge && file.size <= MAX_UPLOAD_BYTES) return { file }
    const scale = Math.min(1, maxEdge / bitmap.width, maxEdge / bitmap.height)
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(bitmap.width * scale))
    canvas.height = Math.max(1, Math.round(bitmap.height * scale))
    const context = canvas.getContext('2d')
    if (!context) throw new Error('浏览器无法进行图片压缩')
    // JPEG has no alpha: use white instead of silently turning transparency black.
    context.fillStyle = '#ffffff'
    context.fillRect(0, 0, canvas.width, canvas.height)
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    for (const quality of [0.9, 0.82, 0.7, 0.55, 0.4]) {
      const blob = await new Promise<Blob>((resolve, reject) => {
        canvas.toBlob(result => result ? resolve(result) : reject(new Error('JPEG编码失败')), 'image/jpeg', quality)
      })
      if (blob.size <= MAX_UPLOAD_BYTES) {
        const name = file.name.replace(/\.[^.]+$/, '') + '.jpg'
        return {
          file: new File([blob], name, { type: 'image/jpeg', lastModified: file.lastModified }),
          note: `已转JPEG ${canvas.width}×${canvas.height}，${(blob.size / 1024 / 1024).toFixed(2)}MiB`,
        }
      }
    }
    throw new Error('压缩后仍超过20MiB，请降低图片尺寸后重试')
  } finally {
    bitmap.close()
  }
}

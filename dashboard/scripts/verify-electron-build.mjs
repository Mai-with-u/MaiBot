import { access, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const dashboardDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const packageJson = JSON.parse(
  await readFile(path.join(dashboardDirectory, 'package.json'), 'utf8')
)
const mainEntry = path.resolve(dashboardDirectory, packageJson.main)

await access(mainEntry)
const mainSource = await readFile(mainEntry, 'utf8')

if (/require\((['"])electron-store\1\)/.test(mainSource)) {
  throw new Error('Electron 主进程仍在运行时 require ESM-only 的 electron-store')
}

console.log(`Electron 主进程产物校验通过：${path.relative(dashboardDirectory, mainEntry)}`)

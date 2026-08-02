const WEBUI_HEADING_PATTERN = /^(#{2,6})\s+webui(?:\s+\[([^\]]+)\])?\s*$/i

function parseWebuiHeading(line: string): RegExpExecArray | null {
  return WEBUI_HEADING_PATTERN.exec(line)
}

export function extractWebuiSections(markdown: string): string {
  const lines = markdown.split(/\r?\n/)
  const sections: string[] = []

  for (let index = 0; index < lines.length; index += 1) {
    const heading = parseWebuiHeading(lines[index])
    if (!heading) {
      continue
    }

    const headingLevel = heading[1].length
    let end = index + 1
    while (end < lines.length) {
      const nextHeading = /^(#{1,6})\s+/.exec(lines[end])
      if (nextHeading && nextHeading[1].length <= headingLevel) {
        break
      }
      end += 1
    }
    sections.push(lines.slice(index, end).join('\n').trim())
    index = end - 1
  }

  return sections.join('\n\n')
}

export function extractWebuiVersion(markdown: string): string | null {
  for (const line of markdown.split(/\r?\n/)) {
    const version = parseWebuiHeading(line)?.[2]?.trim()
    if (version) {
      return version
    }
  }
  return null
}

export function removeTopLevelHeadings(markdown: string): string {
  return markdown
    .split(/\r?\n/)
    .filter((line) => !/^#\s+/.test(line))
    .join('\n')
    .trim()
}

export function removeWebuiVersions(markdown: string): string {
  return markdown
    .split(/\r?\n/)
    .map((line) => (parseWebuiHeading(line) ? line.replace(/\s+\[[^\]]+\]\s*$/, '') : line))
    .join('\n')
}

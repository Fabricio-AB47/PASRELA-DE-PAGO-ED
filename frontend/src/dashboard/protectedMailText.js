// Keep one native text control while mapping read-only data back to template tokens.
export function protectedMailText(template, values = {}) {
  const ranges = []
  let text = ''
  let previousEnd = 0
  for (const match of template.matchAll(/\{([^{}]+)\}/g)) {
    text += template.slice(previousEnd, match.index)
    const value = String(values[match[1]] ?? match[0]) || match[0]
    ranges.push({ start: text.length, end: text.length + value.length,
      sourceStart: match.index, sourceEnd: match.index + match[0].length })
    text += value
    previousEnd = match.index + match[0].length
  }
  text += template.slice(previousEnd)
  return { text, ranges }
}

export function editProtectedMailText(template, nextText, values = {}) {
  const { text, ranges } = protectedMailText(template, values)
  if (text === nextText) return template
  let start = 0
  while (start < text.length && start < nextText.length && text[start] === nextText[start]) start += 1
  let oldEnd = text.length
  let newEnd = nextText.length
  while (oldEnd > start && newEnd > start && text[oldEnd - 1] === nextText[newEnd - 1]) {
    oldEnd -= 1
    newEnd -= 1
  }
  if (ranges.some((range) => start < range.end && oldEnd > range.start
    || start === oldEnd && start > range.start && start < range.end)) return null
  const insertion = nextText.slice(start, newEnd)
  if (/[{}]/.test(insertion)) return null
  function sourceOffset(offset) {
    return offset + ranges.filter((range) => range.end <= offset)
      .reduce((difference, range) => difference + (range.sourceEnd - range.sourceStart) - (range.end - range.start), 0)
  }
  return template.slice(0, sourceOffset(start)) + insertion + template.slice(sourceOffset(oldEnd))
}

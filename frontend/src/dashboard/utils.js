export const STATUS_OPTIONS = [
  { value: 'A', label: 'Activo' },
  { value: 'P', label: 'Inactivo' },
]

export const statusLabel = (estado) => (estado === 'A' ? 'Activo' : 'Inactivo')
export const categoryLabel = (value, fallback = 'Sin categoria') => String(value || '').trim() || fallback
export const normalizeSearchText = (value) =>
  String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim()
export const uniqueSortedValues = (values, fallback = 'Sin categoria') =>
  Array.from(new Set((values || []).map((value) => categoryLabel(value, fallback)))).sort((first, second) => {
    const firstNumber = Number(first)
    const secondNumber = Number(second)

    if (Number.isFinite(firstNumber) && Number.isFinite(secondNumber)) {
      return firstNumber - secondNumber
    }

    return first.localeCompare(second)
  })
export const nextSubjectCode = (pensum) => {
  const numericCodes = (pensum || [])
    .map((item) => String(item.codigo_materia || '').trim())
    .filter((code) => /^\d+$/.test(code))

  if (!numericCodes.length) {
    return '1'
  }

  const nextCode = Math.max(...numericCodes.map((code) => Number(code))) + 1
  const minLength = Math.max(...numericCodes.map((code) => code.length))
  return String(nextCode).padStart(minLength, '0')
}
export const sortByCareerCode = (items) =>
  [...(items || [])].sort((first, second) => {
    const firstCode = Number(first.cod_anio_basica)
    const secondCode = Number(second.cod_anio_basica)

    if (Number.isFinite(firstCode) && Number.isFinite(secondCode)) {
      return firstCode - secondCode
    }

    return String(first.cod_anio_basica || '').localeCompare(String(second.cod_anio_basica || ''))
  })


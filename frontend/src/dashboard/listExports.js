import { downloadBlobResponse, readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

function exportRow(person) {
  return {
    nombre: person?.nombre || person?.nombres || '',
    correo_personal: person?.correo_personal || '',
    correo_intec: person?.correo_intec || person?.login || '',
    telefono: person?.telefono || '',
    movil: person?.movil || '',
  }
}

export async function downloadPeopleList({ kind, format, title, rows }) {
  const response = await adminFetch('/api/auth/admin/list-export/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      kind,
      format,
      title,
      rows: (rows || []).map(exportRow),
    }),
  })

  if (!response.ok) {
    const payload = await readResponsePayload(response)
    throw new Error(payload?.message || `No fue posible generar el archivo (${response.status}).`)
  }

  await downloadBlobResponse(
    response,
    `listado-${kind === 'teachers' ? 'docentes' : 'estudiantes'}.${format}`,
  )
}

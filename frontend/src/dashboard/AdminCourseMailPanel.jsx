import { useEffect, useRef, useState } from 'react'
import { adminFetch } from './api.js'
import { readResponsePayload } from '../shared.js'
import { editProtectedMailText, protectedMailText } from './protectedMailText.js'

const TYPES = {
  approval: 'Aprobación y gestión del certificado',
  failed: 'Resultado académico: reprobación',
  balance: 'Valores pendientes',
}

const DEFAULT_TEMPLATES = {
  approval: {
    subject: 'Aprobación y gestión del certificado · INTEC',
    body: 'Estimado/a {nombre}:\n\nNos complace informarle que ha aprobado satisfactoriamente {curso}, con una calificación final de {nota}/10.\n\nLe felicitamos por su esfuerzo, dedicación y compromiso durante su formación.\n\nSu certificado de aprobación se encuentra en proceso de gestión. Una vez emitido, será enviado al correo electrónico que tiene registrado en nuestra institución.\n\n{estado_pagos}\n\nAtentamente,\nEducación Continua · INTEC',
  },
  failed: {
    subject: 'Resultado académico: reprobación · INTEC',
    body: 'Estimado/a {nombre}:\n\nLe informamos que su calificación final registrada es {nota}/10.\n\nDe acuerdo con la evaluación académica, no alcanzó el requisito mínimo de aprobación. Por este motivo, su resultado en {curso} es reprobado y no es posible emitir el certificado de aprobación.\n\nSi desea solicitar una revisión de su resultado, resolver dudas o conocer las opciones disponibles para continuar con su formación, comuníquese con Secretaría Académica. Nuestro equipo le brindará la orientación necesaria sobre los pasos a seguir.\n\nAtentamente,\nEducación Continua · INTEC',
  },
  balance: {
    subject: 'Valores pendientes · INTEC',
    body: 'Estimado/a {nombre}:\n\nAl revisar su estado de cuenta, hemos identificado valores pendientes de pago.\n\nSaldo pendiente: USD {saldo}.\n\nLe solicitamos comunicarse con el área financiera de INTEC para consultar el detalle de los valores pendientes, verificar su estado de cuenta y recibir orientación para regularizar su situación.\n\nSi ya realizó el pago o envió su comprobante anteriormente, por favor reenvíelo a DeptCobranzas@intec.edu.ec e incluya su nombre completo y número de cédula. Esta información permitirá verificar el pago y actualizar su estado de cuenta.\n\nAgradecemos su atención y colaboración.\n\nAtentamente,\nEducación Continua · INTEC',
  },
}

async function request(path, data) {
  const response = await adminFetch(`/api/auth/admin/${path}`, data ? {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  } : {})
  const payload = await readResponsePayload(response)
  if (!response.ok || !payload?.ok) throw new Error(payload?.message || 'No se pudo completar la solicitud.')
  return payload
}

function ProtectedMessageEditor({ label, value, onChange, disabled, multiline = false, fieldValues = {} }) {
  const [blocked, setBlocked] = useState(false)
  const { text } = protectedMailText(value, fieldValues)
  const props = {
    value: text, disabled,
    onChange: (event) => {
      const next = editProtectedMailText(value, event.target.value, fieldValues)
      setBlocked(next === null)
      if (next === null) event.target.value = text
      else onChange(next)
    },
  }
  return <label className="field"><span>{label}</span>
    {multiline ? <textarea {...props} rows={12} maxLength={12000} /> : <input {...props} maxLength={250} />}
    <small>Los campos automáticos dentro del mensaje están protegidos.</small>
    {blocked && <small role="status">Solo puedes editar la redacción; los datos automáticos no se pueden cambiar ni borrar.</small>}
  </label>
}

export default function AdminCourseMailPanel({ user }) {
  const financial = String(user?.role?.name || '').trim().toUpperCase() === 'FINANCIERO'
  const [kind, setKind] = useState(financial ? 'balance' : 'approval')
  const [cuts, setCuts] = useState([])
  const [cut, setCut] = useState('')
  const [students, setStudents] = useState([])
  const [selected, setSelected] = useState([])
  const [query, setQuery] = useState('')
  const [extra, setExtra] = useState('')
  const [templates, setTemplates] = useState(DEFAULT_TEMPLATES)
  const [preview, setPreview] = useState(null)
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const sending = useRef(false)

  useEffect(() => {
    let active = true
    request('course-cuts/').then((data) => {
      if (active) setCuts(data.cuts || [])
    }).catch((err) => { if (active) setError(err.message) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    let active = true
    if (!cut) return
    const params = new URLSearchParams({ corte_id: cut, kind })
    request(`course-mail/students/?${params}`).then((data) => {
      if (active) setStudents(data.result.students)
    }).catch((err) => { if (active) setError(err.message) })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [cut, kind])

  function changeFilter(nextCut, nextKind) {
    setStudents([])
    setSelected([])
    setPreview(null)
    setResult(null)
    setError('')
    setBusy(Boolean(nextCut))
    setCut(nextCut)
    setKind(nextKind)
  }

  function toggle(id) {
    setPreview(null)
    setResult(null)
    setSelected((previous) => previous.includes(id)
      ? previous.filter((value) => value !== id)
      : previous.length < 25 ? [...previous, id] : previous)
  }

  async function prepare() {
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const edits = Object.fromEntries((preview?.messages || []).map((message) => [message.student_id,
        { subject: message.editable_subject, body: message.editable_body }]))
      const data = await request('course-mail/preview/', {
        corte_id: cut, kind, student_ids: selected, extra, edits, template: templates[kind],
      })
      setPreview(data.result)
    } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }

  function editMessage(studentId, field, value) {
    setPreview((previous) => ({ ...previous, token: null,
      messages: previous.messages.map((message) => message.student_id === studentId
        ? { ...message, [field]: value } : message),
    }))
    setError('')
  }

  function editTemplate(field, value) {
    setTemplates((previous) => ({ ...previous, [kind]: { ...previous[kind], [field]: value } }))
    setPreview(null)
    setResult(null)
    setError('')
  }

  async function send() {
    if (sending.current || !preview?.token) return
    sending.current = true
    const token = preview.token
    setPreview(null)
    setBusy(true)
    setError('')
    try {
      const data = await request('course-mail/send/', { token })
      setResult(data.result)
      setSelected([])
    } catch (err) { setError(`${err.message} Si hubo un corte de conexión, revisa Enviados antes de reintentar.`) }
    finally { setBusy(false); sending.current = false }
  }

  const visible = students.filter((student) => `${student.nombre} ${student.cedula} ${(student.emails || []).join(' ')}`
    .toLocaleLowerCase().includes(query.toLocaleLowerCase()))

  return <section className="course-mail-panel">
    <article className="module-card">
      <div className="module-card-header"><div><h4>Nuevo correo de curso</h4>
        <p>Cada tipo se envía por separado. Selecciona hasta 25 estudiantes por envío.</p>
        <p>Los avisos se envían al correo institucional y al personal registrados. Si solo hay uno válido, se utiliza ese; si ambos coinciden, se envía una sola vez.</p>
        <p>El aviso de aprobación orienta sobre el certificado; su generación se realiza en el apartado Certificado.</p>
      </div></div>
      <div className="admin-form-grid">
        <label className="field"><span>Tipo de correo</span>
          <select value={kind} onChange={(event) => { changeFilter(cut, event.target.value); setExtra('') }} disabled={busy}>
            {Object.entries(TYPES).filter(([id]) => !financial || id === 'balance')
              .map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select></label>
        <section className="course-mail-template full-span" aria-label={`Editar plantilla de ${TYPES[kind]}`}>
          <h4>Editar mensaje: {TYPES[kind]}</h4>
          <p>Edita el mensaje completo en el cuadro de texto. Los campos automáticos están protegidos dentro del mismo mensaje y se completan con los registros del sistema.</p>
          <ProtectedMessageEditor label="Asunto del mensaje" value={templates[kind].subject} disabled={busy}
            onChange={(value) => editTemplate('subject', value)} />
          <ProtectedMessageEditor label="Texto del mensaje" value={templates[kind].body} disabled={busy} multiline
            onChange={(value) => editTemplate('body', value)} />
          <p>Los datos del estudiante, la nota y los valores son de solo lectura. No se pueden cambiar ni eliminar desde este editor.</p>
          <p>Los cambios se conservan por tipo mientras este apartado permanezca abierto.</p>
          <button type="button" className="ghost-button compact-button" disabled={busy}
            onClick={() => { setTemplates((previous) => ({ ...previous, [kind]: DEFAULT_TEMPLATES[kind] })); setPreview(null); setResult(null) }}>
            Restaurar texto original de este tipo
          </button>
        </section>
        <label className="field"><span>Cohorte</span><select value={cut} disabled={busy}
          onChange={(event) => changeFilter(event.target.value, kind)}>
          <option value="">Selecciona una cohorte</option>
          {cuts.map((item) => <option key={item.corte_id} value={item.corte_id}>
            {item.nombre_corte} — {item.materias_label || item.curso_educontinua || item.materia_pensum}
          </option>)}
        </select></label>
        <label className="field"><span>Buscar estudiante</span><input type="search" value={query}
          onChange={(event) => setQuery(event.target.value)} placeholder="Nombre, cédula o correo" /></label>
        <label className="field full-span"><span>Información adicional (opcional)</span>
          <textarea rows={4} maxLength={3000} value={extra} disabled={busy}
            onChange={(event) => { setExtra(event.target.value); setPreview(null) }}
            placeholder="Indicaciones, contacto o pasos para realizar la gestión." /></label>
      </div>
      {error && <p className="status-message error" role="alert">{error}</p>}
      {busy && <p role="status">Procesando…</p>}
      <p>{selected.length} seleccionados. Solo se habilitan estudiantes con correo válido y nota o saldo correspondiente.</p>
      <div className="student-selection-actions">
        <button type="button" className="ghost-button compact-button" disabled={busy || !visible.some((s) => s.eligible)}
          onClick={() => { setSelected(visible.filter((s) => s.eligible).slice(0, 25).map((s) => String(s.corte_estudiante_id))); setPreview(null); setResult(null) }}>Seleccionar hasta 25 visibles</button>
        <button type="button" className="ghost-button compact-button" disabled={busy}
          onClick={() => { setSelected([]); setPreview(null) }}>Limpiar selección</button>
        <button type="button" className="submit-button compact-button" disabled={busy || !selected.length} onClick={prepare}>Revisar correos</button>
      </div>
      <div className="admin-table-wrap"><table className="admin-table"><thead><tr>
        <th>Seleccionar</th><th>Estudiante</th><th>Correos de destino</th><th>Nota</th><th>Saldo USD</th><th>Estado</th>
      </tr></thead><tbody>{visible.map((student) => {
        const id = String(student.corte_estudiante_id)
        return <tr key={id}><td><input type="checkbox" aria-label={`Seleccionar a ${student.nombre}`}
          checked={selected.includes(id)} disabled={busy || !student.eligible || (selected.length >= 25 && !selected.includes(id))}
          onChange={() => toggle(id)} /></td><td>{student.nombre}<br />{student.cedula}</td>
          <td>{student.emails?.join(' · ') || 'Sin correo'}</td><td>{student.nota_final ?? 'Sin nota'}</td>
          <td>{student.saldo_pendiente ?? '—'}</td><td>{student.reason || 'Habilitado'}</td></tr>
      })}{!visible.length && <tr><td colSpan={6}>{cut ? 'No hay estudiantes para mostrar.' : 'Selecciona una cohorte.'}</td></tr>}</tbody></table></div>
    </article>
    {preview && <article className="module-card"><h4>Vista previa · {TYPES[kind]}</h4>
      <p>Edita el asunto y el texto de cada destinatario. Después pulsa Actualizar vista previa para revisar el correo antes de enviarlo. La vista previa tiene una vigencia de 15 minutos.</p>
      {preview.messages.map((message) => <details key={message.student_id} open={preview.messages.length === 1}>
        <summary>{message.name} · {message.emails?.join(' · ')}</summary>
        <ProtectedMessageEditor label={`Asunto para ${message.name}`} value={message.editable_subject}
          disabled={busy} fieldValues={message.field_values}
          onChange={(value) => editMessage(message.student_id, 'editable_subject', value)} />
        <ProtectedMessageEditor label={`Texto del correo para ${message.name}`} value={message.editable_body}
          disabled={busy} multiline fieldValues={message.field_values}
          onChange={(value) => editMessage(message.student_id, 'editable_body', value)} />
        {preview.token && <iframe title={`Vista previa del correo para ${message.name}`} sandbox=""
          className="course-mail-preview"
          srcDoc={message.html.replace('cid:intec-logo.png', preview.logo_url || '')} />}
      </details>)}
      {!preview.token && <p role="status">Hay cambios sin revisar. Actualiza la vista previa para habilitar el envío.</p>}
      <button type="button" className="ghost-button compact-button" disabled={busy} onClick={prepare}>Actualizar vista previa</button>
      <button type="button" className="submit-button compact-button" disabled={busy || !preview.token} onClick={send}>
        Enviar {preview.messages.length} correo(s) de {TYPES[kind].toLowerCase()}
      </button>
    </article>}
    {result && <article className="module-card" aria-live="polite"><h4>Resultado del envío</h4>
      <p>Aceptados por el servicio: {result.accepted}. Sin confirmación: {result.failed}. La aceptación no garantiza la entrega al buzón.</p>
      {result.results.map((item) => <p key={item.student_id} className={`status-message ${item.ok ? 'success' : 'error'}`}>
        {item.name} · {item.emails?.join(' · ')}: {item.status}
      </p>)}
    </article>}
  </section>
}

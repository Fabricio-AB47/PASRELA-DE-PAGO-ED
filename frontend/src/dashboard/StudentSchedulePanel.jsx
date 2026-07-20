import { useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const numberFormatter = new Intl.NumberFormat('es-EC')
const WEEKDAYS = [
  { id: 1, label: 'Lunes' },
  { id: 2, label: 'Martes' },
  { id: 3, label: 'Miércoles' },
  { id: 4, label: 'Jueves' },
  { id: 5, label: 'Viernes' },
]

export default function StudentSchedulePanel() {
  const [dashboard, setDashboard] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const courses = useMemo(() => dashboard?.courses || [], [dashboard?.courses])
  const weeklySchedule = useMemo(() => buildWeeklySchedule(courses), [courses])
  const scheduleRows = useMemo(() => buildScheduleRows(weeklySchedule), [weeklySchedule])
  const schedulePeriod = useMemo(() => buildSchedulePeriod(courses), [courses])

  useEffect(() => {
    let isMounted = true

    async function loadSchedule() {
      setIsLoading(true)
      setError('')

      try {
        const response = await adminFetch('/api/auth/student/schedule/')
        const payload = await readResponsePayload(response)
        if (!payload || !response.ok || !payload.ok || !payload.dashboard) {
          throw new Error(payload?.message ?? `No fue posible cargar el horario (${response.status}).`)
        }

        if (!isMounted) {
          return
        }

        setDashboard(payload.dashboard)
      } catch (loadError) {
        if (isMounted) {
          setError(loadError.message)
        }
      } finally {
        if (isMounted) {
          setIsLoading(false)
        }
      }
    }

    loadSchedule()

    return () => {
      isMounted = false
    }
  }, [])

  if (isLoading) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>Cargando horario</h3>
          <p>Estamos consultando tus materias matriculadas.</p>
        </div>
      </article>
    )
  }

  if (error) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>No se pudo cargar el horario</h3>
          <p>{error}</p>
        </div>
      </article>
    )
  }

  const metrics = dashboard?.metrics || {}

  return (
    <section className="teacher-panel" aria-labelledby="student-schedule-title">
      <div className="admin-section-heading">
        <div>
          <span className="eyebrow">Estudiante</span>
          <h3 id="student-schedule-title">Horario</h3>
        </div>
      </div>

      <section className="summary-grid teacher-summary-grid" aria-label="Indicadores de horario estudiantil">
        <article className="summary-card">
          <span>Cursos</span>
          <strong>{formatNumber(metrics.cursos)}</strong>
        </article>
        <article className="summary-card">
          <span>Horarios</span>
          <strong>{formatNumber(metrics.horarios)}</strong>
        </article>
        <article className="summary-card">
          <span>Sesiones</span>
          <strong>{formatNumber(metrics.sesiones)}</strong>
        </article>
        <article className="summary-card">
          <span>Virtuales</span>
          <strong>{formatNumber(metrics.virtuales)}</strong>
        </article>
      </section>

      <article className="module-card teacher-panel-card">
        {courses.length ? (
          <div className="student-weekly-schedule">
            <div className="module-card-header student-weekly-schedule-header">
              <div>
                <h4>Horario semanal</h4>
                <p>Materias organizadas de lunes a viernes, sin horarios duplicados.</p>
              </div>
              <div className="student-schedule-period" aria-label="Vigencia general del horario">
                <div>
                  <span>Fecha de inicio</span>
                  <strong>{formatDate(schedulePeriod.start)}</strong>
                </div>
                <div>
                  <span>Fecha final</span>
                  <strong>{formatDate(schedulePeriod.end)}</strong>
                </div>
              </div>
            </div>

            <div className="student-weekly-table-wrap">
              <table className="student-weekly-table">
                <colgroup>
                  <col className="student-weekly-time-column" />
                  {WEEKDAYS.map((day) => <col key={day.id} />)}
                </colgroup>
                <thead>
                  <tr>
                    <th>Horario</th>
                    {WEEKDAYS.map((day) => <th key={day.id}>{day.label}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {scheduleRows.length ? scheduleRows.map((row) => (
                    <tr key={row.key}>
                      <th className="student-weekly-time" scope="row">
                        <strong>{row.hora_inicio || '-'}</strong>
                        <span>a {row.hora_fin || '-'}</span>
                      </th>
                      {WEEKDAYS.map((day) => (
                        <td key={day.id}>
                          <div className="student-weekly-day">
                            {row.days[day.id].length ? row.days[day.id].map((item) => (
                              <article key={item.key} className="student-schedule-subject">
                                <strong>{item.materia}</strong>
                                <span>{item.nombre_corte || 'Sin cohorte'}</span>
                                <span>Docente: {item.docente || 'Sin asignar'}</span>
                                <span>{[item.modalidad, item.aula].filter(Boolean).join(' · ') || 'Modalidad pendiente'}</span>
                                {item.enlace_virtual ? (
                                  <a href={item.enlace_virtual} target="_blank" rel="noreferrer">Abrir clase</a>
                                ) : null}
                              </article>
                            )) : <span className="student-weekly-empty">—</span>}
                          </div>
                        </td>
                      ))}
                    </tr>
                  )) : (
                    <tr>
                      <td className="student-weekly-empty-row" colSpan={WEEKDAYS.length + 1}>
                        No existen horarios de lunes a viernes registrados.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <p className="teacher-panel-empty">No tienes materias matriculadas con horario disponible.</p>
        )}
      </article>
    </section>
  )
}

function formatNumber(value) {
  return numberFormatter.format(Number(value || 0))
}

function buildWeeklySchedule(courses) {
  const schedule = Object.fromEntries(WEEKDAYS.map((day) => [day.id, []]))
  const seen = new Set()

  courses.forEach((course) => {
    ;(course.schedules || []).forEach((item) => {
      const day = Number(item.dia_semana)
      if (!schedule[day]) return

      const key = [
        course.corte_id,
        course.materia,
        day,
        item.hora_inicio,
        item.hora_fin,
        item.modalidad,
        item.aula,
      ].map((value) => String(value || '').trim().toLowerCase()).join('|')
      if (seen.has(key)) return
      seen.add(key)

      schedule[day].push({
        key,
        materia: course.materia,
        nombre_corte: course.nombre_corte || course.codigo_periodo,
        docente: course.docente,
        hora_inicio: item.hora_inicio,
        hora_fin: item.hora_fin,
        modalidad: item.modalidad,
        aula: item.aula,
        enlace_virtual: item.enlace_virtual,
      })
    })
  })

  Object.values(schedule).forEach((items) => {
    items.sort((left, right) => (
      String(left.hora_inicio || '').localeCompare(String(right.hora_inicio || ''))
      || String(left.materia || '').localeCompare(String(right.materia || ''))
    ))
  })
  return schedule
}

function buildSchedulePeriod(courses) {
  const starts = courses.flatMap((course) => {
    const sessionStarts = (course.schedules || []).map((item) => item.primera_sesion).filter(Boolean)
    return sessionStarts.length ? sessionStarts : [course.fecha_inicio].filter(Boolean)
  }).sort()
  const ends = courses.flatMap((course) => {
    const sessionEnds = (course.schedules || []).map((item) => item.ultima_sesion).filter(Boolean)
    return sessionEnds.length ? sessionEnds : [course.fecha_fin].filter(Boolean)
  }).sort()
  return { start: starts[0] || '', end: ends.at(-1) || '' }
}

function buildScheduleRows(weeklySchedule) {
  const rows = new Map()
  WEEKDAYS.forEach((day) => {
    ;(weeklySchedule[day.id] || []).forEach((item) => {
      const key = `${item.hora_inicio || ''}|${item.hora_fin || ''}`
      if (!rows.has(key)) {
        rows.set(key, {
          key,
          hora_inicio: item.hora_inicio,
          hora_fin: item.hora_fin,
          days: Object.fromEntries(WEEKDAYS.map((weekday) => [weekday.id, []])),
        })
      }
      rows.get(key).days[day.id].push(item)
    })
  })
  return Array.from(rows.values()).sort((left, right) => (
    String(left.hora_inicio || '').localeCompare(String(right.hora_inicio || ''))
    || String(left.hora_fin || '').localeCompare(String(right.hora_fin || ''))
  ))
}

function formatDate(value) {
  if (!value) return 'fecha pendiente'
  const normalized = String(value).slice(0, 10)
  const [year, month, day] = normalized.split('-')
  return year && month && day ? `${day}/${month}/${year}` : value
}

import { useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const DEFAULT_PARALLEL = 'A'
const DEFAULT_JOURNEY_CODE = '1'
const numberFormatter = new Intl.NumberFormat('es-EC')

const normalizeCode = (value) => String(value ?? '').trim()
const normalizeParallel = (value) => normalizeCode(value).toUpperCase()

function parallelCatalogKey(values) {
  return [
    normalizeCode(values?.cod_anio_basica),
    normalizeCode(values?.codigo_materia),
    normalizeCode(values?.codigo_periodo),
  ].join('|')
}

function parallelSubjectCatalogKey(values) {
  return [
    normalizeCode(values?.cod_anio_basica),
    normalizeCode(values?.codigo_materia),
    '*',
  ].join('|')
}

function parallelOptionValue(option) {
  return normalizeParallel(option?.paralelo)
}

function getParallelOptions(catalogs, values) {
  const exactKey = parallelCatalogKey(values)
  const subjectKey = parallelSubjectCatalogKey(values)
  const exactOptions = catalogs?.paralelos_por_materia?.[exactKey] || []
  const subjectOptions = catalogs?.paralelos_por_materia?.[subjectKey] || []
  const globalOptions = catalogs?.paralelos || []
  const merged = []
  const seen = new Set()

  for (const option of [...exactOptions, ...subjectOptions, ...globalOptions]) {
    const key = parallelOptionValue(option)
    if (!key || seen.has(key)) {
      continue
    }
    seen.add(key)
    merged.push(option)
  }

  return merged
}

function resolveParallelSelection(catalogs, values, preferred = {}) {
  const options = getParallelOptions(catalogs, values)
  if (!options.length) {
    return {
      paralelo: normalizeParallel(preferred.paralelo) || DEFAULT_PARALLEL,
      cod_jornada: normalizeCode(preferred.cod_jornada) || DEFAULT_JOURNEY_CODE,
    }
  }

  const preferredParallel = normalizeParallel(preferred.paralelo)
  const selectedOption = (
    options.find((option) => normalizeParallel(option.paralelo) === preferredParallel) ||
    options[0]
  )

  return {
    paralelo: normalizeParallel(selectedOption.paralelo) || DEFAULT_PARALLEL,
    cod_jornada: normalizeCode(preferred.cod_jornada) || DEFAULT_JOURNEY_CODE,
  }
}

function getJourneyOptions(catalogs) {
  return catalogs?.jornadas?.length
    ? catalogs.jornadas
    : [{ codigo_jornada: DEFAULT_JOURNEY_CODE, jornada: 'Matutino', cod_modalidad: '' }]
}

function formatNumber(value) {
  return numberFormatter.format(Number(value || 0))
}

function cutLabel(cut) {
  const name = cut.nombre_corte || `Cohorte ${cut.numero_corte || cut.corte_id}`
  const subject = cut.materias_label || cut.materia_pensum || cut.curso_educontinua || ''
  return subject ? `${name} - ${subject}` : name
}

function cutSubjectLabel(cut) {
  return cut?.materias_label || cut?.materia_pensum || cut?.curso_educontinua || cut?.cod_curso || ''
}

function cutCareerLabel(cut) {
  return cut?.carrera || cut?.tipo_oferta || ''
}

function cutPeriodLabel(cut) {
  return cut?.periodo || cut?.codigo_periodo || cut?.nombre_corte || ''
}

function chooseInitialCut(cuts) {
  const loadedCuts = cuts || []
  return (
    loadedCuts.find((cut) => Number(cut.total_estudiantes || 0) > 0 && normalizeCode(cut.estado_corte).toUpperCase() === 'ABIERTO') ||
    loadedCuts.find((cut) => Number(cut.total_estudiantes || 0) > 0) ||
    loadedCuts.find((cut) => normalizeCode(cut.estado_corte).toUpperCase() === 'ABIERTO') ||
    loadedCuts[0] ||
    null
  )
}

function buildFormFromCut(cut, catalogs, current = {}) {
  if (!cut) {
    return {
      ...current,
      corte_id: '',
    }
  }

  const codAnio = normalizeCode(cut.cod_anio_basica)
  const codigoMateria = normalizeCode(cut.codigo_materia || cut.cod_curso)
  const codigoPeriodo = normalizeCode(cut.codigo_periodo)
  const selectedCareer = (catalogs?.carreras || []).find(
    (career) => normalizeCode(career.cod_anio_basica) === codAnio,
  )
  const nextValues = {
    cod_anio_basica: codAnio,
    codigo_materia: codigoMateria,
    codigo_periodo: codigoPeriodo,
  }
  const nextParallel = resolveParallelSelection(catalogs, nextValues, current)

  return {
    ...current,
    corte_id: normalizeCode(cut.corte_id),
    carrera_num: selectedCareer?.num ? String(selectedCareer.num) : '',
    cod_anio_basica: codAnio,
    codigo_materia: codigoMateria,
    codigo_periodo: codigoPeriodo,
    estado_periodo: normalizeCode(cut.estado_corte),
    nombre_materia: cutSubjectLabel(cut),
    paralelo: nextParallel.paralelo,
    cod_jornada: nextParallel.cod_jornada,
  }
}

function studentSyncStatus(student) {
  if (student.continuing_education?.synced) {
    return student.continuing_education.estado || 'Matriculado'
  }
  if (!student.activo) {
    return 'Inactivo'
  }
  return 'Pendiente'
}

const initialForm = {
  corte_id: '',
  codigo_doc: '',
  carrera_num: '',
  cod_anio_basica: '',
  codigo_materia: '',
  codigo_periodo: '',
  estado_periodo: '',
  nombre_materia: '',
  paralelo: DEFAULT_PARALLEL,
  cod_jornada: DEFAULT_JOURNEY_CODE,
}

export default function AdminTeacherEnrollmentPanel() {
  const [catalogs, setCatalogs] = useState({
    carreras: [],
    periodos: [],
    cursos_por_carrera: {},
    paralelos_por_materia: {},
    paralelos: [],
    jornadas: [],
  })
  const [courseCuts, setCourseCuts] = useState([])
  const [selectedCutId, setSelectedCutId] = useState('')
  const [studentPreview, setStudentPreview] = useState(null)
  const [form, setForm] = useState(initialForm)
  const [teacherSearch, setTeacherSearch] = useState('')
  const [teacherCandidates, setTeacherCandidates] = useState([])
  const [selectedTeacher, setSelectedTeacher] = useState(null)
  const [isLoadingCatalogs, setIsLoadingCatalogs] = useState(true)
  const [isLoadingCuts, setIsLoadingCuts] = useState(true)
  const [isLoadingStudents, setIsLoadingStudents] = useState(false)
  const [isLoadingTeachers, setIsLoadingTeachers] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [studentsError, setStudentsError] = useState(null)
  const [message, setMessage] = useState('')
  const [result, setResult] = useState(null)

  useEffect(() => {
    let isMounted = true

    async function loadCatalogs() {
      try {
        const response = await adminFetch('/api/auth/inscription/catalogs/')
        const payload = await readResponsePayload(response)

        if (!payload || !response.ok || !payload.ok || !payload.catalogs) {
          throw new Error(payload?.message ?? `No fue posible cargar catálogos (${response.status}).`)
        }

        if (!isMounted) {
          return
        }

        setCatalogs(payload.catalogs)
      } catch (loadError) {
        if (isMounted) {
          setError(loadError.message)
        }
      } finally {
        if (isMounted) {
          setIsLoadingCatalogs(false)
        }
      }
    }

    loadCatalogs()

    return () => {
      isMounted = false
    }
  }, [])

  useEffect(() => {
    let isMounted = true

    async function loadCourseCuts() {
      setIsLoadingCuts(true)

      try {
        const response = await adminFetch('/api/auth/admin/course-cuts/')
        const payload = await readResponsePayload(response)
        if (!payload || !response.ok || !payload.ok) {
          throw new Error(payload?.message ?? `No fue posible cargar cohortes (${response.status}).`)
        }

        if (isMounted) {
          const loadedCuts = payload.cuts || []
          const initialCut = chooseInitialCut(loadedCuts)
          setCourseCuts(loadedCuts)
          setSelectedCutId(initialCut?.corte_id || '')
          setForm((current) => buildFormFromCut(initialCut, null, current))
        }
      } catch (loadError) {
        if (isMounted) {
          setError(loadError.message)
        }
      } finally {
        if (isMounted) {
          setIsLoadingCuts(false)
        }
      }
    }

    loadCourseCuts()

    return () => {
      isMounted = false
    }
  }, [])

  function handleChange(event) {
    const { name, value } = event.target
    setResult(null)
    setMessage('')
    setForm((current) => ({
      ...current,
      [name]: value,
    }))
  }

  function handleCutChange(event) {
    const nextCutId = event.target.value
    const nextCut = courseCuts.find((cut) => String(cut.corte_id) === String(nextCutId)) || null
    setSelectedCutId(nextCutId)
    setStudentPreview(null)
    setStudentsError(null)
    setResult(null)
    setMessage('')
    setForm((current) => buildFormFromCut(nextCut, catalogs, current))
  }

  function handleParallelChange(event) {
    const selectedValue = event.target.value
    const selectedOption = parallelOptions.find((option) => parallelOptionValue(option) === selectedValue)
    if (!selectedOption) {
      return
    }

    setResult(null)
    setMessage('')
    setForm((current) => ({
      ...current,
      paralelo: normalizeParallel(selectedOption.paralelo) || DEFAULT_PARALLEL,
    }))
  }

  async function loadTeacherCandidates() {
    setIsLoadingTeachers(true)
    setError('')
    setMessage('')

    try {
      const params = new URLSearchParams({
        q: teacherSearch,
        limit: '100',
      })
      const response = await adminFetch(`/api/auth/admin/teachers/?${params.toString()}`)
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible cargar docentes (${response.status}).`)
      }

      const loadedTeachers = payload.teachers || []
      setTeacherCandidates(loadedTeachers)
      setMessage(`${loadedTeachers.length} docente(s) encontrado(s).`)
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setIsLoadingTeachers(false)
    }
  }

  function selectTeacher(teacher) {
    setSelectedTeacher(teacher)
    setResult(null)
    setMessage(`Docente seleccionado: ${teacher.nombre}`)
    setForm((current) => ({
      ...current,
      codigo_doc: String(teacher.codigo_doc || ''),
    }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setIsSubmitting(true)
    setError('')
    setMessage('')
    setResult(null)

    if (!selectedTeacher?.codigo_doc && !form.codigo_doc) {
      setError('Busca y selecciona un docente antes de matricular.')
      setIsSubmitting(false)
      return
    }
    if (!selectedCut?.corte_id) {
      setError('Selecciona la cohorte donde están los estudiantes antes de matricular al docente.')
      setIsSubmitting(false)
      return
    }

    try {
      const response = await adminFetch('/api/auth/admin/teacher-enrollment/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ...form,
          corte_id: selectedCut.corte_id,
          codigo_doc: selectedTeacher?.codigo_doc || form.codigo_doc,
          cedula: selectedTeacher?.cedula || '',
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible matricular al docente (${response.status}).`)
      }

      setResult(payload.result)
      setMessage(payload.message || 'Matrícula docente procesada.')
    } catch (submitError) {
      setError(submitError.message)
    } finally {
      setIsSubmitting(false)
    }
  }

  const selectedCut = courseCuts.find((cut) => String(cut.corte_id) === String(selectedCutId)) || null
  const selectedSubjectName = cutSubjectLabel(selectedCut) || form.nombre_materia
  const selectedCareerName = cutCareerLabel(selectedCut)
  const selectedPeriodName = cutPeriodLabel(selectedCut)
  const catalogParallelOptions = getParallelOptions(catalogs, form)
  const journeyOptions = getJourneyOptions(catalogs)
  const fallbackParallelOptions = [
    {
      paralelo: form.paralelo || DEFAULT_PARALLEL,
      jornada: '',
      total_estudiantes: '',
    },
  ]
  const parallelOptions = catalogParallelOptions.length ? catalogParallelOptions : fallbackParallelOptions
  const selectedParallelValue = parallelOptionValue({
    paralelo: form.paralelo,
    cod_jornada: form.cod_jornada,
  })
  const selectedCutPreview = studentPreview?.corte_id === selectedCutId ? studentPreview.result : null
  const assignedStudents = useMemo(() => selectedCutPreview?.students || [], [selectedCutPreview])
  const visibleAssignedStudents = useMemo(() => {
    const selectedParallel = normalizeParallel(form.paralelo)
    const hasParallelData = assignedStudents.some((student) => normalizeParallel(student.paralelo))

    if (!selectedParallel || !hasParallelData) {
      return assignedStudents
    }

    return assignedStudents.filter((student) => {
      const studentParallel = normalizeParallel(student.paralelo)
      return studentParallel === selectedParallel
    })
  }, [assignedStudents, form.paralelo])
  const currentStudentsError = studentsError?.corte_id === selectedCutId ? studentsError.message : ''
  const canSubmit = (
    !isSubmitting &&
    !isLoadingCatalogs &&
    !isLoadingCuts &&
    Boolean(selectedCut?.corte_id) &&
    Boolean(selectedTeacher?.codigo_doc || form.codigo_doc)
  )

  useEffect(() => {
    let isMounted = true

    if (!selectedCutId) {
      return () => {
        isMounted = false
      }
    }

    async function loadAssignedStudents() {
      setIsLoadingStudents(true)
      setStudentsError(null)

      try {
        const params = new URLSearchParams({ corte_id: selectedCutId })
        const response = await adminFetch(`/api/auth/admin/course-cuts/students/?${params.toString()}`)
        const payload = await readResponsePayload(response)
        if (!payload || !response.ok || !payload.ok || !payload.result) {
          throw new Error(payload?.message ?? `No fue posible cargar estudiantes (${response.status}).`)
        }

        if (isMounted) {
          setStudentPreview({
            corte_id: selectedCutId,
            result: payload.result,
          })
        }
      } catch (loadError) {
        if (isMounted) {
          setStudentsError({
            corte_id: selectedCutId,
            message: loadError.message,
          })
        }
      } finally {
        if (isMounted) {
          setIsLoadingStudents(false)
        }
      }
    }

    loadAssignedStudents()

    return () => {
      isMounted = false
    }
  }, [selectedCutId])

  return (
    <section id="admin-teacher-enrollment" className="admin-bulk-enrollment">
      <div className="admin-section-heading">
        <div>
          <h3>Matrícula docente</h3>
          <p>Busca un docente ingresado por nombre o cédula y asígnalo a una materia.</p>
        </div>
      </div>

      <form className="auth-form bulk-enrollment-form" onSubmit={handleSubmit}>
        <section className="bulk-selection-panel">
          <div className="bulk-selection-header">
            <div>
              <h4>Buscar docente</h4>
              <p>Consulta registros existentes en DATOSDOCENTE por nombre o número de cédula.</p>
            </div>
            <strong>{selectedTeacher ? `Seleccionado: ${selectedTeacher.codigo_doc}` : 'Sin selección'}</strong>
          </div>

          <div className="student-selection-toolbar">
            <label className="field">
              <span>Nombre o cédula</span>
              <input
                type="search"
                value={teacherSearch}
                onChange={(event) => setTeacherSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    loadTeacherCandidates()
                  }
                }}
                placeholder="Ej. María Pérez o 0999999999"
              />
            </label>
            <div className="student-selection-actions">
              <button
                type="button"
                className="ghost-button compact-button"
                onClick={loadTeacherCandidates}
                disabled={isLoadingTeachers}
              >
                {isLoadingTeachers ? 'Buscando...' : 'Buscar docentes'}
              </button>
              <button
                type="button"
                className="ghost-button compact-button"
                onClick={() => {
                  setSelectedTeacher(null)
                  setForm((current) => ({ ...current, codigo_doc: '' }))
                }}
                disabled={!selectedTeacher}
              >
                Limpiar selección
              </button>
            </div>
          </div>

          <div className="admin-table-wrap student-selection-table">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Seleccionar</th>
                  <th>Docente</th>
                  <th>Cédula</th>
                  <th>Correo INTEC</th>
                  <th>Credenciales</th>
                </tr>
              </thead>
              <tbody>
                {teacherCandidates.map((teacher) => {
                  const teacherId = String(teacher.codigo_doc || '')
                  return (
                    <tr key={teacherId}>
                      <td>
                        <label className="student-selector-cell">
                          <input
                            type="radio"
                            name="selected_teacher"
                            checked={String(selectedTeacher?.codigo_doc || '') === teacherId}
                            onChange={() => selectTeacher(teacher)}
                          />
                          <span>{teacher.codigo_doc}</span>
                        </label>
                      </td>
                      <td>{teacher.nombre}</td>
                      <td>{teacher.cedula}</td>
                      <td>{teacher.correo_intec || teacher.login || '-'}</td>
                      <td>{teacher.tiene_credenciales ? 'Sí' : 'No'}</td>
                    </tr>
                  )
                })}
                {!teacherCandidates.length ? (
                  <tr>
                    <td colSpan="5" className="student-selection-empty">
                      Busca por nombre o cédula para seleccionar un docente existente.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>

        <div className="registration-grid registration-course-grid">
          <label className="field">
            <span>Cohorte *</span>
            <select
              name="corte_id"
              value={selectedCutId}
              onChange={handleCutChange}
              disabled={isLoadingCuts || !courseCuts.length}
              required
            >
              <option value="">Selecciona cohorte</option>
              {courseCuts.map((cut) => (
                <option key={cut.corte_id} value={cut.corte_id}>
                  {cutLabel(cut)}
                  {cut.total_estudiantes ? ` - ${formatNumber(cut.total_estudiantes)} estudiante(s)` : ''}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Carrera u oferta</span>
            <input value={selectedCareerName || 'Sin carrera'} readOnly />
          </label>

          <label className="field">
            <span>Materia o curso</span>
            <input value={selectedSubjectName || 'Sin materia'} readOnly />
          </label>

          <label className="field">
            <span>Período o cohorte</span>
            <input value={selectedPeriodName || 'Sin período'} readOnly />
          </label>
        </div>

        <div className="registration-grid registration-grid-4">
          <label className="field">
            <span>Paralelo *</span>
            <select
              name="parallel_option"
              value={selectedParallelValue}
              onChange={handleParallelChange}
              disabled={isLoadingCatalogs || !parallelOptions.length}
              required
            >
              {parallelOptions.map((option) => (
                <option key={parallelOptionValue(option)} value={parallelOptionValue(option)}>
                  {normalizeParallel(option.paralelo)}
                  {option.codigo_periodo && normalizeCode(option.codigo_periodo) !== normalizeCode(form.codigo_periodo)
                    ? ` - período ${option.codigo_periodo}`
                    : ''}
                  {option.total_estudiantes ? ` - ${formatNumber(option.total_estudiantes)} estudiante(s)` : ''}
                  {option.total_docentes ? ` - ${formatNumber(option.total_docentes)} docente(s)` : ''}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Código jornada *</span>
            <select
              name="cod_jornada"
              value={form.cod_jornada}
              onChange={handleChange}
              required
            >
              {journeyOptions.map((journey) => (
                <option key={journey.codigo_jornada} value={journey.codigo_jornada}>
                  {journey.codigo_jornada} - {journey.jornada}
                </option>
              ))}
            </select>
          </label>
        </div>

        {selectedCut ? (
          <p className="payment-result-note">
            Se asignará la cohorte {cutLabel(selectedCut)}
            {selectedTeacher ? ` a ${selectedTeacher.nombre}.` : '.'}
          </p>
        ) : null}

        <section className="bulk-selection-panel teacher-student-preview">
          <div className="bulk-selection-header">
            <div>
              <h4>Estudiantes a cargo</h4>
              <p>
                {selectedCut
                  ? `${cutLabel(selectedCut)} · Paralelo ${form.paralelo || '-'} · ${selectedCut.estado_corte || 'Sin estado'}`
                  : 'Selecciona una materia con cohorte registrada para ver los estudiantes.'}
              </p>
            </div>
            <strong>{selectedCut ? `${formatNumber(visibleAssignedStudents.length)} estudiante(s)` : 'Sin cohorte'}</strong>
          </div>

          {isLoadingCuts || (selectedCut && isLoadingStudents) ? (
            <p className="student-selection-empty">Cargando estudiantes de la cohorte...</p>
          ) : currentStudentsError ? (
            <p className="form-error">{currentStudentsError}</p>
          ) : selectedCut ? (
            <div className="admin-table-wrap student-selection-table">
              <table className="admin-table course-cut-table">
                <thead>
                  <tr>
                    <th>Estudiante</th>
                    <th>Cédula</th>
                    <th>Paralelo</th>
                    <th>Matrícula</th>
                    <th>Estado</th>
                    <th>Educación continua</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleAssignedStudents.map((student) => (
                    <tr key={student.corte_estudiante_id || student.codigo_estud}>
                      <td>
                        <strong>{student.nombre}</strong>
                        <span>Código {student.codigo_estud || '-'}</span>
                      </td>
                      <td>{student.cedula || '-'}</td>
                      <td>
                        <strong>{student.paralelo || form.paralelo || '-'}</strong>
                        <span>{student.jornada || (student.cod_jornada ? `Grupo ${student.cod_jornada}` : '')}</span>
                      </td>
                      <td>{student.num_matricula || '-'}</td>
                      <td>{student.activo ? student.estado_participacion || 'Activo' : student.estado_registro || 'Inactivo'}</td>
                      <td>{studentSyncStatus(student)}</td>
                    </tr>
                  ))}
                  {!visibleAssignedStudents.length ? (
                    <tr>
                      <td colSpan="6" className="student-selection-empty">
                        No hay estudiantes registrados para el paralelo seleccionado.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="student-selection-empty">
              No se encontró una cohorte registrada para la carrera, materia y período seleccionados.
            </p>
          )}
        </section>

        {error ? <p className="form-error">{error}</p> : null}
        {message ? <p className="form-success">{message}</p> : null}

        <button className="submit-button" type="submit" disabled={!canSubmit}>
          {isSubmitting ? 'Procesando matrícula docente...' : 'Matricular docente'}
        </button>
      </form>

      {result ? (
        <section className="bulk-results">
          <div className="bulk-result-summary">
            <div>
              <span>Docente</span>
              <strong>{result.teacher?.nombre || '-'}</strong>
            </div>
            <div>
              <span>Cédula</span>
              <strong>{result.teacher?.cedula || '-'}</strong>
            </div>
            <div>
              <span>Materia</span>
              <strong>{result.assignment?.materia || result.assignment?.codigo_materia || '-'}</strong>
            </div>
            <div>
              <span>Paralelo</span>
              <strong>{result.assignment?.paralelo || '-'}</strong>
            </div>
          </div>

          <pre className="json-result">{JSON.stringify(result, null, 2)}</pre>
        </section>
      ) : null}
    </section>
  )
}

import { useEffect, useMemo, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'

const numberFormatter = new Intl.NumberFormat('es-EC')
const MODALITY_OPTIONS = ['EN LÍNEA', 'PRESENCIAL']
const emptyScheduleForm = {
  horario_id: '',
  dia_semana: '1',
  hora_inicio: '18:00',
  hora_fin: '20:00',
  modalidad: 'EN LÍNEA',
  aula: '',
  enlace_virtual: '',
  fecha_desde: '',
  fecha_hasta: '',
  generar_sesiones: true,
  fechas_clase: [],
}
const emptyModuleForm = {
  modulo_id: '',
  nombre_modulo: '',
  tema_modulo: '',
  fecha_finalizacion: '',
  actividades_finales: '',
  docente_corte_ids: [],
}
const WEEKDAY_SHORT_LABELS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
const monthFormatter = new Intl.DateTimeFormat('es-EC', { month: 'long', year: 'numeric' })

export default function AdminSchedulePanel() {
  const [cuts, setCuts] = useState([])
  const [selectedCutId, setSelectedCutId] = useState('')
  const [selectedCourseTeacherIds, setSelectedCourseTeacherIds] = useState([])
  const [selectedModuleId, setSelectedModuleId] = useState('')
  const [selectedModuleIds, setSelectedModuleIds] = useState([])
  const [moduleTeacherSelections, setModuleTeacherSelections] = useState({})
  const [moduleDateRanges, setModuleDateRanges] = useState({})
  const [editingModuleId, setEditingModuleId] = useState('')
  const [moduleForm, setModuleForm] = useState(emptyModuleForm)
  const [scheduleData, setScheduleData] = useState(null)
  const [form, setForm] = useState(emptyScheduleForm)
  const [calendarMonth, setCalendarMonth] = useState(() => toMonthKey(new Date()))
  const [isLoading, setIsLoading] = useState(true)
  const [isScheduleLoading, setIsScheduleLoading] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isModuleSaving, setIsModuleSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState(null)
  const selectedCut = useMemo(
    () => cuts.find((cut) => String(cut.corte_id) === String(selectedCutId)) || null,
    [cuts, selectedCutId],
  )
  const teachers = useMemo(() => scheduleData?.teachers || [], [scheduleData?.teachers])
  const modules = useMemo(() => scheduleData?.modules || [], [scheduleData?.modules])
  const selectedModule = useMemo(
    () => modules.find((module) => String(module.modulo_id) === String(selectedModuleId)) || null,
    [modules, selectedModuleId],
  )
  const selectedModules = useMemo(() => {
    const selected = new Set(selectedModuleIds.map(String))
    return modules
      .filter((module) => selected.has(String(module.modulo_id)))
      .map((module) => {
        const range = moduleDateRanges[String(module.modulo_id)] || {}
        return {
          ...module,
          fecha_inicio: range.fecha_inicio || module.fecha_inicio,
          fecha_fin: range.fecha_fin || module.fecha_fin,
        }
      })
  }, [modules, selectedModuleIds, moduleDateRanges])
  const moduleTeachers = teachers
  const selectedDateSet = useMemo(() => new Set(form.fechas_clase || []), [form.fechas_clase])
  const calendarDays = useMemo(() => buildCalendarDays(calendarMonth), [calendarMonth])

  useEffect(() => {
    let isMounted = true

    async function loadCuts() {
      setIsLoading(true)
      setError('')

      try {
        const response = await adminFetch('/api/auth/admin/course-cuts/')
        const payload = await readResponsePayload(response)
        if (!payload || !response.ok || !payload.ok) {
          throw new Error(payload?.message ?? `No fue posible cargar cohortes (${response.status}).`)
        }

        if (!isMounted) {
          return
        }

        const loadedCuts = payload.cuts || []
        const firstCut = loadedCuts[0] || null
        setCuts(loadedCuts)
        setSelectedCutId(firstCut?.corte_id || '')
        setForm(buildScheduleForm(firstCut, null))
        setCalendarMonth(resolveCalendarMonth(firstCut, []))
        if (firstCut) {
          await loadScheduleData(firstCut)
        } else {
          setScheduleData(null)
        }
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

    loadCuts()

    return () => {
      isMounted = false
    }
  // La carga inicial se ejecuta una sola vez; las recargas posteriores son explícitas por cohorte.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function loadScheduleData(cut, { resetForm = true } = {}) {
    if (!cut?.corte_id) {
      return null
    }

    setIsScheduleLoading(true)
    setError('')

    try {
      const params = new URLSearchParams({ corte_id: cut.corte_id })
      const response = await adminFetch(`/api/auth/admin/course-cuts/schedule/?${params.toString()}`)
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible cargar horario (${response.status}).`)
      }

      const result = payload.result || {}
      setScheduleData(result)
      const nextModule = resolveSelectedModule(result, selectedModuleId)
      const registeredModuleIds = new Set((result.schedules || []).map((schedule) => String(schedule.modulo_id)))
      const pendingModuleIds = (result.modules || [])
        .map((module) => String(module.modulo_id))
        .filter((moduleId) => !registeredModuleIds.has(moduleId))
      const nextTeacherSelections = Object.fromEntries((result.modules || []).map((module) => {
        const moduleSchedule = (result.schedules || []).find((schedule) =>
          String(schedule.modulo_id) === String(module.modulo_id) && schedule.docente_responsable?.docente_corte_id)
        return [
          String(module.modulo_id),
          String(
            moduleSchedule?.docente_responsable?.docente_corte_id
            || module.docente_corte_ids?.[0]
            || result.teachers?.[0]?.docente_corte_id
            || '',
          ),
        ]
      }))
      const nextDateRanges = Object.fromEntries((result.modules || []).map((module) => [
        String(module.modulo_id),
        { fecha_inicio: module.fecha_inicio || '', fecha_fin: module.fecha_fin || '' },
      ]))
      setSelectedModuleId(nextModule?.modulo_id || '')
      setSelectedModuleIds(pendingModuleIds)
      setEditingModuleId('')
      setModuleTeacherSelections(nextTeacherSelections)
      setModuleDateRanges(nextDateRanges)
      setSelectedCourseTeacherIds((result.teachers || []).slice(0, 3).map((teacher) => String(teacher.docente_corte_id)))
      setModuleForm(buildModuleForm(nextModule, result))
      if (resetForm) {
        const nextForm = buildScheduleForm(cut, result)
        const pendingDates = (result.modules || [])
          .filter((module) => pendingModuleIds.includes(String(module.modulo_id)))
          .flatMap((module) => datesBetween(module.fecha_inicio, module.fecha_fin))
        nextForm.fechas_clase = [...new Set(pendingDates)].sort()
        setForm(nextForm)
        setCalendarMonth(nextForm.fechas_clase[0]?.slice(0, 7) || nextModule?.fecha_inicio?.slice(0, 7) || resolveCalendarMonth(cut, []))
        setMessage((result.schedules || []).length
          ? `Se encontraron ${result.schedules.length} horario(s) y ${(result.sessions || []).length} fecha(s) registradas en la base de datos.`
          : 'No existe un horario registrado en la base de datos. Las fechas pendientes quedaron marcadas para realizar el registro.')
      }
      return result
    } catch (loadError) {
      setError(loadError.message)
      return null
    } finally {
      setIsScheduleLoading(false)
    }
  }

  async function handleCutChange(event) {
    const nextCutId = event.target.value
    const nextCut = cuts.find((cut) => String(cut.corte_id) === String(nextCutId)) || null
    setSelectedCutId(nextCutId)
    setSelectedCourseTeacherIds([])
    setSelectedModuleId('')
    setSelectedModuleIds([])
    setModuleTeacherSelections({})
    setModuleDateRanges({})
    setEditingModuleId('')
    setModuleForm(emptyModuleForm)
    setScheduleData(null)
    setForm(buildScheduleForm(nextCut, null))
    setCalendarMonth(resolveCalendarMonth(nextCut, []))
    setMessage('')
    setError('')
    if (nextCut) {
      await loadScheduleData(nextCut)
    }
  }

  function handleChange(event) {
    const { name, value, checked, type } = event.target
    setForm((current) => ({
      ...current,
      [name]: type === 'checkbox' ? checked : value,
    }))
  }

  function handleEditModuleSchedule(module) {
    const moduleId = String(module?.modulo_id || '')
    const moduleSchedules = (scheduleData?.schedules || []).filter(
      (schedule) => String(schedule.modulo_id) === moduleId,
    )
    if (!moduleSchedules.length) return
    const firstSchedule = moduleSchedules[0]
    const scheduleDates = sessionDatesForModule(scheduleData, moduleId)
    const editTeacherId = String(
      firstSchedule?.docente_responsable?.docente_corte_id
      || moduleTeacherSelections[moduleId]
      || resolveSelectedTeacherId(scheduleData, '', module),
    )
    setEditingModuleId(moduleId)
    setSelectedModuleId(moduleId)
    setSelectedModuleIds([moduleId])
    setModuleTeacherSelections((current) => ({ ...current, [moduleId]: editTeacherId }))
    setForm((current) => ({
      ...current,
      horario_id: '',
      hora_inicio: firstSchedule?.hora_inicio || current.hora_inicio,
      hora_fin: firstSchedule?.hora_fin || current.hora_fin,
      modalidad: firstSchedule?.modalidad || current.modalidad,
      aula: firstSchedule?.aula || '',
      enlace_virtual: firstSchedule?.enlace_virtual || '',
      fechas_clase: scheduleDates,
    }))
    setCalendarMonth(resolveCalendarMonth(selectedCut, scheduleDates))
    setError('')
    setMessage(`Editando el horario registrado de ${module.nombre_modulo}. Puedes cambiar fechas, horas, modalidad o docente.`)
  }

  function selectPendingModulesAndDates() {
    const registered = new Set((scheduleData?.schedules || []).map((schedule) => String(schedule.modulo_id)))
    const pendingModules = modules.filter((module) => !registered.has(String(module.modulo_id)))
    const pendingIds = pendingModules.map((module) => String(module.modulo_id))
    const allDates = pendingModules.flatMap((module) => {
      const range = moduleDateRanges[String(module.modulo_id)] || module
      return datesBetween(range.fecha_inicio, range.fecha_fin)
    })
    setEditingModuleId('')
    setSelectedModuleIds(pendingIds)
    setForm((current) => ({ ...current, fechas_clase: [...new Set(allDates)].sort() }))
    if (allDates[0]) setCalendarMonth(allDates[0].slice(0, 7))
    setError(pendingModules.length ? '' : 'Los cuatro módulos ya tienen horarios registrados. Utiliza el apartado de edición.')
    setMessage(pendingModules.length ? 'Se marcaron automáticamente todas las fechas de los módulos pendientes.' : '')
  }

  function selectModule(module) {
    const moduleId = String(module?.modulo_id || '')
    setSelectedModuleId(moduleId)
    setSelectedModuleIds((current) => current.includes(moduleId) ? current : [...current, moduleId])
    setModuleForm(buildModuleForm(module, scheduleData))
    if (module?.fecha_inicio) setCalendarMonth(module.fecha_inicio.slice(0, 7))
    setMessage('')
  }

  function selectResponsibleTeacher(teacherId, moduleId = selectedModuleId) {
    const normalizedTeacherId = String(teacherId || '')
    const normalizedModuleId = String(moduleId || '')
    setModuleTeacherSelections((current) => ({ ...current, [normalizedModuleId]: normalizedTeacherId }))
  }

  function toggleCourseTeacher(teacherId) {
    const normalized = String(teacherId)
    setSelectedCourseTeacherIds((current) => {
      if (current.includes(normalized)) return current.filter((item) => item !== normalized)
      if (current.length >= 3) {
        setError('El curso permite hasta tres docentes principales.')
        return current
      }
      setError('')
      return [...current, normalized]
    })
  }

  function toggleScheduleModule(moduleId) {
    const normalized = String(moduleId)
    const alreadyRegistered = (scheduleData?.schedules || []).some(
      (schedule) => String(schedule.modulo_id) === normalized,
    )
    if (alreadyRegistered && String(editingModuleId) !== normalized) {
      setError('Ese módulo ya tiene un horario registrado. Utiliza el apartado de edición.')
      return
    }
    const nextIds = selectedModuleIds.includes(normalized)
      ? selectedModuleIds.filter((item) => item !== normalized)
      : [...selectedModuleIds, normalized]
    setSelectedModuleIds(nextIds)
    const nextModules = modules.filter((module) => nextIds.includes(String(module.modulo_id)))
    setForm((current) => ({
      ...current,
      fechas_clase: current.fechas_clase.filter((date) => nextModules.some((module) => isDateInsideModule(date, module))),
    }))
    setMessage('')
    setError('')
  }

  function updateModuleDateRange(module, field, value) {
    const moduleId = String(module.modulo_id)
    const currentRange = moduleDateRanges[moduleId] || {
      fecha_inicio: module.fecha_inicio || '',
      fecha_fin: module.fecha_fin || '',
    }
    const nextRange = { ...currentRange, [field]: value }
    setModuleDateRanges((current) => ({ ...current, [moduleId]: nextRange }))

    if (!nextRange.fecha_inicio || !nextRange.fecha_fin || nextRange.fecha_inicio > nextRange.fecha_fin) return
    const rangeDates = datesBetween(nextRange.fecha_inicio, nextRange.fecha_fin)
    if (rangeDates.length > 14) {
      setError('Cada módulo puede abarcar como máximo dos semanas (14 días).')
      return
    }
    setError('')
    setSelectedModuleIds((current) => current.includes(moduleId) ? current : [...current, moduleId])
    setForm((current) => {
      const preservedDates = current.fechas_clase.filter((date) =>
        !isDateInsideModule(date, module)
        && !(date >= currentRange.fecha_inicio && date <= currentRange.fecha_fin))
      const nextDates = [...new Set([...preservedDates, ...rangeDates])].sort()
      return {
        ...current,
        fechas_clase: nextDates,
        dia_semana: nextDates[0] ? String(weekdayFromIsoDate(nextDates[0])) : current.dia_semana,
      }
    })
    setCalendarMonth(nextRange.fecha_inicio.slice(0, 7))
  }

  function toggleModuleTeacher(teacherId) {
    const normalized = String(teacherId)
    if (!moduleForm.docente_corte_ids.includes(normalized) && moduleForm.docente_corte_ids.length >= 3) {
      setError('Puedes seleccionar hasta tres docentes por módulo.')
      return
    }
    setError('')
    setModuleForm((current) => ({
      ...current,
      docente_corte_ids: current.docente_corte_ids.includes(normalized)
        ? current.docente_corte_ids.filter((item) => item !== normalized)
        : [...current.docente_corte_ids, normalized],
    }))
  }

  function selectAllModuleTeachers() {
    const teacherIds = teachers.slice(0, 3).map((teacher) => String(teacher.docente_corte_id))
    setModuleForm((current) => ({ ...current, docente_corte_ids: teacherIds }))
    setError(teachers.length > 3 ? 'Se seleccionaron los primeros tres docentes, que es el máximo permitido.' : '')
  }

  async function applyTeachersToAllModules() {
    if (!selectedCut?.corte_id || !moduleForm.docente_corte_ids.length) {
      setError('Selecciona entre uno y tres docentes para aplicarlos a los cuatro módulos.')
      return
    }
    setIsModuleSaving(true)
    setError('')
    setMessage('')
    try {
      let updated = scheduleData
      for (const module of modules) {
        const response = await adminFetch('/api/auth/admin/course-cuts/modules/save/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            corte_id: selectedCut.corte_id,
            modulo_id: module.modulo_id,
            nombre_modulo: module.nombre_modulo,
            tema_modulo: module.tema_modulo || '',
            fecha_finalizacion: module.fecha_finalizacion || module.fecha_fin || '',
            actividades_finales: module.actividades_finales || '',
            descripcion: module.descripcion || '',
            docente_corte_ids: moduleForm.docente_corte_ids,
          }),
        })
        const payload = await readResponsePayload(response)
        if (!payload?.ok || !response.ok) {
          throw new Error(`${module.nombre_modulo}: ${payload?.message ?? `no fue posible asignar docentes (${response.status})`}`)
        }
        updated = payload.result || updated
      }
      const teacherId = String(moduleForm.docente_corte_ids[0] || '')
      setScheduleData(updated)
      setSelectedModuleIds(modules.map((module) => String(module.modulo_id)))
      setModuleTeacherSelections(Object.fromEntries(modules.map((module) => [String(module.modulo_id), teacherId])))
      const activeModule = (updated.modules || []).find((module) => String(module.modulo_id) === String(selectedModuleId))
      setModuleForm(buildModuleForm(activeModule, updated))
      setMessage('Docentes aplicados correctamente a los cuatro módulos. Ya puedes crear el horario conjunto.')
    } catch (applyError) {
      setError(applyError.message)
    } finally {
      setIsModuleSaving(false)
    }
  }

  async function saveModule() {
    if (!selectedCut?.corte_id) return
    if (!moduleForm.nombre_modulo.trim()) return setError('Ingresa el nombre del módulo.')
    if (!moduleForm.tema_modulo.trim()) return setError('Describe el tema principal del módulo.')
    if (!moduleForm.fecha_finalizacion) return setError('Selecciona la fecha de finalización del módulo.')
    if (!moduleForm.actividades_finales.trim()) return setError('Describe las actividades finales del módulo.')
    if (!moduleForm.docente_corte_ids.length) return setError('Selecciona al menos un docente para el módulo.')
    setIsModuleSaving(true); setError(''); setMessage('')
    try {
      const response = await adminFetch('/api/auth/admin/course-cuts/modules/save/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...moduleForm, corte_id: selectedCut.corte_id }),
      })
      const payload = await readResponsePayload(response)
      if (!payload?.ok || !response.ok) throw new Error(payload?.message ?? `No fue posible guardar el módulo (${response.status}).`)
      const updated = payload.result || {}
      const savedModule = (updated.modules || []).find((item) => String(item.modulo_id) === String(moduleForm.modulo_id))
        || (updated.modules || []).find((item) => item.nombre_modulo === moduleForm.nombre_modulo)
        || (updated.modules || []).at(-1)
      setScheduleData(updated)
      setSelectedModuleId(String(savedModule?.modulo_id || ''))
      setModuleForm(buildModuleForm(savedModule, updated))
      const responsibleTeacherId = resolveSelectedTeacherId(updated, '', savedModule)
      setModuleTeacherSelections((current) => ({
        ...current,
        [String(savedModule?.modulo_id || '')]: responsibleTeacherId,
      }))
      setMessage(payload.message || 'Módulo y docentes guardados.')
    } catch (saveError) { setError(saveError.message) } finally { setIsModuleSaving(false) }
  }

  function toggleClassDate(dateIso) {
    if (!dateIso || isCalendarDateDisabled(dateIso, selectedCut, selectedModules)) {
      return
    }
    setForm((current) => {
      const currentDates = current.fechas_clase || []
      const exists = currentDates.includes(dateIso)
      const nextDates = exists
        ? currentDates.filter((item) => item !== dateIso)
        : [...currentDates, dateIso].sort()
      return {
        ...current,
        fechas_clase: nextDates,
        dia_semana: nextDates[0] ? String(weekdayFromIsoDate(nextDates[0])) : current.dia_semana,
      }
    })
  }

  function clearSelectedDates() {
    setForm((current) => ({
      ...current,
      fechas_clase: [],
    }))
  }

  async function handleScheduleSubmit(event) {
    event.preventDefault()
    if (!selectedCut?.corte_id) {
      setError('Selecciona una cohorte para guardar el horario.')
      return
    }
    if (!form.fechas_clase?.length) {
      setError('Selecciona al menos un día de clase en el calendario.')
      return
    }
    if (!selectedModuleIds.length) {
      setError('Selecciona al menos uno de los cuatro módulos para guardar el horario.')
      return
    }

    const modulesToSave = selectedModules
    const orderedRanges = [...modulesToSave].sort((left, right) => String(left.fecha_inicio).localeCompare(String(right.fecha_inicio)))
    const overlappingRange = orderedRanges.find((module, index) =>
      index > 0 && module.fecha_inicio <= orderedRanges[index - 1].fecha_fin)
    if (overlappingRange) {
      setError(`El período de ${overlappingRange.nombre_modulo} se superpone con otro módulo.`)
      return
    }
    const batches = modulesToSave.map((module) => ({
      module,
      dates: form.fechas_clase.filter((date) => isDateInsideModule(date, module)),
      teacherId: String(moduleTeacherSelections[String(module.modulo_id)] || module.docente_corte_ids?.[0] || ''),
    }))
    const incompleteModule = batches.find((batch) => !batch.dates.length || !batch.teacherId)
    if (incompleteModule) {
      setError(`${incompleteModule.module.nombre_modulo} necesita al menos una fecha y un docente asignado.`)
      return
    }
    const registeredModuleIds = new Set((scheduleData?.schedules || []).map((schedule) => String(schedule.modulo_id)))
    const registeredBatch = batches.find((batch) => registeredModuleIds.has(String(batch.module.modulo_id)))
    if (registeredBatch && String(editingModuleId) !== String(registeredBatch.module.modulo_id)) {
      const duplicateMessage = `${registeredBatch.module.nombre_modulo} ya tiene un horario registrado. Utiliza Editar horario.`
      setError(duplicateMessage)
      setNotice({ type: 'warning', title: 'Horario ya registrado', message: duplicateMessage })
      return
    }
    if (editingModuleId && (batches.length !== 1 || String(batches[0].module.modulo_id) !== String(editingModuleId))) {
      setError('La edición debe realizarse sobre un solo módulo registrado.')
      return
    }

    setIsSaving(true)
    setMessage('')
    setError('')

    try {
      for (const batch of batches) {
        const assignedIds = (batch.module.docente_corte_ids || []).map(String)
        const nextAssignedIds = [...new Set([
          ...assignedIds,
          ...selectedCourseTeacherIds,
          batch.teacherId,
        ])].slice(0, 3)
        const response = await adminFetch('/api/auth/admin/course-cuts/modules/save/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            corte_id: selectedCut.corte_id,
            modulo_id: batch.module.modulo_id,
            nombre_modulo: batch.module.nombre_modulo,
            tema_modulo: batch.module.tema_modulo || '',
            fecha_inicio: batch.module.fecha_inicio,
            fecha_fin: batch.module.fecha_fin,
            fecha_finalizacion: batch.module.fecha_fin,
            actividades_finales: batch.module.actividades_finales || '',
            descripcion: batch.module.descripcion || '',
            docente_corte_ids: nextAssignedIds,
          }),
        })
        const payload = await readResponsePayload(response)
        if (!payload?.ok || !response.ok) {
          throw new Error(`${batch.module.nombre_modulo}: ${payload?.message ?? 'no fue posible asociar el docente'}`)
        }
      }
      const moduleSchedules = batches.map((batch) => {
        const teacher = teachers.find((item) => String(item.docente_corte_id) === batch.teacherId)
        return {
          horario_id: batches.length === 1 ? form.horario_id : '',
          fechas_clase: batch.dates,
          modulo_id: batch.module.modulo_id,
          docente_corte_id: batch.teacherId,
          codigo_docente: teacher?.codigo_docente || '',
        }
      })
      const response = await adminFetch('/api/auth/admin/course-cuts/schedule/save/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...form,
          horario_id: '',
          corte_id: selectedCut.corte_id,
          editar_existente: Boolean(editingModuleId),
          module_schedules: moduleSchedules,
        }),
      })
      const payload = await readResponsePayload(response)
      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible guardar los horarios (${response.status}).`)
      }
      const wasEditing = Boolean(editingModuleId)
      const refreshed = await loadScheduleData(selectedCut, { resetForm: true })
      if (!refreshed) {
        throw new Error('El servidor respondió correctamente, pero no fue posible verificar el horario en la base de datos.')
      }
      const confirmation = wasEditing
        ? 'Horario actualizado correctamente, incluido el docente responsable.'
        : `Horario registrado correctamente para ${batches.length} módulo(s). La información fue verificada en la base de datos.`
      setMessage(confirmation)
      setNotice({
        type: 'success',
        title: wasEditing ? 'Horario actualizado' : 'Horario registrado',
        message: confirmation,
      })
    } catch (saveError) {
      setError(saveError.message)
      const duplicate = /ya tiene un horario|ya se encuentra registrado/i.test(saveError.message || '')
      setNotice({
        type: duplicate ? 'warning' : 'error',
        title: duplicate ? 'Horario ya registrado' : 'No fue posible guardar el horario',
        message: saveError.message,
      })
      if (duplicate) {
        await loadScheduleData(selectedCut, { resetForm: true })
      }
    } finally {
      setIsSaving(false)
    }
  }

  if (isLoading) {
    return (
      <article className="module-card dashboard-module-card">
        <div>
          <h3>Cargando horarios</h3>
          <p>Estamos consultando las cohortes disponibles.</p>
        </div>
      </article>
    )
  }

  const metrics = scheduleData?.metrics || {}
  const scheduleUnavailable = scheduleData?.continuing_education && !scheduleData.continuing_education.available
  const teacherUnavailable = selectedCut && scheduleData && !isScheduleLoading && !moduleTeachers.length
  const selectedModuleSet = new Set(selectedModuleIds.map(String))
  const visibleSessions = (scheduleData?.sessions || []).filter(
    (session) => !selectedModuleSet.size || selectedModuleSet.has(String(session.modulo_id)),
  )
  const selectedDates = form.fechas_clase || []
  const visibleSelectedDates = selectedDates.slice(0, 8)
  const hiddenSelectedDatesCount = Math.max(selectedDates.length - visibleSelectedDates.length, 0)

  return (
    <section className="teacher-panel admin-schedule-panel" aria-labelledby="admin-schedule-title">
      <div className="admin-section-heading">
        <div>
          <h3 id="admin-schedule-title">Horario</h3>
          <p>Crea horarios por cohorte y genera las sesiones vinculadas.</p>
        </div>
      </div>

      {error ? <p className="form-error">{error}</p> : null}
      {message ? <p className="form-success">{message}</p> : null}

      {selectedCut ? (
        <article className="module-card teacher-panel-card course-module-manager">
          <div className="admin-subsection-header">
            <div>
              <h4>Módulos del diplomado</h4>
              <p>Asigna uno o varios docentes a cada módulo. Un docente puede participar en varios módulos.</p>
            </div>
          </div>
          <div className="tab-switcher course-module-tabs" role="tablist" aria-label="Módulos del diplomado">
            {modules.map((module) => (
              <button
                key={module.modulo_id}
                type="button"
                className={String(module.modulo_id) === String(selectedModuleId) ? 'is-active' : ''}
                onClick={() => selectModule(module)}
                role="tab"
                aria-selected={String(module.modulo_id) === String(selectedModuleId)}
              >
                {module.nombre_modulo}
                <small>Semanas {module.semana_inicio}-{module.semana_fin}</small>
              </button>
            ))}
          </div>
          <div className="course-module-bulk-selector">
            <div>
              <strong>Módulos incluidos en la creación del horario</strong>
              <span>Selecciona los cuatro para cargar sus fechas en una sola operación.</span>
            </div>
            <div className="course-module-bulk-options">
              {modules.map((module) => {
                const moduleId = String(module.modulo_id)
                const allowedTeachers = teachers.filter((teacher) =>
                  (module.docente_corte_ids || []).map(String).includes(String(teacher.docente_corte_id)))
                return (
                  <div key={module.modulo_id} className="course-module-bulk-option">
                    <label className="student-selector-cell">
                      <input
                        type="checkbox"
                        checked={selectedModuleIds.includes(moduleId)}
                        onChange={() => toggleScheduleModule(module.modulo_id)}
                        disabled={(scheduleData?.schedules || []).some((schedule) =>
                          String(schedule.modulo_id) === moduleId) && String(editingModuleId) !== moduleId}
                      />
                      <span>{module.nombre_modulo}</span>
                    </label>
                    <select
                      aria-label={`Docente responsable de ${module.nombre_modulo}`}
                      value={moduleTeacherSelections[moduleId] || ''}
                      onChange={(event) => selectResponsibleTeacher(event.target.value, moduleId)}
                      disabled={!selectedModuleIds.includes(moduleId) || !allowedTeachers.length}
                    >
                      {allowedTeachers.length ? allowedTeachers.map((teacher) => (
                        <option key={teacher.docente_corte_id} value={teacher.docente_corte_id}>{teacher.nombre}</option>
                      )) : <option value="">Sin docente asignado</option>}
                    </select>
                  </div>
                )
              })}
            </div>
            <button
              type="button"
              className="ghost-button compact-button"
              onClick={selectPendingModulesAndDates}
            >
              Marcar módulos pendientes
            </button>
          </div>
          <div className="admin-form-grid schedule-form-grid course-module-form">
            <label className="field">
              <span>Nombre del módulo *</span>
              <input
                value={moduleForm.nombre_modulo}
                onChange={(event) => setModuleForm((current) => ({ ...current, nombre_modulo: event.target.value }))}
                placeholder={`MÓDULO ${toRoman(modules.length + 1)}`}
                maxLength="200"
                readOnly
              />
            </label>
            <label className="field">
              <span>Tema o descripción del módulo *</span>
              <input
                value={moduleForm.tema_modulo}
                onChange={(event) => setModuleForm((current) => ({ ...current, tema_modulo: event.target.value }))}
                maxLength="500"
                placeholder="Ejemplo: Fundamentos y marco normativo"
              />
            </label>
            <label className="field">
              <span>Fecha de finalización del módulo *</span>
              <input
                type="date"
                value={moduleForm.fecha_finalizacion}
                min={selectedModule?.fecha_inicio || ''}
                max={selectedModule?.fecha_fin || ''}
                onChange={(event) => setModuleForm((current) => ({ ...current, fecha_finalizacion: event.target.value }))}
              />
              <small>Rango permitido: {selectedModule?.fecha_inicio || '-'} a {selectedModule?.fecha_fin || '-'}</small>
            </label>
            <label className="field full-span">
              <span>Actividades finales *</span>
              <textarea
                value={moduleForm.actividades_finales}
                onChange={(event) => setModuleForm((current) => ({ ...current, actividades_finales: event.target.value }))}
                maxLength="2000"
                rows="4"
                placeholder="Evaluación final, proyecto, exposición, entrega u otras actividades de cierre..."
              />
            </label>
            <div className="field course-module-teachers">
              <div className="course-module-teacher-heading">
                <div>
                  <span>Docentes del módulo *</span>
                  <small>Selecciona entre uno y tres docentes. El mismo docente puede repetirse en otros módulos.</small>
                </div>
                <div className="course-module-teacher-actions">
                  <button type="button" className="ghost-button compact-button" onClick={selectAllModuleTeachers} disabled={!teachers.length}>
                    Seleccionar todos (máx. 3)
                  </button>
                  <button type="button" className="ghost-button compact-button" onClick={() => setModuleForm((current) => ({ ...current, docente_corte_ids: [] }))}>
                    Limpiar
                  </button>
                </div>
              </div>
              <div className="course-module-teacher-list">
                {teachers.map((teacher) => (
                  <label key={teacher.docente_corte_id} className="student-selector-cell">
                    <input
                      type="checkbox"
                      checked={moduleForm.docente_corte_ids.includes(String(teacher.docente_corte_id))}
                      onChange={() => toggleModuleTeacher(teacher.docente_corte_id)}
                    />
                    <span>{teacher.nombre} · {teacher.rol_docente || 'DOCENTE'}</span>
                  </label>
                ))}
                {!teachers.length ? <p className="teacher-panel-empty">Primero matricula docentes en la cohorte.</p> : null}
              </div>
            </div>
          </div>
          <div className="student-selection-actions">
            <button type="button" className="ghost-button compact-button" onClick={applyTeachersToAllModules} disabled={isModuleSaving || !teachers.length}>
              Aplicar docentes a los 4 módulos
            </button>
            <button type="button" className="submit-button compact-button" onClick={saveModule} disabled={isModuleSaving || !teachers.length}>
              {isModuleSaving ? 'Guardando...' : moduleForm.modulo_id ? 'Actualizar módulo' : 'Guardar módulo'}
            </button>
          </div>
        </article>
      ) : null}

      <article className="module-card teacher-panel-card admin-schedule-selector-card">
        <label className="field">
          <span>Cohorte</span>
          <select value={selectedCutId} onChange={handleCutChange} disabled={!cuts.length || isScheduleLoading}>
            {cuts.length ? (
              cuts.map((cut) => (
                <option key={cut.corte_id} value={cut.corte_id}>
                  {cutLabel(cut)}
                </option>
              ))
            ) : (
              <option value="">No hay cohortes registradas</option>
            )}
          </select>
        </label>

        <div className="field course-main-teachers">
          <span>Docentes principales del curso ({selectedCourseTeacherIds.length}/3)</span>
          <div className="course-main-teacher-list">
            {teachers.length ? teachers.map((teacher) => (
              <label key={teacher.docente_corte_id} className="student-selector-cell">
                <input
                  type="checkbox"
                  checked={selectedCourseTeacherIds.includes(String(teacher.docente_corte_id))}
                  onChange={() => toggleCourseTeacher(teacher.docente_corte_id)}
                  disabled={isScheduleLoading}
                />
                <span>
                  <strong>{teacher.nombre}</strong>
                  <small>{teacher.rol_docente || 'DOCENTE'} · {teacher.cedula || 'sin cédula'}</small>
                </span>
              </label>
            )) : <p className="teacher-panel-empty">Sin docentes matriculados.</p>}
          </div>
          <small>Los tres docentes seleccionados quedarán asociados al diplomado y sus módulos.</small>
        </div>

        {selectedCut ? (
          <section className="schedule-summary-grid" aria-label="Resumen de horario administrativo">
            <div>
              <span>Horarios</span>
              <strong>{formatNumber(metrics.horarios)}</strong>
            </div>
            <div>
              <span>Sesiones</span>
              <strong>{formatNumber(metrics.sesiones)}</strong>
            </div>
            <div>
              <span>Docentes</span>
              <strong>{formatNumber(teachers.length)}</strong>
            </div>
            <div>
              <span>Fechas</span>
              <strong>{formatNumber(visibleSessions.length)}</strong>
            </div>
          </section>
        ) : null}
        {teacherUnavailable ? (
          <p className="teacher-panel-empty admin-schedule-teacher-warning">
            Configura el módulo y asigna al menos un docente antes de crear su horario.
          </p>
        ) : null}
      </article>

      {selectedCut ? (
        <>
          <article className="module-card teacher-panel-card admin-schedule-editor-card">
            {scheduleUnavailable ? <p className="form-error">{scheduleData?.continuing_education?.message}</p> : null}
            {isScheduleLoading ? <p className="form-success">Cargando horario...</p> : null}
            {scheduleData && !isScheduleLoading ? (
              <div
                className={`schedule-database-status ${Number(metrics.horarios || 0) > 0 ? 'is-registered' : 'is-empty'}`}
                role="status"
              >
                <strong>{Number(metrics.horarios || 0) > 0 ? 'Horario registrado en la base de datos' : 'Horario pendiente de registro'}</strong>
                <span>{Number(metrics.horarios || 0) > 0
                  ? `${formatNumber(metrics.horarios)} horario(s) y ${formatNumber(metrics.sesiones)} sesión(es) recuperadas. No se puede crear nuevamente; utiliza Editar horarios registrados.`
                  : 'No se encontraron horarios ni sesiones guardadas. Las fechas de los módulos pendientes se marcaron automáticamente.'}</span>
              </div>
            ) : null}

            <section className="schedule-module-editor" aria-labelledby="schedule-module-editor-title">
              <div className="admin-subsection-header">
                <div>
                  <h4 id="schedule-module-editor-title">Módulos, docentes y fechas</h4>
                  <p>Activa los módulos, selecciona el docente responsable y define su fecha de finalización.</p>
                </div>
                <button
                  type="button"
                  className="ghost-button compact-button"
                  onClick={selectPendingModulesAndDates}
                >
                  Marcar fechas pendientes
                </button>
              </div>
              <div className="schedule-module-grid">
                {modules.map((module) => {
                  const moduleId = String(module.modulo_id)
                  const range = moduleDateRanges[moduleId] || {
                    fecha_inicio: module.fecha_inicio || '',
                    fecha_fin: module.fecha_fin || '',
                  }
                  const effectiveModule = { ...module, ...range }
                  const isSelected = selectedModuleIds.includes(moduleId)
                  const selectedCount = form.fechas_clase.filter((date) => isDateInsideModule(date, effectiveModule)).length
                  const registeredSchedules = (scheduleData?.schedules || []).filter(
                    (schedule) => String(schedule.modulo_id) === moduleId,
                  )
                  const registeredDates = sessionDatesForModule(scheduleData, moduleId)
                  const isRegistered = registeredSchedules.length > 0
                  return (
                    <article key={module.modulo_id} className={`schedule-module-card module-tone-${module.numero_modulo || 1} ${isSelected ? 'is-selected' : ''}`}>
                      <label className="student-selector-cell">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleScheduleModule(moduleId)}
                          disabled={isRegistered && String(editingModuleId) !== moduleId}
                        />
                        <strong>{module.nombre_modulo}</strong>
                      </label>
                      <span>Semanas {module.semana_inicio}-{module.semana_fin}</span>
                      <small>
                        {range.fecha_inicio || '-'} a {range.fecha_fin || '-'} · {isRegistered
                          ? `${registeredDates.length} fecha(s) registrada(s) en la base`
                          : `${selectedCount} fecha(s) marcada(s)`}
                      </small>
                      {isRegistered ? <strong className="schedule-registered-label">Horario registrado</strong> : null}
                      <label className="field">
                        <span>Docente responsable</span>
                        <select
                          value={moduleTeacherSelections[moduleId] || ''}
                          onChange={(event) => selectResponsibleTeacher(event.target.value, moduleId)}
                            disabled={!isSelected || !teachers.length || (isRegistered && String(editingModuleId) !== moduleId)}
                        >
                          {teachers.length ? teachers.map((teacher) => (
                            <option key={teacher.docente_corte_id} value={teacher.docente_corte_id}>{teacher.nombre}</option>
                          )) : <option value="">Sin docentes matriculados</option>}
                        </select>
                      </label>
                      <div className="schedule-module-date-fields">
                        <label className="field">
                          <span>Fecha de inicio</span>
                          <input
                            type="date"
                            value={range.fecha_inicio}
                            onChange={(event) => updateModuleDateRange(module, 'fecha_inicio', event.target.value)}
                            disabled={!isSelected || (isRegistered && String(editingModuleId) !== moduleId)}
                          />
                        </label>
                        <label className="field">
                          <span>Fecha de fin</span>
                          <input
                            type="date"
                            min={range.fecha_inicio || ''}
                            value={range.fecha_fin}
                            onChange={(event) => updateModuleDateRange(module, 'fecha_fin', event.target.value)}
                            disabled={!isSelected || (isRegistered && String(editingModuleId) !== moduleId)}
                          />
                        </label>
                      </div>
                    </article>
                  )
                })}
              </div>
              <p className="schedule-module-assignment-note">
                Marca las fechas de cada bloque de dos semanas en el calendario. Al guardar, cada fecha utilizará automáticamente el docente seleccionado en su módulo.
              </p>
            </section>

            <form className="auth-form compact-form schedule-form" onSubmit={handleScheduleSubmit}>
              <div className="schedule-calendar-panel">
                <div className="schedule-calendar-header">
                  <div>
                    <span className="eyebrow">Calendario</span>
                    <h4>{capitalize(monthFormatter.format(monthDateFromKey(calendarMonth)))}</h4>
                  </div>
                  <div className="schedule-calendar-actions">
                    <button
                      type="button"
                      className="ghost-button compact-button"
                      onClick={() => setCalendarMonth(shiftMonth(calendarMonth, -1))}
                    >
                      Anterior
                    </button>
                    <button
                      type="button"
                      className="ghost-button compact-button"
                      onClick={() => setCalendarMonth(resolveCalendarMonth(selectedCut, form.fechas_clase))}
                    >
                      Actual
                    </button>
                    <button
                      type="button"
                      className="ghost-button compact-button"
                      onClick={() => setCalendarMonth(shiftMonth(calendarMonth, 1))}
                    >
                      Siguiente
                    </button>
                  </div>
                </div>

                <div className="schedule-calendar-grid" aria-label="Calendario de días de clase">
                  {WEEKDAY_SHORT_LABELS.map((label) => (
                    <span key={label} className="schedule-calendar-weekday">
                      {label}
                    </span>
                  ))}
                  {calendarDays.map((day) => {
                    const selected = selectedDateSet.has(day.iso)
                    const disabled = isCalendarDateDisabled(day.iso, selectedCut, selectedModules)
                    const dateModule = selectedModules.find((module) => isDateInsideModule(day.iso, module))
                    return (
                      <button
                        key={day.iso}
                        type="button"
                        className={[
                          'schedule-calendar-day',
                          day.isCurrentMonth ? '' : 'is-muted',
                          dateModule ? `is-module-date module-date-${dateModule.numero_modulo || 1}` : '',
                          selected ? 'is-selected' : '',
                        ].filter(Boolean).join(' ')}
                        onClick={() => toggleClassDate(day.iso)}
                        disabled={disabled}
                        aria-pressed={selected}
                        title={dateModule ? `${dateModule.nombre_modulo} · ${day.iso}` : day.iso}
                      >
                        <span>{day.day}</span>
                      </button>
                    )
                  })}
                </div>

                <div className="schedule-selected-dates">
                  <div>
                    <strong>{formatNumber(form.fechas_clase?.length)} día(s) seleccionados</strong>
                    <span>{selectedModules.length ? `${selectedModules.length} módulo(s) seleccionados · calendario habilitado para sus ocho semanas` : 'Selecciona al menos un módulo'}</span>
                  </div>
                  {selectedDates.length ? (
                    <button type="button" className="ghost-button compact-button" onClick={clearSelectedDates}>
                      Limpiar días
                    </button>
                  ) : null}
                </div>

                {selectedDates.length ? (
                  <div className="schedule-date-chip-list" aria-label="Días de clase seleccionados">
                    {visibleSelectedDates.map((dateIso) => (
                      <button
                        key={dateIso}
                        type="button"
                        className="schedule-date-chip"
                        onClick={() => toggleClassDate(dateIso)}
                      >
                        {dateIso}
                      </button>
                    ))}
                    {hiddenSelectedDatesCount ? (
                      <span className="schedule-date-chip is-count">+{hiddenSelectedDatesCount}</span>
                    ) : null}
                  </div>
                ) : (
                  <p className="teacher-panel-empty">Selecciona en el calendario los días reales de clase.</p>
                )}
              </div>

              <div className="admin-form-grid schedule-form-grid">
                <label className="field">
                  <span>Modalidad *</span>
                  <select name="modalidad" value={form.modalidad} onChange={handleChange} required>
                    {MODALITY_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="field">
                  <span>Hora inicio *</span>
                  <input name="hora_inicio" type="time" value={form.hora_inicio} onChange={handleChange} required />
                </label>

                <label className="field">
                  <span>Hora fin *</span>
                  <input name="hora_fin" type="time" value={form.hora_fin} onChange={handleChange} required />
                </label>

              </div>

              <div className="student-selection-actions">
                <button
                  type="button"
                  className="ghost-button compact-button"
                  onClick={() => {
                    const nextForm = buildScheduleForm(selectedCut, scheduleData)
                    setForm(nextForm)
                    setCalendarMonth(resolveCalendarMonth(selectedCut, nextForm.fechas_clase))
                  }}
                  disabled={isSaving || isScheduleLoading}
                >
                  Limpiar
                </button>
                <button
                  type="submit"
                  className="submit-button compact-button"
                  disabled={isSaving || isScheduleLoading || Boolean(scheduleUnavailable) || !selectedModuleIds.length}
                >
                  {isSaving ? 'Guardando...' : `Guardar horario de ${selectedModuleIds.length || 0} módulo(s)`}
                </button>
              </div>
            </form>
          </article>

          <article className="module-card teacher-panel-card admin-schedule-table-card">
            <div className="admin-subsection-header">
              <div>
                <h4>Editar horarios registrados</h4>
                <p>Consulta la información guardada y cambia fechas, horas, modalidad o docente responsable.</p>
              </div>
              <button
                type="button"
                className="ghost-button compact-button"
                onClick={() => loadScheduleData(selectedCut, { resetForm: false })}
                disabled={isScheduleLoading}
              >
                Actualizar
              </button>
            </div>

            <div className="admin-table-wrap">
              <table className="admin-table schedule-table">
                <thead>
                  <tr>
                    <th>Módulo</th>
                    <th>Período</th>
                    <th>Hora</th>
                    <th>Docente</th>
                    <th>Modalidad</th>
                    <th>Sesiones</th>
                    <th>Acción</th>
                  </tr>
                </thead>
                <tbody>
                  {registeredModuleSummaries(scheduleData, modules).length ? (
                    registeredModuleSummaries(scheduleData, modules).map((summary) => (
                      <tr key={summary.module.modulo_id}>
                        <td>{summary.module.nombre_modulo}</td>
                        <td>{summary.firstDate || '-'}<span>{summary.lastDate || ''}</span></td>
                        <td>
                          <strong>{summary.schedule?.hora_inicio || '-'}</strong>
                          <span>{summary.schedule?.hora_fin || '-'}</span>
                        </td>
                        <td>
                          <strong>{summary.schedule?.docente_responsable?.nombre || '-'}</strong>
                          <span>
                            {summary.schedule?.docente_responsable?.codigo_docente || ''}
                          </span>
                        </td>
                        <td>{summary.schedule?.modalidad || '-'}</td>
                        <td>{formatNumber(summary.dates.length)}</td>
                        <td>
                          <button
                            type="button"
                            className="ghost-button compact-button table-action-button"
                            onClick={() => handleEditModuleSchedule(summary.module)}
                          >
                            Editar horario
                          </button>
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan="7">No existen horarios registrados en la base de datos.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </article>

          <article className="module-card teacher-panel-card admin-schedule-table-card">
            <div className="admin-subsection-header">
              <div>
                <h4>Fechas de clase</h4>
                <p>{visibleSessions.length} sesión(es) programadas para el módulo seleccionado.</p>
              </div>
            </div>

            <div className="admin-table-wrap">
              <table className="admin-table schedule-table">
                <thead>
                  <tr>
                    <th>Módulo</th>
                    <th>Fecha</th>
                    <th>Hora</th>
                    <th>Modalidad</th>
                    <th>Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleSessions.length ? (
                    visibleSessions.map((session) => (
                      <tr key={session.sesion_id}>
                        <td>{moduleNameFromId(modules, session.modulo_id)}</td>
                        <td>{session.fecha || '-'}</td>
                        <td>
                          <strong>{session.hora_inicio || '-'}</strong>
                          <span>{session.hora_fin || '-'}</span>
                        </td>
                        <td>{session.modalidad || '-'}</td>
                        <td>{session.estado || '-'}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan="5">No hay fechas de clase programadas.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </article>

        </>
      ) : (
        <article className="module-card dashboard-module-card">
          <div>
            <h3>Sin cohortes disponibles</h3>
            <p>Crea una cohorte antes de cargar horarios.</p>
          </div>
        </article>
      )}
      {notice ? (
        <div
          className="modal-backdrop schedule-notice-backdrop"
          role="presentation"
          onMouseDown={() => setNotice(null)}
        >
          <section
            className={`registered-user-modal schedule-notice-modal is-${notice.type || 'success'}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby="schedule-notice-title"
            aria-describedby="schedule-notice-message"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="schedule-notice-icon" aria-hidden="true">
              {notice.type === 'success' ? '✓' : notice.type === 'warning' ? '!' : '×'}
            </div>
            <div className="schedule-notice-copy">
              <h2 id="schedule-notice-title">{notice.title}</h2>
              <p id="schedule-notice-message">{notice.message}</p>
            </div>
            <button
              type="button"
              className="submit-button schedule-notice-action"
              onClick={() => setNotice(null)}
              autoFocus
            >
              Aceptar
            </button>
          </section>
        </div>
      ) : null}
    </section>
  )
}

function buildScheduleForm(cut, scheduleData) {
  const firstSchedule = scheduleData?.schedules?.[0] || null

  return {
    ...emptyScheduleForm,
    horario_id: firstSchedule?.horario_id || '',
    dia_semana: firstSchedule?.dia_semana ? String(firstSchedule.dia_semana) : emptyScheduleForm.dia_semana,
    hora_inicio: firstSchedule?.hora_inicio || emptyScheduleForm.hora_inicio,
    hora_fin: firstSchedule?.hora_fin || emptyScheduleForm.hora_fin,
    modalidad: firstSchedule?.modalidad || emptyScheduleForm.modalidad,
    aula: firstSchedule?.aula || '',
    enlace_virtual: firstSchedule?.enlace_virtual || '',
    fecha_desde: cut?.fecha_inicio_iso || '',
    fecha_hasta: cut?.fecha_fin_iso || '',
    fechas_clase: [],
  }
}

function cutLabel(cut) {
  const subject = cut.materias_label || cut.materia_pensum || cut.curso_educontinua || cut.cod_curso || 'Sin materia'
  const period = cut.periodo || cut.codigo_periodo || cut.nombre_corte || `Cohorte ${cut.numero_corte || cut.corte_id}`
  const status = cut.estado_inscripcion || cut.estado_corte || ''
  return `${subject} - ${period}${status ? ` - ${status}` : ''}`
}

function formatNumber(value) {
  return numberFormatter.format(Number(value || 0))
}

function resolveSelectedModule(scheduleData, currentModuleId = '') {
  const modules = scheduleData?.modules || []
  if (currentModuleId) {
    const current = modules.find((module) => String(module.modulo_id) === String(currentModuleId))
    if (current) return current
  }
  return modules[0] || null
}

function buildModuleForm(module, scheduleData) {
  if (module) {
    return {
      modulo_id: String(module.modulo_id || ''),
      nombre_modulo: module.nombre_modulo || '',
      tema_modulo: module.tema_modulo || '',
      fecha_finalizacion: module.fecha_finalizacion || module.fecha_fin || '',
      actividades_finales: module.actividades_finales || '',
      docente_corte_ids: (module.docente_corte_ids || []).map(String),
    }
  }
  const firstTeacher = scheduleData?.teachers?.[0]
  return {
    ...emptyModuleForm,
    nombre_modulo: `MÓDULO ${toRoman((scheduleData?.modules?.length || 0) + 1)}`,
    docente_corte_ids: firstTeacher ? [String(firstTeacher.docente_corte_id)] : [],
  }
}

function resolveSelectedTeacherId(scheduleData, currentTeacherId = '') {
  const teachers = scheduleData?.teachers || []
  const availableTeachers = teachers
  if (!availableTeachers.length) {
    return ''
  }
  if (currentTeacherId && availableTeachers.some((teacher) => String(teacher.docente_corte_id) === String(currentTeacherId))) {
    return currentTeacherId
  }
  const titular = availableTeachers.find((teacher) => String(teacher.rol_docente || '').toUpperCase() === 'TITULAR')
  return String((titular || availableTeachers[0]).docente_corte_id || '')
}

function toRoman(value) {
  const numbers = [
    [1000, 'M'], [900, 'CM'], [500, 'D'], [400, 'CD'], [100, 'C'], [90, 'XC'],
    [50, 'L'], [40, 'XL'], [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I'],
  ]
  let remaining = Math.max(1, Number(value || 1))
  return numbers.reduce((label, [number, roman]) => {
    while (remaining >= number) { label += roman; remaining -= number }
    return label
  }, '')
}

function buildCalendarDays(monthKey) {
  const [year, month] = monthKey.split('-').map(Number)
  const firstDay = new Date(year, month - 1, 1)
  const gridStart = new Date(firstDay)
  const mondayOffset = (firstDay.getDay() + 6) % 7
  gridStart.setDate(firstDay.getDate() - mondayOffset)

  return Array.from({ length: 42 }, (_, index) => {
    const current = new Date(gridStart)
    current.setDate(gridStart.getDate() + index)
    return {
      iso: toDateIso(current),
      day: current.getDate(),
      isCurrentMonth: current.getMonth() === month - 1,
    }
  })
}

function toDateIso(value) {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function toMonthKey(value) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}`
}

function monthDateFromKey(monthKey) {
  const [year, month] = monthKey.split('-').map(Number)
  return new Date(year, month - 1, 1)
}

function shiftMonth(monthKey, offset) {
  const current = monthDateFromKey(monthKey)
  current.setMonth(current.getMonth() + offset)
  return toMonthKey(current)
}

function resolveCalendarMonth(cut, selectedDates = []) {
  const firstDate = selectedDates?.[0] || cut?.fecha_inicio_iso || ''
  if (firstDate) {
    const parsed = new Date(`${firstDate}T00:00:00`)
    if (!Number.isNaN(parsed.getTime())) {
      return toMonthKey(parsed)
    }
  }
  return toMonthKey(new Date())
}

function isCalendarDateDisabled(dateIso, cut, selectedModules = []) {
  if (!cut || !selectedModules.length) {
    return true
  }
  return !selectedModules.some((module) => isDateInsideModule(dateIso, module))
}

function sessionDatesForModule(scheduleData, moduleId) {
  const normalizedModuleId = String(moduleId || '')
  const dates = (scheduleData?.sessions || [])
    .filter((session) => String(session.modulo_id) === normalizedModuleId)
    .map((session) => session.fecha)
    .filter(Boolean)
  return [...new Set(dates)].sort()
}

function registeredModuleSummaries(scheduleData, modules) {
  return (modules || []).map((module) => {
    const moduleId = String(module.modulo_id || '')
    const schedules = (scheduleData?.schedules || []).filter(
      (schedule) => String(schedule.modulo_id) === moduleId,
    )
    const dates = sessionDatesForModule(scheduleData, moduleId)
    return {
      module,
      schedules,
      schedule: schedules[0] || null,
      dates,
      firstDate: dates[0] || '',
      lastDate: dates.at(-1) || '',
    }
  }).filter((summary) => summary.schedules.length)
}

function isDateInsideModule(dateIso, module) {
  if (!dateIso || !module) return false
  if (module.fecha_inicio && dateIso < module.fecha_inicio) return false
  if (module.fecha_fin && dateIso > module.fecha_fin) return false
  return true
}

function datesBetween(startIso, endIso) {
  if (!startIso || !endIso || startIso > endIso) return []
  const start = new Date(`${startIso}T00:00:00`)
  const end = new Date(`${endIso}T00:00:00`)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return []
  const dates = []
  const current = new Date(start)
  while (current <= end && dates.length <= 14) {
    dates.push(toDateIso(current))
    current.setDate(current.getDate() + 1)
  }
  return dates
}

function moduleNameFromId(modules, moduleId) {
  return modules.find((module) => String(module.modulo_id) === String(moduleId))?.nombre_modulo || '-'
}

function weekdayFromIsoDate(dateIso) {
  const parsed = new Date(`${dateIso}T00:00:00`)
  const day = parsed.getDay()
  return day === 0 ? 7 : day
}

function capitalize(value) {
  const text = String(value || '')
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : ''
}

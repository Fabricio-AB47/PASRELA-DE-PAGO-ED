import { useEffect, useState } from 'react'
import { readResponsePayload } from '../shared.js'
import { adminFetch } from './api.js'
import { STATUS_OPTIONS, categoryLabel, nextSubjectCode, normalizeSearchText, sortByCareerCode, statusLabel, uniqueSortedValues } from './utils.js'

const DEFAULT_PENSUM_FORM = {
  cod_anio_basica: '',
  codigo_materia: '',
  unidad_organiza: '',
  nombre_materia: '',
  semestre: '1',
  creditos: '',
  orden: '',
  num_malla: '',
  cod_materia: '',
  horas: '',
  modalidad_valor: 'presencial',
  valor_hora: '',
  valor_hora_virtual: '',
  combinar_materia: '0',
  ver_reporte: '1',
  secuencia_materia: '0',
  tipo_materia: 'E',
  estado_materia: 'A',
}

const pensumFormFromItem = (item) => ({
  cod_anio_basica: String(item.cod_anio_basica || ''),
  codigo_materia: String(item.codigo_materia || ''),
  unidad_organiza: String(item.unidad_organiza || ''),
  nombre_materia: String(item.nombre_materia || ''),
  semestre: String(item.semestre || '1'),
  creditos: String(item.creditos || ''),
  orden: String(item.orden || ''),
  num_malla: String(item.num_malla || ''),
  cod_materia: String(item.cod_materia || ''),
  horas: String(item.horas || ''),
  modalidad_valor:
    Number(item.valor_hora_virtual || 0) > 0 && Number(item.valor_hora || 0) <= 0
      ? 'online'
      : 'presencial',
  valor_hora: String(item.valor_hora || ''),
  valor_hora_virtual: String(item.valor_hora_virtual || ''),
  combinar_materia: String(item.combinar_materia || '0'),
  ver_reporte: String(item.ver_reporte || '1'),
  secuencia_materia: String(item.secuencia_materia || '0'),
  tipo_materia: String(item.tipo_materia || 'E').slice(0, 1) || 'E',
  estado_materia: item.es_activo ? 'A' : 'P',
})

export default function AdminAcademicPanel() {
  const [catalogs, setCatalogs] = useState({
    carreras: [],
    pensum: [],
  })
  const [pensumForm, setPensumForm] = useState(DEFAULT_PENSUM_FORM)
  const [pensumFilterCareer, setPensumFilterCareer] = useState('')
  const [careerSearch, setCareerSearch] = useState('')
  const [careerCategoryFilter, setCareerCategoryFilter] = useState('all')
  const [careerStatusFilter, setCareerStatusFilter] = useState('all')
  const [pensumSearch, setPensumSearch] = useState('')
  const [pensumCategoryFilter, setPensumCategoryFilter] = useState('all')
  const [pensumSemesterFilter, setPensumSemesterFilter] = useState('all')
  const [pensumStatusFilter, setPensumStatusFilter] = useState('all')
  const [isLoading, setIsLoading] = useState(true)
  const [isSavingPensum, setIsSavingPensum] = useState(false)
  const [isUpdatingStatus, setIsUpdatingStatus] = useState(false)
  const [academicError, setAcademicError] = useState('')
  const [academicMessage, setAcademicMessage] = useState('')
  const [isCareerListOpen, setIsCareerListOpen] = useState(false)
  const [isPensumFormOpen, setIsPensumFormOpen] = useState(false)

  async function loadAcademicCatalogs(forceNextCode = false) {
    setIsLoading(true)
    setAcademicError('')

    try {
      const response = await adminFetch('/api/auth/admin/academic-catalogs/')
      const payload = await readResponsePayload(response)

      if (!payload || !response.ok || !payload.ok) {
        throw new Error(payload?.message ?? `No fue posible cargar el catalogo (${response.status}).`)
      }

      const loadedCatalogs = payload.catalogs || { carreras: [], pensum: [] }
      setCatalogs(loadedCatalogs)
      setPensumForm((current) => {
        const selectedCareer = current.cod_anio_basica || loadedCatalogs.carreras?.[0]?.cod_anio_basica || ''
        return {
          ...current,
          cod_anio_basica: selectedCareer,
          codigo_materia:
            forceNextCode || !current.codigo_materia
              ? nextSubjectCode(loadedCatalogs.pensum)
              : current.codigo_materia,
          tipo_materia: current.tipo_materia || 'E',
        }
      })
      setPensumFilterCareer((current) => current || loadedCatalogs.carreras?.[0]?.cod_anio_basica || '')
    } catch (error) {
      setAcademicError(error.message)
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    let isMounted = true

    adminFetch('/api/auth/admin/academic-catalogs/')
      .then(async (response) => {
        const payload = await readResponsePayload(response)
        if (!payload || !response.ok || !payload.ok) {
          throw new Error(payload?.message ?? `No fue posible cargar el catalogo (${response.status}).`)
        }
        return payload.catalogs || { carreras: [], pensum: [] }
      })
      .then((loadedCatalogs) => {
        if (!isMounted) {
          return
        }

        setCatalogs(loadedCatalogs)
        setPensumForm((current) => ({
          ...current,
          cod_anio_basica: current.cod_anio_basica || loadedCatalogs.carreras?.[0]?.cod_anio_basica || '',
          codigo_materia:
            current.codigo_materia ||
            nextSubjectCode(loadedCatalogs.pensum),
          tipo_materia: current.tipo_materia || 'E',
        }))
        setPensumFilterCareer((current) => current || loadedCatalogs.carreras?.[0]?.cod_anio_basica || '')
      })
      .catch((error) => {
        if (isMounted) {
          setAcademicError(error.message)
        }
      })
      .finally(() => {
        if (isMounted) {
          setIsLoading(false)
        }
      })

    return () => {
      isMounted = false
    }
  }, [])

  function handlePensumChange(event) {
    const { name, value } = event.target
    const normalizedValue = name === 'tipo_materia' ? value.toUpperCase().slice(0, 1) : value
    if (name === 'modalidad_valor') {
      setPensumForm((current) => ({
        ...current,
        modalidad_valor: value,
        valor_hora_virtual:
          value === 'online' && !current.valor_hora_virtual
            ? current.valor_hora
            : current.valor_hora_virtual,
        valor_hora:
          value === 'presencial' && !current.valor_hora
            ? current.valor_hora_virtual
            : current.valor_hora,
      }))
      return
    }

    setPensumForm((current) => ({
      ...current,
      [name]: normalizedValue,
      ...(name === 'cod_anio_basica'
        ? {
            codigo_materia: nextSubjectCode(catalogs.pensum),
          }
        : {}),
    }))
  }

  async function postAcademicUpdate(url, body) {
    const response = await adminFetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    })
    const payload = await readResponsePayload(response)
    if (!payload || !response.ok || !payload.ok) {
      throw new Error(payload?.message ?? `No fue posible guardar (${response.status}).`)
    }
    return payload
  }

  async function handleCarreraStatusChange(carrera, estado) {
    setIsUpdatingStatus(true)
    setAcademicError('')
    setAcademicMessage('')
    setCatalogs((current) => ({
      ...current,
      carreras: current.carreras.map((item) =>
        String(item.cod_anio_basica) === String(carrera.cod_anio_basica)
          ? {
              ...item,
              estado_raw: estado,
              estado: statusLabel(estado),
              es_activo: estado === 'A',
            }
          : item,
      ),
    }))

    try {
      await postAcademicUpdate('/api/auth/admin/carrera-status/', {
        cod_anio_basica: carrera.cod_anio_basica,
        estado,
      })
      setAcademicMessage('Estado de carrera actualizado.')
      await loadAcademicCatalogs()
    } catch (error) {
      setAcademicError(error.message)
      await loadAcademicCatalogs()
    } finally {
      setIsUpdatingStatus(false)
    }
  }

  async function handlePensumStatusChange(item, estado) {
    setIsUpdatingStatus(true)
    setAcademicError('')
    setAcademicMessage('')
    setCatalogs((current) => ({
      ...current,
      pensum: current.pensum.map((pensumItem) =>
        String(pensumItem.cod_anio_basica) === String(item.cod_anio_basica) &&
        String(pensumItem.codigo_materia) === String(item.codigo_materia)
          ? {
              ...pensumItem,
              estado_materia_raw: estado,
              estado_materia: statusLabel(estado),
              es_activo: estado === 'A',
            }
          : pensumItem,
      ),
    }))

    try {
      await postAcademicUpdate('/api/auth/admin/pensum-status/', {
        cod_anio_basica: item.cod_anio_basica,
        codigo_materia: item.codigo_materia,
        estado_materia: estado,
      })
      setAcademicMessage('Estado de materia actualizado.')
      await loadAcademicCatalogs()
    } catch (error) {
      setAcademicError(error.message)
      await loadAcademicCatalogs()
    } finally {
      setIsUpdatingStatus(false)
    }
  }

  async function handlePensumSubmit(event) {
    event.preventDefault()
    setIsSavingPensum(true)
    setAcademicError('')
    setAcademicMessage('')

    try {
      const payload = await postAcademicUpdate('/api/auth/admin/pensum/', pensumForm)
      setAcademicMessage(
        payload.result?.action === 'actualizado'
          ? 'Pensum actualizado correctamente.'
          : 'Materia agregada al pensum correctamente.',
      )
      setPensumForm((current) => ({
        ...DEFAULT_PENSUM_FORM,
        cod_anio_basica: current.cod_anio_basica,
        codigo_materia: '',
      }))
      await loadAcademicCatalogs(true)
    } catch (error) {
      setAcademicError(error.message)
    } finally {
      setIsSavingPensum(false)
    }
  }

  function handlePensumEdit(item) {
    setPensumForm(pensumFormFromItem(item))
    setIsPensumFormOpen(true)
    setIsCareerListOpen(false)
  }

  const sortedCarreras = sortByCareerCode(catalogs.carreras)
  const careerCategories = uniqueSortedValues(sortedCarreras.map((carrera) => carrera.categoria || carrera.tp_escuela))
  const careerGroups = careerCategories.map((category) => ({
    category,
    carreras: sortedCarreras.filter(
      (carrera) => categoryLabel(carrera.categoria || carrera.tp_escuela) === category,
    ),
  }))
  const careerSearchTerm = normalizeSearchText(careerSearch)
  const filteredCarreras = sortedCarreras.filter((carrera) => {
    const statusValue = carrera.es_activo ? 'A' : 'P'
    const category = categoryLabel(carrera.categoria || carrera.tp_escuela)
    const searchableText = normalizeSearchText(
      `${carrera.cod_anio_basica} ${carrera.nombre_basica} ${category} ${carrera.estado}`,
    )

    return (
      (!careerSearchTerm || searchableText.includes(careerSearchTerm)) &&
      (careerCategoryFilter === 'all' || category === careerCategoryFilter) &&
      (careerStatusFilter === 'all' || statusValue === careerStatusFilter)
    )
  })
  const pensumByCareer = catalogs.pensum.filter(
    (item) => String(item.cod_anio_basica) === String(pensumFilterCareer),
  )
  const pensumCategories = uniqueSortedValues(
    pensumByCareer.map((item) => item.categoria || item.tipo_materia || item.unidad_organiza),
  )
  const pensumSemesters = uniqueSortedValues(
    pensumByCareer.map((item) => item.semestre),
    'Sin semestre',
  )
  const pensumSearchTerm = normalizeSearchText(pensumSearch)
  const selectedCareerPensum = pensumByCareer.filter((item) => {
    const statusValue = item.es_activo ? 'A' : 'P'
    const category = categoryLabel(item.categoria || item.tipo_materia || item.unidad_organiza)
    const semester = categoryLabel(item.semestre, 'Sin semestre')
    const searchableText = normalizeSearchText(
      `${item.codigo_materia} ${item.nombre_materia} ${category} ${semester} ${item.estado_materia}`,
    )

    return (
      (!pensumSearchTerm || searchableText.includes(pensumSearchTerm)) &&
      (pensumCategoryFilter === 'all' || category === pensumCategoryFilter) &&
      (pensumSemesterFilter === 'all' || semester === pensumSemesterFilter) &&
      (pensumStatusFilter === 'all' || statusValue === pensumStatusFilter)
    )
  })

  return (
    <section id="admin-academic" className="admin-academic">
      <article className="module-card full-span">
        <div className="module-card-header">
          <div>
            <h3>Administrar carreras y pensum</h3>
            <p>Activa carreras, registra materias del pensum y controla que solo lo activo aparezca en inscripción.</p>
          </div>
          <div className="academic-action-bar">
            <button
              type="button"
              className="ghost-button compact-button"
              onClick={() => {
                setIsCareerListOpen(true)
                setIsPensumFormOpen(false)
              }}
            >
              Carreras
            </button>
            <button
              type="button"
              className="ghost-button compact-button"
              onClick={() => {
                setPensumForm((current) => {
                  const selectedCareer = current.cod_anio_basica || sortedCarreras[0]?.cod_anio_basica || ''
                  return {
                    ...DEFAULT_PENSUM_FORM,
                    cod_anio_basica: selectedCareer,
                    codigo_materia: nextSubjectCode(catalogs.pensum),
                  }
                })
                setIsPensumFormOpen(true)
                setIsCareerListOpen(false)
              }}
            >
              Registrar pensum
            </button>
            <button type="button" className="ghost-button compact-button" onClick={() => loadAcademicCatalogs()} disabled={isLoading}>
              {isLoading ? 'Cargando...' : 'Actualizar'}
            </button>
          </div>
        </div>

        {academicError ? <p className="form-error">{academicError}</p> : null}
        {academicMessage ? <p className="form-success">{academicMessage}</p> : null}

        {isCareerListOpen ? (
          <div className="modal-backdrop" role="presentation">
            <section
              id="career-list-panel"
              className="career-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="career-modal-title"
            >
              <div className="career-modal-header">
                <div>
                  <h4 id="career-modal-title">Carreras</h4>
                  <p>Lista ordenada por codigo.</p>
                </div>
                <button
                  type="button"
                  className="ghost-button compact-button"
                  onClick={() => setIsCareerListOpen(false)}
                >
                  Cerrar
                </button>
              </div>
              <div className="career-modal-body">
                <div className="catalog-filter-grid">
                  <label className="field">
                    <span>Buscar carrera</span>
                    <input
                      type="search"
                      value={careerSearch}
                      onChange={(event) => setCareerSearch(event.target.value)}
                      placeholder="Codigo, carrera o categoria"
                    />
                  </label>
                  <label className="field">
                    <span>Categoria</span>
                    <select
                      value={careerCategoryFilter}
                      onChange={(event) => setCareerCategoryFilter(event.target.value)}
                    >
                      <option value="all">Todas las categorias</option>
                      {careerCategories.map((category) => (
                        <option key={category} value={category}>
                          {category}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    <span>Estado</span>
                    <select
                      value={careerStatusFilter}
                      onChange={(event) => setCareerStatusFilter(event.target.value)}
                    >
                      <option value="all">Todos los estados</option>
                      {STATUS_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <p className="filter-count">{filteredCarreras.length} carreras encontradas.</p>
                <div className="admin-table-wrap">
                  <table className="admin-table">
                    <thead>
                      <tr>
                        <th>Codigo</th>
                        <th>Carrera</th>
                        <th>Categoria</th>
                        <th>Estado</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredCarreras.map((carrera) => (
                        <tr key={carrera.cod_anio_basica || carrera.num}>
                          <td>{carrera.cod_anio_basica}</td>
                          <td>{carrera.nombre_basica}</td>
                          <td>{categoryLabel(carrera.categoria || carrera.tp_escuela)}</td>
                          <td>
                            <select
                              value={carrera.es_activo ? 'A' : 'P'}
                              onChange={(event) => handleCarreraStatusChange(carrera, event.target.value)}
                              disabled={isUpdatingStatus}
                            >
                              {STATUS_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                          </td>
                        </tr>
                      ))}
                      {!filteredCarreras.length ? (
                        <tr>
                          <td colSpan="4">No hay carreras que coincidan con los filtros.</td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>
          </div>
        ) : null}

        {isPensumFormOpen ? (
          <div className="modal-backdrop" role="presentation">
            <section
              className="career-modal pensum-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="pensum-modal-title"
            >
              <div className="career-modal-header">
                <div>
                  <h4 id="pensum-modal-title">Ingresar pensum</h4>
                  <p>Registra o actualiza la materia vinculada a una carrera.</p>
                </div>
                <button
                  type="button"
                  className="ghost-button compact-button"
                  onClick={() => setIsPensumFormOpen(false)}
                >
                  Cerrar
                </button>
              </div>
              <div className="career-modal-body">
                {academicError ? <p className="form-error">{academicError}</p> : null}
                {academicMessage ? <p className="form-success">{academicMessage}</p> : null}

                <form className="auth-form compact-form" onSubmit={handlePensumSubmit}>
                  <div className="admin-form-grid">
                    <label className="field full-span">
                      <span>Carrera</span>
                      <select
                        name="cod_anio_basica"
                        value={pensumForm.cod_anio_basica}
                        onChange={handlePensumChange}
                        required
                      >
                        <option value="">Selecciona una carrera</option>
                        {careerGroups.map((group) => (
                          <optgroup key={group.category} label={group.category}>
                            {group.carreras.map((carrera) => (
                              <option key={carrera.cod_anio_basica} value={carrera.cod_anio_basica}>
                                {carrera.nombre_basica} ({carrera.estado})
                              </option>
                            ))}
                          </optgroup>
                        ))}
                      </select>
                    </label>
                    <label className="field">
                      <span>Codigo materia</span>
                      <input
                        name="codigo_materia"
                        type="text"
                        value={pensumForm.codigo_materia}
                        onChange={handlePensumChange}
                        placeholder="Siguiente automatico"
                        readOnly
                        required
                      />
                    </label>
                    <label className="field">
                      <span>Codigo alterno</span>
                      <input
                        name="cod_materia"
                        type="text"
                        maxLength="50"
                        value={pensumForm.cod_materia}
                        onChange={handlePensumChange}
                      />
                    </label>
                    <label className="field">
                      <span>Categoria</span>
                      <input
                        name="tipo_materia"
                        type="text"
                        maxLength="1"
                        value={pensumForm.tipo_materia}
                        onChange={handlePensumChange}
                        placeholder="E"
                      />
                    </label>
                    <label className="field">
                      <span>Unidad organiza</span>
                      <input
                        name="unidad_organiza"
                        type="text"
                        maxLength="50"
                        value={pensumForm.unidad_organiza}
                        onChange={handlePensumChange}
                      />
                    </label>
                    <label className="field full-span">
                      <span>Nombre materia</span>
                      <input
                        name="nombre_materia"
                        type="text"
                        maxLength="200"
                        value={pensumForm.nombre_materia}
                        onChange={handlePensumChange}
                        required
                      />
                    </label>
                    <label className="field">
                      <span>Semestre</span>
                      <input
                        name="semestre"
                        type="number"
                        min="1"
                        value={pensumForm.semestre}
                        onChange={handlePensumChange}
                      />
                    </label>
                    <label className="field">
                      <span>Creditos</span>
                      <input
                        name="creditos"
                        type="number"
                        min="0"
                        step="0.01"
                        value={pensumForm.creditos}
                        onChange={handlePensumChange}
                      />
                    </label>
                    <label className="field">
                      <span>Orden</span>
                      <input
                        name="orden"
                        type="number"
                        min="1"
                        value={pensumForm.orden}
                        onChange={handlePensumChange}
                        placeholder="Auto"
                      />
                    </label>
                    <label className="field">
                      <span>Num malla</span>
                      <input
                        name="num_malla"
                        type="number"
                        min="0"
                        value={pensumForm.num_malla}
                        onChange={handlePensumChange}
                        placeholder="Auto"
                      />
                    </label>
                    <label className="field">
                      <span>Horas</span>
                      <input
                        name="horas"
                        type="number"
                        min="0"
                        step="1"
                        value={pensumForm.horas}
                        onChange={handlePensumChange}
                      />
                    </label>
                    <section className="rate-mode-panel full-span" aria-labelledby="rate-mode-title">
                      <div className="rate-mode-header">
                        <div>
                          <h5 id="rate-mode-title">Valor por modalidad</h5>
                          <p>Selecciona si el valor aplica para modalidad presencial u online.</p>
                        </div>
                        <label className="field compact-filter">
                          <span>Modalidad</span>
                          <select
                            name="modalidad_valor"
                            value={pensumForm.modalidad_valor}
                            onChange={handlePensumChange}
                          >
                            <option value="presencial">Presencial</option>
                            <option value="online">Online</option>
                          </select>
                        </label>
                      </div>

                      <div className="rate-mode-grid">
                        <label className={`field ${pensumForm.modalidad_valor === 'presencial' ? '' : 'muted-field'}`}>
                          <span>Valor hora presencial</span>
                          <input
                            name="valor_hora"
                            type="number"
                            min="0"
                            step="0.00001"
                            value={pensumForm.valor_hora}
                            onChange={handlePensumChange}
                            disabled={pensumForm.modalidad_valor !== 'presencial'}
                            required={pensumForm.modalidad_valor === 'presencial'}
                          />
                        </label>
                        <label className={`field ${pensumForm.modalidad_valor === 'online' ? '' : 'muted-field'}`}>
                          <span>Valor hora online</span>
                          <input
                            name="valor_hora_virtual"
                            type="number"
                            min="0"
                            step="0.00001"
                            value={pensumForm.valor_hora_virtual}
                            onChange={handlePensumChange}
                            disabled={pensumForm.modalidad_valor !== 'online'}
                            required={pensumForm.modalidad_valor === 'online'}
                          />
                        </label>
                      </div>
                    </section>
                    <label className="field">
                      <span>Combinar materia</span>
                      <select
                        name="combinar_materia"
                        value={pensumForm.combinar_materia}
                        onChange={handlePensumChange}
                      >
                        <option value="0">No</option>
                        <option value="1">Si</option>
                      </select>
                    </label>
                    <label className="field">
                      <span>Ver reporte</span>
                      <select
                        name="ver_reporte"
                        value={pensumForm.ver_reporte}
                        onChange={handlePensumChange}
                      >
                        <option value="1">Si</option>
                        <option value="0">No</option>
                      </select>
                    </label>
                    <label className="field">
                      <span>Secuencia materia</span>
                      <input
                        name="secuencia_materia"
                        type="text"
                        maxLength="50"
                        value={pensumForm.secuencia_materia}
                        onChange={handlePensumChange}
                      />
                    </label>
                    <label className="field full-span">
                      <span>Estado materia</span>
                      <select
                        name="estado_materia"
                        value={pensumForm.estado_materia}
                        onChange={handlePensumChange}
                      >
                        {STATUS_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <button type="submit" className="submit-button" disabled={isSavingPensum}>
                    {isSavingPensum ? 'Guardando...' : 'Guardar pensum'}
                  </button>
                </form>
              </div>
            </section>
          </div>
        ) : null}

        <section className="admin-subsection">
          <div className="admin-subsection-header">
            <h4>Materias del pensum</h4>
          </div>
          <div className="catalog-filter-grid pensum-filter-grid">
            <label className="field">
              <span>Carrera</span>
              <select
                value={pensumFilterCareer}
                onChange={(event) => {
                  setPensumFilterCareer(event.target.value)
                  setPensumCategoryFilter('all')
                  setPensumSemesterFilter('all')
                }}
              >
                <option value="">Selecciona una carrera</option>
                {careerGroups.map((group) => (
                  <optgroup key={group.category} label={group.category}>
                    {group.carreras.map((carrera) => (
                      <option key={carrera.cod_anio_basica} value={carrera.cod_anio_basica}>
                        {carrera.nombre_basica} ({carrera.estado})
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Buscar materia</span>
              <input
                type="search"
                value={pensumSearch}
                onChange={(event) => setPensumSearch(event.target.value)}
                placeholder="Codigo, materia o categoria"
              />
            </label>
            <label className="field">
              <span>Categoria</span>
              <select
                value={pensumCategoryFilter}
                onChange={(event) => setPensumCategoryFilter(event.target.value)}
              >
                <option value="all">Todas las categorias</option>
                {pensumCategories.map((category) => (
                  <option key={category} value={category}>
                    {category}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Semestre</span>
              <select
                value={pensumSemesterFilter}
                onChange={(event) => setPensumSemesterFilter(event.target.value)}
              >
                <option value="all">Todos</option>
                {pensumSemesters.map((semester) => (
                  <option key={semester} value={semester}>
                    {semester}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Estado</span>
              <select
                value={pensumStatusFilter}
                onChange={(event) => setPensumStatusFilter(event.target.value)}
              >
                <option value="all">Todos los estados</option>
                {STATUS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="filter-count">{selectedCareerPensum.length} materias encontradas.</p>
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Codigo</th>
                  <th>Materia</th>
                  <th>Categoria</th>
                  <th>Semestre</th>
                  <th>Creditos</th>
                  <th>Horas</th>
                  <th>Malla</th>
                  <th>Monto</th>
                  <th>Estado</th>
                  <th>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {selectedCareerPensum.map((item) => (
                  <tr key={item.row_key}>
                    <td>{item.codigo_materia}</td>
                    <td>{item.nombre_materia}</td>
                    <td>{categoryLabel(item.categoria || item.tipo_materia || item.unidad_organiza)}</td>
                    <td>{categoryLabel(item.semestre, 'Sin semestre')}</td>
                    <td>{item.creditos}</td>
                    <td>{item.horas}</td>
                    <td>{item.num_malla}</td>
                    <td>RD$ {item.monto_calculado}</td>
                    <td>
                      <select
                        value={item.es_activo ? 'A' : 'P'}
                        onChange={(event) => handlePensumStatusChange(item, event.target.value)}
                        disabled={isUpdatingStatus}
                      >
                        {STATUS_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="ghost-button compact-button"
                        onClick={() => handlePensumEdit(item)}
                      >
                        Editar
                      </button>
                    </td>
                  </tr>
                ))}
                {!selectedCareerPensum.length ? (
                  <tr>
                    <td colSpan="10">No hay materias registradas para esta busqueda.</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>
      </article>
    </section>
  )
}


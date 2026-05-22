/* eslint-disable react-refresh/only-export-components */
import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './App.css'
import { readResponsePayload } from './shared.js'

const FIXED_COD_ANIO_BASICA = '13'
const PAYMENT_RECEIPT_EMAIL = 'DeptCobranzas@intec.edu.ec'

const onlyActiveCourses = (courses) => (courses || []).filter((course) => course.es_activo !== false)

function InscriptionPage() {
  const removeNumbersFromLabel = (value) =>
    String(value || '')
      .replace(/[0-9]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()

  const [registrationForm, setRegistrationForm] = useState({
    nombre: '',
    cedula: '',
    email: '',
    telefono: '',
    localidad: '',
    direccion: '',
    ocupacion: '',
    empresa: '',
    carrera_num: '',
    cod_anio_basica: '',
    codigo_materia: '',
    codigo_periodo: '',
    estado_periodo: '',
    matricula: '',
    monto: '',
    descripcion: 'Pago de inscripcion',
    dataTreatment: '',
  })
  const [isRegistrationSubmitting, setIsRegistrationSubmitting] = useState(false)
  const [registrationErrorMessage, setRegistrationErrorMessage] = useState('')
  const [registrationResult, setRegistrationResult] = useState(null)
  const [, setIsMatriculaLoading] = useState(true)
  const [catalogs, setCatalogs] = useState({
    carreras: [],
    periodos: [],
    cursos_por_carrera: {},
  })
  const [isCatalogsLoading, setIsCatalogsLoading] = useState(true)

  useEffect(() => {
    let isMounted = true

    async function loadMatricula() {
      try {
        const response = await fetch('/api/auth/inscription/matricula/', {
          cache: 'no-store',
        })
        const payload = await readResponsePayload(response)

        if (!payload || !response.ok || !payload.ok || !payload.matricula) {
          throw new Error(payload?.message ?? 'No fue posible generar la matricula unica.')
        }

        if (!isMounted) {
          return
        }

        setRegistrationForm((current) => ({
          ...current,
          matricula: String(payload.matricula),
        }))
      } catch (error) {
        if (isMounted) {
          setRegistrationErrorMessage(error.message)
        }
      } finally {
        if (isMounted) {
          setIsMatriculaLoading(false)
        }
      }
    }

    loadMatricula()

    return () => {
      isMounted = false
    }
  }, [])

  useEffect(() => {
    let isMounted = true

    async function loadCatalogs() {
      try {
        const response = await fetch('/api/auth/inscription/catalogs/', {
          cache: 'no-store',
        })
        const payload = await readResponsePayload(response)

        if (!payload || !response.ok || !payload.ok || !payload.catalogs) {
          throw new Error(payload?.message ?? 'No fue posible cargar carreras, cursos y periodos.')
        }

        if (!isMounted) {
          return
        }

        const loadedCatalogs = payload.catalogs
        setCatalogs(loadedCatalogs)

        const fixedCareer = (loadedCatalogs.carreras || []).find(
          (item) => String(item.cod_anio_basica) === FIXED_COD_ANIO_BASICA,
        )
        const activeCareer = fixedCareer || (loadedCatalogs.carreras || []).find((item) => item.es_activo)
        const activePeriod = (loadedCatalogs.periodos || []).find((item) => item.es_activo)
        const careerCourses = activeCareer
          ? onlyActiveCourses(loadedCatalogs.cursos_por_carrera?.[String(activeCareer.cod_anio_basica)])
          : []
        const defaultCourse = careerCourses[0]

        setRegistrationForm((current) => ({
          ...current,
          carrera_num: activeCareer?.num ? String(activeCareer.num) : current.carrera_num,
          cod_anio_basica: activeCareer?.cod_anio_basica
            ? String(activeCareer.cod_anio_basica)
            : current.cod_anio_basica,
          codigo_materia: defaultCourse?.codigo_materia
            ? String(defaultCourse.codigo_materia)
            : current.codigo_materia,
          monto: defaultCourse?.monto_calculado || current.monto,
          codigo_periodo: activePeriod?.cod_periodo
            ? String(activePeriod.cod_periodo)
            : current.codigo_periodo,
          estado_periodo: activePeriod?.estado || current.estado_periodo,
        }))
      } catch (error) {
        if (isMounted) {
          setRegistrationErrorMessage(error.message)
        }
      } finally {
        if (isMounted) {
          setIsCatalogsLoading(false)
        }
      }
    }

    loadCatalogs()

    return () => {
      isMounted = false
    }
  }, [])

  function handleRegistrationChange(event) {
    const { name, value } = event.target
    if (name === 'carrera_num') {
      const selectedCareer = catalogs.carreras.find((item) => String(item.num) === String(value))
      const codAnio = selectedCareer?.cod_anio_basica ? String(selectedCareer.cod_anio_basica) : ''
      const cursos = onlyActiveCourses(catalogs.cursos_por_carrera?.[codAnio])
      const firstCourse = cursos[0]

      setRegistrationForm((current) => ({
        ...current,
        carrera_num: String(value),
        cod_anio_basica: codAnio,
        codigo_materia: firstCourse?.codigo_materia ? String(firstCourse.codigo_materia) : '',
        monto: firstCourse?.monto_calculado || '',
      }))
      return
    }

    if (name === 'codigo_materia') {
      const courses = onlyActiveCourses(catalogs.cursos_por_carrera?.[registrationForm.cod_anio_basica])
      const selectedCourse = courses.find(
        (item) => String(item.codigo_materia) === String(value),
      )
      setRegistrationForm((current) => ({
        ...current,
        codigo_materia: String(value),
        monto: selectedCourse?.monto_calculado || '',
      }))
      return
    }

    if (name === 'codigo_periodo') {
      const selectedPeriod = catalogs.periodos.find(
        (item) => String(item.cod_periodo) === String(value),
      )
      setRegistrationForm((current) => ({
        ...current,
        codigo_periodo: String(value),
        estado_periodo: selectedPeriod?.estado || '',
      }))
      return
    }

    setRegistrationForm((current) => ({
      ...current,
      [name]: value,
    }))
  }

  const fixedCareer = catalogs.carreras.find(
    (item) => String(item.cod_anio_basica) === FIXED_COD_ANIO_BASICA,
  )
  const careerLocked = Boolean(fixedCareer)
  const activePeriods = catalogs.periodos.filter((period) => period.es_activo)
  const fixedActivePeriod = activePeriods[0] || null
  const periodLocked = Boolean(fixedActivePeriod)
  const activeCoursesForSelectedCareer = onlyActiveCourses(
    catalogs.cursos_por_carrera?.[registrationForm.cod_anio_basica],
  )
  const automaticSelectedCourse =
    activeCoursesForSelectedCareer.find(
      (item) => String(item.codigo_materia) === String(registrationForm.codigo_materia),
    ) ||
    activeCoursesForSelectedCareer[0] ||
    null
  const canSubmitRegistration = registrationForm.dataTreatment === 'si' && !isRegistrationSubmitting

  async function handleRegistrationSubmit(event) {
    event.preventDefault()
    setIsRegistrationSubmitting(true)
    setRegistrationErrorMessage('')
    setRegistrationResult(null)

    if (registrationForm.dataTreatment !== 'si') {
      setRegistrationErrorMessage(
        'Para completar la inscripcion debes aceptar el tratamiento de datos personales.',
      )
      setIsRegistrationSubmitting(false)
      return
    }

    if (!registrationForm.matricula) {
      setRegistrationErrorMessage('No se pudo generar la matricula unica. Recarga la pagina.')
      setIsRegistrationSubmitting(false)
      return
    }

    const courses = onlyActiveCourses(catalogs.cursos_por_carrera?.[registrationForm.cod_anio_basica])
    const selectedCourse =
      courses.find((item) => String(item.codigo_materia) === String(registrationForm.codigo_materia)) ||
      courses[0]

    if (!selectedCourse) {
      setRegistrationErrorMessage('No hay un curso activo disponible para completar la inscripcion.')
      setIsRegistrationSubmitting(false)
      return
    }

    const selectedCourseCode = String(selectedCourse.codigo_materia)
    const cleanedCourseName = String(selectedCourse?.nombre_materia || '')
      .replace(/[0-9]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
    const paymentDescription = cleanedCourseName
      ? `Pago de inscripcion del curso ${cleanedCourseName}`
      : 'Pago de inscripcion'

    try {
      const response = await fetch('/api/auth/inscription/payment-link/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          nombre: registrationForm.nombre.trim(),
          cedula: registrationForm.cedula,
          email: registrationForm.email,
          telefono: registrationForm.telefono,
          direccion: registrationForm.direccion,
          matricula: registrationForm.matricula,
          monto: registrationForm.monto ? Number(registrationForm.monto) : null,
          descripcion: paymentDescription,
          nombre_materia: selectedCourse?.nombre_materia || '',
          carrera_num: registrationForm.carrera_num,
          cod_anio_basica: registrationForm.cod_anio_basica,
          codigo_materia: selectedCourseCode,
          codigo_periodo: registrationForm.codigo_periodo,
          estado_periodo: registrationForm.estado_periodo,
          data_treatment_accepted: true,
          provider_payload: {
            tipo: 'inscripcion',
            nombre: registrationForm.nombre.trim(),
            cedula: registrationForm.cedula,
            email: registrationForm.email,
            telefono: registrationForm.telefono,
            localidad: registrationForm.localidad,
            direccion: registrationForm.direccion,
            ocupacion: registrationForm.ocupacion,
            empresa: registrationForm.empresa,
            matricula: registrationForm.matricula,
            monto: registrationForm.monto ? Number(registrationForm.monto) : null,
            descripcion: paymentDescription,
            nombre_materia: selectedCourse?.nombre_materia || '',
            carrera_num: registrationForm.carrera_num,
            cod_anio_basica: registrationForm.cod_anio_basica,
            codigo_materia: selectedCourseCode,
            codigo_periodo: registrationForm.codigo_periodo,
            estado_periodo: registrationForm.estado_periodo,
          },
        }),
      })

      const payload = await readResponsePayload(response)
      if (!payload) {
        throw new Error(`El servidor devolvio una respuesta vacia (${response.status}).`)
      }

      if (!response.ok || !payload.ok) {
        throw new Error(
          payload.message ??
            `No fue posible finalizar la inscripcion (${response.status}).`,
        )
      }

      setRegistrationResult(payload)
      setRegistrationForm((current) => ({
        ...current,
        dataTreatment: 'si',
      }))
    } catch (error) {
      setRegistrationErrorMessage(error.message)
    } finally {
      setIsRegistrationSubmitting(false)
    }
  }

  const receiptEmail =
    registrationResult?.receipt_email || registrationResult?.email_result?.receipt_email || PAYMENT_RECEIPT_EMAIL

  return (
    <main className="inscription-fullscreen">
      <section className="inscription-centered">
          <div className="auth-card lookup-mode inscription-card">
          <img
            className="inscription-logo"
            src="/Intec-Logowithslogangray.svg"
            alt="INTEC"
          />

          <span className="eyebrow">Registro de inscripción</span>
          <h2>Formulario de inscripción</h2>
          <p className="auth-intro">
            Registro de Inscripción para estudiantes del INTEC. Completa el formulario para generar tu enlace de pago y finalizar tu inscripción.
          </p>

            <section className="registration-box">
              <div className="registration-box-header">
                <h3>Registro de Estudiante</h3>
              <p>
                  Por favor complete sus datos personales para ser parte de esta experiencia.
              </p>
            </div>

              <form className="auth-form" onSubmit={handleRegistrationSubmit}>
                <div className="registration-row-group">
                  <div className="registration-grid registration-grid-4">
                    <label className="field">
                      <span>Curso en: *</span>
                      <select
                        name="carrera_num"
                        value={registrationForm.carrera_num}
                        onChange={handleRegistrationChange}
                        required
                        disabled={isCatalogsLoading || careerLocked}
                      >
                        <option value="">Selecciona el Curso</option>
                        {catalogs.carreras.map((career) => (
                          <option key={career.num} value={career.num}>
                            {removeNumbersFromLabel(career.nombre_basica)}
                          </option>
                        ))}
                      </select>
                    </label>

                    <label className="field readonly-field">
                      <span>Curso a seguir *</span>
                      <input
                        type="text"
                        value={
                          automaticSelectedCourse
                            ? removeNumbersFromLabel(automaticSelectedCourse.nombre_materia)
                            : ''
                        }
                        placeholder={
                          isCatalogsLoading
                            ? 'Cargando curso activo'
                            : 'No hay curso activo disponible'
                        }
                        readOnly
                        required
                      />
                      <input name="codigo_materia" type="hidden" value={registrationForm.codigo_materia} readOnly />
                    </label>

                    <label className="field">
                      <span>Período *</span>
                      <select
                        name="codigo_periodo"
                        value={registrationForm.codigo_periodo}
                        onChange={handleRegistrationChange}
                        required
                        disabled={isCatalogsLoading || periodLocked}
                      >
                        <option value="">Selecciona un período</option>
                        {(fixedActivePeriod ? [fixedActivePeriod] : [])
                          .map((period) => (
                            <option key={period.cod_periodo} value={period.cod_periodo}>
                              {removeNumbersFromLabel(period.detalle_periodo)}
                            </option>
                          ))}
                      </select>
                    </label>

                  </div>
                </div>

                <div className="registration-row-group">
                  <div className="registration-grid">
                    <label className="field">
                    <span>Nombre completo *</span>
                      <input
                        name="nombre"
                        type="text"
                      value={registrationForm.nombre}
                      onChange={handleRegistrationChange}
                      placeholder="Nombre completo"
                      required
                    />
                  </label>

                    <label className="field">
                      <span>Cédula *</span>
                      <input
                        name="cedula"
                        type="text"
                        inputMode="numeric"
                        pattern="[0-9]{6,20}"
                        title="Ingresa una cédula numérica válida"
                        value={registrationForm.cedula}
                        onChange={handleRegistrationChange}
                        placeholder="Ej. 00123456789"
                        required
                      />
                    </label>
                  </div>
                </div>

                <div className="registration-row-group">
                  <div className="registration-grid">
                    <label className="field">
                    <span>Correo Electrónico *</span>
                      <input
                        name="email"
                        type="email"
                      value={registrationForm.email}
                      onChange={handleRegistrationChange}
                    placeholder="correo@dominio.com"
                    autoComplete="email"
                      required
                      />
                    </label>

                    <label className="field">
                    <span>Número de Teléfono *</span>
                      <input
                      name="telefono"
                      type="text"
                      value={registrationForm.telefono}
                      onChange={handleRegistrationChange}
                      placeholder="Telefono"
                      required
                      />
                    </label>
                  </div>
                </div>

                <div className="registration-row-group">
                  <div className="registration-grid">
                    <label className="field">
                    <span>Localidad *</span>
                    <input
                      name="localidad"
                      type="text"
                      value={registrationForm.localidad}
                      onChange={handleRegistrationChange}
                      placeholder="Localidad"
                      required
                    />
                  </label>

                  <label className="field">
                    <span>Dirección *</span>
                    <input
                      name="direccion"
                      type="text"
                      value={registrationForm.direccion}
                      onChange={handleRegistrationChange}
                      placeholder="Dirección"
                      required
                    />
                  </label>
                  </div>
                </div>

                <div className="registration-row-group">
                  <div className="registration-grid">
                    <label className="field">
                    <span>Ocupación</span>
                    <input
                      name="ocupacion"
                      type="text"
                      value={registrationForm.ocupacion}
                      onChange={handleRegistrationChange}
                      placeholder="Ocupación"
                    />
                  </label>

                  <label className="field">
                    <span>Empresa</span>
                    <input
                      name="empresa"
                      type="text"
                      value={registrationForm.empresa}
                      onChange={handleRegistrationChange}
                      placeholder="Empresa"
                    />
                  </label>
                  </div>
                </div>

                <div className="registration-row-group">
                  <div className="registration-grid">
                  <label className="field readonly-field">
                    <span>Monto *</span>
                    <input
                      name="monto"
                      type="number"
                      min="0"
                      step="0.01"
                      value={registrationForm.monto}
                      placeholder="Monto calculado automáticamente"
                      readOnly
                      disabled
                      required
                    />
                  </label>
                  </div>
                </div>

                <div className="inscription-payment-notice">
                  <p>
                    Luego de realizar el pago, envia el comprobante a{' '}
                    <a href={`mailto:${PAYMENT_RECEIPT_EMAIL}`}>{PAYMENT_RECEIPT_EMAIL}</a>{' '}
                    indicando tu nombre completo y Cedula de ciudadania.
                  </p>
                </div>

                <section className="consent-box">
                  <p>
                    Autorizo de forma libre, previa y expresa el tratamiento de mis datos
                    personales para fines de inscripción, gestión académica y contacto institucional.
                  </p>
                  <div className="consent-options">
                    <label>
                      <input
                        type="radio"
                        name="dataTreatment"
                        value="si"
                        checked={registrationForm.dataTreatment === 'si'}
                        onChange={handleRegistrationChange}
                      />
                      Acepto
                    </label>
                    <label>
                      <input
                        type="radio"
                        name="dataTreatment"
                        value="no"
                        checked={registrationForm.dataTreatment === 'no'}
                        onChange={handleRegistrationChange}
                      />
                      No acepto
                    </label>
                  </div>
                </section>

                {registrationErrorMessage ? <p className="form-error">{registrationErrorMessage}</p> : null}

                <button
                  className="submit-button"
                  type="submit"
                  disabled={!canSubmitRegistration}
                >
                  {isRegistrationSubmitting ? 'Registro de Inscripcion...' : 'Registro de Inscripcion'}
              </button>
            </form>

              {registrationResult ? (
              <div className="payment-result">
                  <p className="payment-result-title">Inscripcion completada</p>
                {registrationResult.payment_link ? (
                  <a className="payment-result-link" href={registrationResult.payment_link} target="_blank" rel="noreferrer">
                    {registrationResult.payment_link}
                  </a>
                ) : null}
                <p className="payment-result-note">
                    {registrationResult.email_result?.message ?? 'Correo enviado correctamente.'}
                </p>
                <p className="payment-result-note payment-result-receipt">
                  Luego de realizar el pago, envia el comprobante a{' '}
                  <a href={`mailto:${receiptEmail}`}>{receiptEmail}</a> indicando tu nombre completo y Cedula de ciudadania.
                </p>
              </div>
            ) : null}
          </section>

        </div>
      </section>
    </main>
  )
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <InscriptionPage />
  </StrictMode>,
)

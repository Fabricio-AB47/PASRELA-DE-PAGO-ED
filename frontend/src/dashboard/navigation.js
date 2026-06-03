export const DASHBOARD_ROUTES = [
  {
    id: 'home',
    hash: '#dashboard',
    label: 'Dashboard',
    title: 'Dashboard general',
    description: 'Selecciona una opción para administrar procesos institucionales.',
  },
  {
    id: 'academic',
    hash: '#academic',
    aliases: ['#admin-academic'],
    label: 'Carreras y pensum',
    title: 'Carreras y pensum',
    description: 'Administra carreras, materias, estados y filtros académicos.',
  },
  {
    id: 'course-cuts',
    hash: '#course-cuts',
    aliases: ['#admin-course-cuts'],
    label: 'Cortes',
    title: 'Cortes de inscripción',
    description: 'Abre y cierra cortes para organizar matrículas, certificados y cargas Excel.',
  },
  {
    id: 'payments',
    hash: '#payments',
    aliases: ['#admin-payments'],
    label: 'Pagos',
    title: 'Pagos',
    description: 'Consulta transacciones y ejecuta anulaciones desde un módulo independiente.',
  },
  {
    id: 'bulk-enrollment',
    hash: '#bulk-enrollment',
    aliases: ['#admin-bulk-enrollment'],
    label: 'Matrícula académica',
    title: 'Matrícula académica',
    description: 'Procesa estudiantes por Excel o selección, matricula materias y envía credenciales INTEC.',
  },
]

export function routeFromHash(hash) {
  const normalizedHash = hash || '#dashboard'
  const route = DASHBOARD_ROUTES.find(
    (item) => item.hash === normalizedHash || item.aliases?.includes(normalizedHash),
  )
  return route?.id || 'home'
}

export function routeById(routeId) {
  return DASHBOARD_ROUTES.find((item) => item.id === routeId) || DASHBOARD_ROUTES[0]
}

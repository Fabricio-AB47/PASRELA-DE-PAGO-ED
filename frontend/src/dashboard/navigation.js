export const DASHBOARD_ROUTES = [
  {
    id: 'home',
    hash: '#dashboard',
    label: 'Dashboard',
    title: 'Dashboard general',
    description: 'Selecciona una opcion para administrar procesos institucionales.',
  },
  {
    id: 'academic',
    hash: '#academic',
    aliases: ['#admin-academic'],
    label: 'Carreras y pensum',
    title: 'Carreras y pensum',
    description: 'Administra carreras, materias, estados y filtros academicos.',
  },
  {
    id: 'payments',
    hash: '#payments',
    aliases: ['#admin-payments'],
    label: 'Pagos',
    title: 'Pagos',
    description: 'Consulta transacciones y ejecuta anulaciones desde un modulo independiente.',
  },
  {
    id: 'bulk-enrollment',
    hash: '#bulk-enrollment',
    aliases: ['#admin-bulk-enrollment'],
    label: 'Matricula masiva',
    title: 'Matricula masiva',
    description: 'Carga estudiantes desde Excel para inscribirlos con carrera, curso y periodo comunes.',
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

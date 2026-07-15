import { ECUADOR_SEPS_INSTITUTIONS_1 } from './ecuadorFinancialInstitutionsPart1.js'
import { ECUADOR_SEPS_INSTITUTIONS_2 } from './ecuadorFinancialInstitutionsPart2.js'
import { ECUADOR_SEPS_INSTITUTIONS_3 } from './ecuadorFinancialInstitutionsPart3.js'
import { ECUADOR_SEPS_INSTITUTIONS_4 } from './ecuadorFinancialInstitutionsPart4.js'
import { ECUADOR_SEPS_INSTITUTIONS_5 } from './ecuadorFinancialInstitutionsPart5.js'

// Catálogos oficiales: Superintendencia de Bancos y SEPS (actualización SEPS: 1 de junio de 2026).
const ECUADOR_BANKS = [
  {
    "name": "BANCO AMAZONAS S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO DE LA PRODUCCION S.A. - PRODUBANCO",
    "type": "Banco"
  },
  {
    "name": "BANCO DEL AUSTRO S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO SOLIDARIO S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO GUAYAQUIL S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO SUDAMERICANO S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO BOLIVARIANO C.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO COOPNACIONAL S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO DE MANABI S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO PROCREDIT S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO DEL LITORAL S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO CAPITAL S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO GENERAL RUMIÑAHUI S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO DELBANK S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO INTERNACIONAL S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO ATLANTIDA S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO DE LOJA S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO DESARROLLO DE LOS PUEBLOS S.A. - CODESARROLLO",
    "type": "Banco"
  },
  {
    "name": "BANCO DE MACHALA S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO VISIONFUND ECUADOR S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO DEL PACIFICO S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO DINERS CLUB DEL ECUADOR S.A.",
    "type": "Banco"
  },
  {
    "name": "BANCO PICHINCHA C.A.",
    "type": "Banco"
  },
  {
    "name": "CITIBANK, N.A. SUCURSAL ECUADOR",
    "type": "Banco"
  },
  {
    "name": "BANCO DE DESARROLLO DEL ECUADOR B.P.",
    "type": "Banco"
  },
  {
    "name": "CORPORACION FINANCIERA NACIONAL B.P.",
    "type": "Banco"
  },
  {
    "name": "BANCO DEL INSTITUTO ECUATORIANO DE SEGURIDAD SOCIAL - BIESS",
    "type": "Banco"
  },
  {
    "name": "BANECUADOR B.P.",
    "type": "Banco"
  }
]

export const ECUADOR_FINANCIAL_INSTITUTIONS = [
  ...ECUADOR_BANKS,
  ...ECUADOR_SEPS_INSTITUTIONS_1,
  ...ECUADOR_SEPS_INSTITUTIONS_2,
  ...ECUADOR_SEPS_INSTITUTIONS_3,
  ...ECUADOR_SEPS_INSTITUTIONS_4,
  ...ECUADOR_SEPS_INSTITUTIONS_5,
]

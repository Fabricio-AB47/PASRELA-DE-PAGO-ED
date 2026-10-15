import assert from 'node:assert/strict'
import { test } from 'node:test'
import { editProtectedMailText, protectedMailText } from './protectedMailText.js'

const template = 'Hola {nombre}, en {curso} adeuda USD {saldo}. Gracias.'
const values = { nombre: 'Ana Pérez', curso: 'Excel', saldo: '25.50' }

test('allows prose changes in a single control and preserves template fields', () => {
  const { text } = protectedMailText(template, values)
  assert.equal(editProtectedMailText(template, text.replace('Gracias.', 'Comuníquese con Finanzas.'), values),
    template.replace('Gracias.', 'Comuníquese con Finanzas.'))
  assert.equal(editProtectedMailText(template, text.replace('Hola ', 'Estimada '), values),
    template.replace('Hola ', 'Estimada '))
})

test('blocks modification, deletion, replacement and cut across automatic values', () => {
  const { text } = protectedMailText(template, values)
  for (const next of [text.replace('25.50', '0.00'), text.replace('Ana Pérez', ''),
    text.replace('Excel', 'Word'), '', text.replace('Pérez, en Excel', 'otro curso'),
    text.replace('Ana Pérez', 'Ana María Pérez')]) {
    assert.equal(editProtectedMailText(template, next, values), null)
  }
})

test('protects unresolved tokens and prevents adding new tokens through paste', () => {
  assert.equal(editProtectedMailText(template, template.replace('{saldo}', '99')), null)
  assert.equal(editProtectedMailText(template, template.replace('{nombre}', '{correo}')), null)
  assert.equal(editProtectedMailText(template, template + ' {correo}'), null)
})

test('allows insertions at field boundaries and restores source offsets after multiple fields', () => {
  const { text } = protectedMailText(template, values)
  assert.equal(editProtectedMailText(template, text.replace('Ana Pérez', 'Sra. Ana Pérez'), values),
    template.replace('{nombre}', 'Sra. {nombre}'))
  assert.equal(editProtectedMailText(template, text.replace('Excel', 'Excel avanzado'), values),
    template.replace('{curso}', '{curso} avanzado'))
  assert.equal(editProtectedMailText(template, text.replace(' adeuda USD ', ' mantiene un saldo de USD '), values),
    template.replace(' adeuda USD ', ' mantiene un saldo de USD '))
})

test('keeps line breaks, unchanged values, empty fields and repeated tokens safe', () => {
  const source = 'Hola {nombre}\n{nombre}\n{estado_pagos}\nFin'
  const display = protectedMailText(source, { nombre: 'Ana', estado_pagos: '' })
  assert.equal(display.text, 'Hola Ana\nAna\n{estado_pagos}\nFin')
  assert.equal(editProtectedMailText(source, display.text, { nombre: 'Ana', estado_pagos: '' }), source)
  assert.equal(editProtectedMailText(source, display.text.replace('Fin', 'Atentamente'), { nombre: 'Ana', estado_pagos: '' }),
    source.replace('Fin', 'Atentamente'))
})

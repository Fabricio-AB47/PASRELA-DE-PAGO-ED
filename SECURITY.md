# Seguridad y conciliación de pagos

## Variables obligatorias de producción

- `DEBUG=0`.
- `SECRET_KEY` debe contener al menos 64 caracteres aleatorios y mantenerse fuera de Git.
- Rotar `TOKEN_PAY`, `MS_CLIENT_SECRET` y las contraseñas SQL desde sus proveedores; nunca copiarlos al repositorio.
- Mantener HTTPS obligatorio y cookies seguras según `.env.example`.

## Conciliación automática AllDigital

El dashboard valida los enlaces pendientes como máximo una vez por cada intervalo definido en
`PAYMENTS_AUTO_RECONCILE_SECONDS`. La consulta usa `GET {PAYMENTS_API_URL}/{transaction_id}`.

Para que la conciliación no dependa de que un usuario abra el dashboard, programar cada minuto en
el Programador de tareas de Windows, desde la raíz del proyecto:

```powershell
.\.venv\Scripts\python.exe backend\E_Cont\manage.py reconcile_payments --limit 50
```

Antes de aplicar un pago se validan el identificador de transacción, la cédula, el monto y la moneda.
El registro en `INTECEDUCONTINUA` es idempotente: una transacción ya aplicada no vuelve a generar
otro movimiento. Los errores se conservan para reintento y deben supervisarse en el log del servidor.

## Contraseñas heredadas

El autenticador admite hashes de Django y mantiene compatibilidad temporal con las contraseñas
heredadas. La migración definitiva requiere ampliar las columnas de contraseña y convertir los
registros a Argon2id o PBKDF2, coordinando previamente cualquier otro sistema que utilice las mismas
tablas. Una vez migrados todos los registros debe retirarse la comparación heredada.

## Repositorio

`.env`, entornos virtuales, logs, comprobantes y archivos generados no deben versionarse. Si ya están
en el índice histórico, retirarlos con `git rm --cached` y revisar el historial antes de publicar.

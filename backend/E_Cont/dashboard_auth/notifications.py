from __future__ import annotations

import json
import logging
from typing import Any

from django.db import connection

from .continuing_education import complement_database_name


class NotificationStorageError(Exception):
    pass


logger = logging.getLogger(__name__)
_schema_ready_for = ''


def create_notification(
    *,
    event_key: str,
    notification_type: str,
    title: str,
    message: str,
    recipient_category: str = '',
    recipient_login: str = '',
    recipient_role: str = '',
    route: str = '',
    data: dict[str, Any] | None = None,
) -> bool:
    notification_table, _ = _ensure_notification_schema()
    clean_event_key = _trim(event_key, 180)
    if not clean_event_key:
        return False
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            DECLARE @Created bit = 0;
            IF NOT EXISTS (SELECT 1 FROM {notification_table} WHERE ClaveEvento = %s)
            BEGIN
                INSERT INTO {notification_table} (
                    ClaveEvento, Tipo, Titulo, Mensaje, DestinatarioCategoria,
                    DestinatarioLogin, DestinatarioRol, Ruta, DatosJson
                )
                VALUES (%s, %s, %s, %s, NULLIF(%s, ''), NULLIF(%s, ''), NULLIF(%s, ''), NULLIF(%s, ''), %s)
                SET @Created = 1;
            END
            SELECT @Created AS CreatedRows
            """,
            [
                clean_event_key,
                clean_event_key,
                _trim(notification_type, 50).upper() or 'GENERAL',
                _trim(title, 160),
                _trim(message, 700),
                _trim(recipient_category, 30).lower(),
                _trim(recipient_login, 255).lower(),
                _trim(recipient_role, 80).upper(),
                _trim(route, 255),
                json.dumps(data or {}, ensure_ascii=False, default=str),
            ],
        )
        row = cursor.fetchone()
    return bool(row and row[0])


def create_notification_safely(**kwargs: Any) -> bool:
    try:
        return create_notification(**kwargs)
    except Exception as exc:
        logger.warning('No fue posible persistir la notificación en INTECEDUCONTINUA: %s', exc)
        return False


def list_notifications(user: dict[str, Any], *, limit: Any = 30) -> dict[str, Any]:
    notification_table, reading_table = _ensure_notification_schema()
    identity = _notification_identity(user)
    safe_limit = max(1, min(_safe_int(limit, 30), 100))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT TOP ({safe_limit})
                CONVERT(varchar(50), N.NotificacionId) AS notification_id,
                N.Tipo AS type,
                N.Titulo AS title,
                N.Mensaje AS message,
                N.Ruta AS route,
                N.DatosJson AS data_json,
                CONVERT(varchar(19), N.FechaCreacion, 120) AS created_at,
                CASE WHEN L.NotificacionId IS NULL THEN 0 ELSE 1 END AS is_read
            FROM {notification_table} AS N
            LEFT JOIN {reading_table} AS L
              ON L.NotificacionId = N.NotificacionId AND L.UsuarioClave = %s
            WHERE (N.DestinatarioCategoria IS NULL OR LOWER(N.DestinatarioCategoria) = %s)
              AND (
                  N.DestinatarioLogin IS NULL
                  OR LOWER(N.DestinatarioLogin) = %s
                  OR LOWER(N.DestinatarioLogin) = %s
              )
              AND (N.DestinatarioRol IS NULL OR UPPER(N.DestinatarioRol) = %s)
            ORDER BY N.FechaCreacion DESC, N.NotificacionId DESC
            """,
            [
                identity['user_key'], identity['category'], identity['login'],
                identity['email'], identity['role'],
            ],
        )
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    items = []
    for row in rows:
        try:
            notification_data = json.loads(row.get('data_json') or '{}')
        except (TypeError, json.JSONDecodeError):
            notification_data = {}
        items.append({
            'id': str(row.get('notification_id') or ''),
            'type': str(row.get('type') or ''),
            'title': str(row.get('title') or ''),
            'message': str(row.get('message') or ''),
            'route': str(row.get('route') or ''),
            'data': notification_data,
            'created_at': str(row.get('created_at') or ''),
            'is_read': bool(row.get('is_read')),
        })
    return {
        'items': items,
        'unread_count': len([item for item in items if not item['is_read']]),
        'source_database': complement_database_name(),
    }


def mark_notifications_read(user: dict[str, Any], notification_ids: list[Any] | None = None) -> int:
    notification_table, reading_table = _ensure_notification_schema()
    identity = _notification_identity(user)
    clean_ids = [int(value) for value in (notification_ids or []) if str(value).strip().isdigit()]
    with connection.cursor() as cursor:
        if clean_ids:
            placeholders = ','.join(['%s'] * len(clean_ids))
            cursor.execute(
                f"""
                INSERT INTO {reading_table} (NotificacionId, UsuarioClave)
                SELECT N.NotificacionId, %s
                FROM {notification_table} AS N
                WHERE N.NotificacionId IN ({placeholders})
                  AND (N.DestinatarioCategoria IS NULL OR LOWER(N.DestinatarioCategoria) = %s)
                  AND (N.DestinatarioLogin IS NULL OR LOWER(N.DestinatarioLogin) IN (%s, %s))
                  AND (N.DestinatarioRol IS NULL OR UPPER(N.DestinatarioRol) = %s)
                  AND NOT EXISTS (
                      SELECT 1 FROM {reading_table} L
                      WHERE L.NotificacionId = N.NotificacionId AND L.UsuarioClave = %s
                  )
                """,
                [
                    identity['user_key'], *clean_ids, identity['category'], identity['login'],
                    identity['email'], identity['role'], identity['user_key'],
                ],
            )
        else:
            cursor.execute(
                f"""
                INSERT INTO {reading_table} (NotificacionId, UsuarioClave)
                SELECT N.NotificacionId, %s
                FROM {notification_table} AS N
                WHERE (N.DestinatarioCategoria IS NULL OR LOWER(N.DestinatarioCategoria) = %s)
                  AND (N.DestinatarioLogin IS NULL OR LOWER(N.DestinatarioLogin) IN (%s, %s))
                  AND (N.DestinatarioRol IS NULL OR UPPER(N.DestinatarioRol) = %s)
                  AND NOT EXISTS (
                      SELECT 1 FROM {reading_table} L
                      WHERE L.NotificacionId = N.NotificacionId AND L.UsuarioClave = %s
                  )
                """,
                [
                    identity['user_key'], identity['category'], identity['login'], identity['email'],
                    identity['role'], identity['user_key'],
                ],
            )
        return max(0, cursor.rowcount)


def notification_storage_status() -> dict[str, Any]:
    notification_table, reading_table = _ensure_notification_schema()
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                (SELECT COUNT(*) FROM {notification_table}) AS notification_count,
                (SELECT COUNT(*) FROM {reading_table}) AS reading_count
            """
        )
        row = cursor.fetchone() or (0, 0)
    return {
        'available': True,
        'database': complement_database_name(),
        'schema': 'noti',
        'notification_count': int(row[0] or 0),
        'reading_count': int(row[1] or 0),
    }


def _notification_identity(user: dict[str, Any]) -> dict[str, str]:
    category = str(user.get('category') or '').strip().lower()
    login = str(user.get('login') or '').strip().lower()
    email = str(user.get('email') or '').strip().lower()
    role = user.get('role') if isinstance(user.get('role'), dict) else {}
    role_name = str(role.get('name') or '').strip().upper()
    return {
        'category': category,
        'login': login,
        'email': email,
        'role': role_name,
        'user_key': f'{category}:{login or email}',
    }


def _ensure_notification_schema() -> tuple[str, str]:
    global _schema_ready_for
    database = complement_database_name()
    notification_table = f'[{database}].[noti].[Notificacion]'
    reading_table = f'[{database}].[noti].[NotificacionLectura]'
    if _schema_ready_for == database:
        return notification_table, reading_table

    with connection.cursor() as cursor:
        cursor.execute('SELECT DB_ID(%s)', [database])
        row = cursor.fetchone()
        if not row or row[0] is None:
            raise NotificationStorageError(
                f'La base complementaria {database} no existe; no se pueden almacenar notificaciones.'
            )
        escaped_database = database.replace(']', ']]')
        schema_ddl = f"""
            USE [{escaped_database}];
            IF SCHEMA_ID('noti') IS NULL EXEC(N'CREATE SCHEMA [noti]');
            IF OBJECT_ID('noti.Notificacion', 'U') IS NULL
            BEGIN
                CREATE TABLE noti.Notificacion (
                    NotificacionId BIGINT IDENTITY(1,1) NOT NULL,
                    ClaveEvento NVARCHAR(180) NOT NULL,
                    Tipo NVARCHAR(50) NOT NULL,
                    Titulo NVARCHAR(160) NOT NULL,
                    Mensaje NVARCHAR(700) NOT NULL,
                    DestinatarioCategoria NVARCHAR(30) NULL,
                    DestinatarioLogin NVARCHAR(255) NULL,
                    DestinatarioRol NVARCHAR(80) NULL,
                    Ruta NVARCHAR(255) NULL,
                    DatosJson NVARCHAR(MAX) NULL,
                    FechaCreacion DATETIME2(0) NOT NULL CONSTRAINT DF_NOTI_NOTIFICACION_FECHA DEFAULT SYSDATETIME(),
                    CONSTRAINT PK_NOTI_NOTIFICACION PRIMARY KEY (NotificacionId),
                    CONSTRAINT UX_NOTI_NOTIFICACION_EVENTO UNIQUE (ClaveEvento)
                );
                CREATE INDEX IX_NOTI_NOTIFICACION_DESTINO_FECHA
                    ON noti.Notificacion (DestinatarioCategoria, DestinatarioLogin, DestinatarioRol, FechaCreacion DESC);
            END;
            IF OBJECT_ID('noti.NotificacionLectura', 'U') IS NULL
            BEGIN
                CREATE TABLE noti.NotificacionLectura (
                    NotificacionId BIGINT NOT NULL,
                    UsuarioClave NVARCHAR(320) NOT NULL,
                    FechaLectura DATETIME2(0) NOT NULL CONSTRAINT DF_NOTI_LECTURA_FECHA DEFAULT SYSDATETIME(),
                    CONSTRAINT PK_NOTI_LECTURA PRIMARY KEY (NotificacionId, UsuarioClave),
                    CONSTRAINT FK_NOTI_LECTURA_NOTIFICACION FOREIGN KEY (NotificacionId)
                        REFERENCES noti.Notificacion(NotificacionId) ON DELETE CASCADE
                );
                CREATE INDEX IX_NOTI_LECTURA_USUARIO_FECHA
                    ON noti.NotificacionLectura (UsuarioClave, FechaLectura DESC);
            END;
            """
        cursor.execute('EXEC sys.sp_executesql %s', [schema_ddl])
        _migrate_legacy_notifications(cursor, database, notification_table, reading_table)
    _schema_ready_for = database
    return notification_table, reading_table


def _migrate_legacy_notifications(cursor: Any, database: str, notification_table: str, reading_table: str) -> None:
    cursor.execute('SELECT DB_NAME()')
    current_database_row = cursor.fetchone()
    current_database = str(current_database_row[0] or '') if current_database_row else ''
    if current_database.lower() == database.lower():
        return
    cursor.execute(
        f"""
        IF OBJECT_ID('dbo.SISTEMA_NOTIFICACION', 'U') IS NOT NULL
        BEGIN
            INSERT INTO {notification_table} (
                ClaveEvento, Tipo, Titulo, Mensaje, DestinatarioCategoria,
                DestinatarioLogin, DestinatarioRol, Ruta, DatosJson, FechaCreacion
            )
            SELECT
                S.ClaveEvento, S.Tipo, S.Titulo, S.Mensaje, S.DestinatarioCategoria,
                S.DestinatarioLogin, S.DestinatarioRol, S.Ruta, S.DatosJson, S.FechaCreacion
            FROM dbo.SISTEMA_NOTIFICACION AS S
            WHERE NOT EXISTS (
                SELECT 1 FROM {notification_table} AS T
                WHERE T.ClaveEvento COLLATE DATABASE_DEFAULT = S.ClaveEvento COLLATE DATABASE_DEFAULT
            );

            IF OBJECT_ID('dbo.SISTEMA_NOTIFICACION_LECTURA', 'U') IS NOT NULL
            BEGIN
                INSERT INTO {reading_table} (NotificacionId, UsuarioClave, FechaLectura)
                SELECT T.NotificacionId, L.UsuarioClave, L.FechaLectura
                FROM dbo.SISTEMA_NOTIFICACION_LECTURA AS L
                INNER JOIN dbo.SISTEMA_NOTIFICACION AS S ON S.NotificacionId = L.NotificacionId
                INNER JOIN {notification_table} AS T
                    ON T.ClaveEvento COLLATE DATABASE_DEFAULT = S.ClaveEvento COLLATE DATABASE_DEFAULT
                WHERE NOT EXISTS (
                    SELECT 1 FROM {reading_table} AS TL
                    WHERE TL.NotificacionId = T.NotificacionId
                      AND TL.UsuarioClave COLLATE DATABASE_DEFAULT = L.UsuarioClave COLLATE DATABASE_DEFAULT
                );
            END;
        END;
        """
    )


def _trim(value: Any, max_length: int) -> str:
    return str(value or '').strip()[:max_length]


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

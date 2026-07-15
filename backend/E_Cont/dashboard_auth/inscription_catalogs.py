from __future__ import annotations

import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import connection


ACTIVE_VALUES = {'a', 'activo', 'active', '1', 'si', 's', 'true'}
FIXED_INSCRIPTION_AMOUNT = Decimal('500.00')
INSCRIPTION_AMOUNT_DISCOUNT = Decimal('0.00')
PENSUM_STATUS_COLUMN_CANDIDATES = (
    'estado_mat',
    'Estado',
)
DEFAULT_PENSUM_STATUS_COLUMN = 'estado_mat'


class AcademicCatalogError(Exception):
    pass


def fetch_inscription_catalogs() -> dict[str, Any]:
    carreras = _fetch_carreras(include_inactive=False)
    periodos = _fetch_periodos()
    cursos_por_carrera = _fetch_cursos_por_carrera(include_inactive=False)
    paralelos_por_materia = _fetch_paralelos_por_materia()
    paralelos = _fetch_paralelos()
    jornadas = _fetch_jornadas()

    return {
        'carreras': carreras,
        'periodos': periodos,
        'cursos_por_carrera': cursos_por_carrera,
        'paralelos_por_materia': paralelos_por_materia,
        'paralelos': paralelos,
        'jornadas': jornadas,
    }


def fetch_admin_academic_catalogs() -> dict[str, Any]:
    carreras = _fetch_carreras(include_inactive=True)
    pensum = _fetch_pensum_rows(include_inactive=True)
    return {
        'carreras': carreras,
        'pensum': pensum,
        'pensum_status_column': get_pensum_status_column(),
    }


def update_carrera_status(payload: dict[str, Any]) -> dict[str, Any]:
    cod_anio_basica = str(payload.get('cod_anio_basica') or '').strip()
    estado = _status_to_db_value(payload.get('estado'))

    if not cod_anio_basica:
        raise AcademicCatalogError('Debes seleccionar una carrera para actualizar su estado.')

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.CARRERAS
            SET Estado = %s
            WHERE LTRIM(RTRIM(CAST(Cod_AnioBasica AS varchar(20)))) = %s
            """,
            [estado, cod_anio_basica],
        )
        updated = cursor.rowcount

    if updated <= 0:
        raise AcademicCatalogError('No se encontró la carrera seleccionada.')

    return {
        'cod_anio_basica': cod_anio_basica,
        'estado': 'Activo' if estado == 'A' else 'Inactivo',
        'es_activo': estado == 'A',
    }


def upsert_pensum_entry(payload: dict[str, Any]) -> dict[str, Any]:
    cod_anio_basica = str(payload.get('cod_anio_basica') or '').strip()
    codigo_materia = str(payload.get('codigo_materia') or '').strip()
    unidad_organiza = str(payload.get('unidad_organiza') or '').strip()
    nombre_materia = str(payload.get('nombre_materia') or '').strip()
    tipo_materia = str(payload.get('tipo_materia') or 'E').strip().upper() or 'E'
    semestre = _safe_int(payload.get('semestre'), default=1)
    orden = _safe_int(payload.get('orden'), default=0)
    creditos = _safe_decimal(payload.get('creditos'), precision='0.01')
    num_malla = _safe_int(payload.get('num_malla'), default=0)
    cod_materia = str(payload.get('cod_materia') or '').strip()
    horas = _safe_int(payload.get('horas'), default=0)
    modalidad_valor = str(payload.get('modalidad_valor') or 'presencial').strip().lower()
    valor_hora = _safe_decimal(payload.get('valor_hora'), precision='0.00001')
    valor_hora_virtual = _safe_decimal(payload.get('valor_hora_virtual'), precision='0.00001')
    combinar_materia = _safe_int(payload.get('combinar_materia'), default=0)
    ver_reporte = _safe_int(payload.get('ver_reporte'), default=1)
    secuencia_materia = str(payload.get('secuencia_materia') or '0').strip() or '0'
    estado = _status_to_db_value(payload.get('estado_materia'))

    if not cod_anio_basica:
        raise AcademicCatalogError('Debes seleccionar la carrera para asociar el pensum.')
    if not nombre_materia:
        raise AcademicCatalogError('Debes ingresar el nombre de la materia.')
    if len(unidad_organiza) > 50:
        raise AcademicCatalogError('La unidad organizativa no puede superar 50 caracteres.')
    if len(nombre_materia) > 200:
        raise AcademicCatalogError('El nombre de la materia no puede superar 200 caracteres.')
    if len(tipo_materia) > 1:
        raise AcademicCatalogError('La categoria de la materia debe tener un solo caracter.')
    if len(cod_materia) > 50:
        raise AcademicCatalogError('El código alterno de materia no puede superar 50 caracteres.')
    if len(secuencia_materia) > 50:
        raise AcademicCatalogError('La secuencia de materia no puede superar 50 caracteres.')
    if modalidad_valor not in {'presencial', 'online'}:
        modalidad_valor = 'presencial'
    if modalidad_valor == 'presencial' and not str(payload.get('valor_hora') or '').strip():
        raise AcademicCatalogError('Debes ingresar el valor hora presencial.')
    if modalidad_valor == 'online' and not str(payload.get('valor_hora_virtual') or '').strip():
        raise AcademicCatalogError('Debes ingresar el valor hora virtual.')

    _ensure_carrera_exists(cod_anio_basica)
    if not codigo_materia:
        codigo_materia = _next_subject_code()

    status_columns = _ensure_pensum_status_columns()
    if orden <= 0:
        orden = _next_pensum_order(cod_anio_basica)
    if num_malla <= 0:
        num_malla = _default_num_malla(cod_anio_basica)

    existing = _pensum_exists(cod_anio_basica, codigo_materia)
    codigo_materia_is_identity = _is_identity_column('PENSUM', 'codigo_materia')
    status_assignments = ',\n                    '.join(
        f'{_quote_identifier(column)} = %s' for column in status_columns
    )

    with connection.cursor() as cursor:
        if existing:
            cursor.execute(
                f"""
                UPDATE dbo.PENSUM
                SET
                    Unidad_Organiza = %s,
                    Nomb_Materia = %s,
                    Semestre = %s,
                    Creditos = %s,
                    Orden = %s,
                    NumMalla = %s,
                    cod_materia = %s,
                    Horas = %s,
                    ValorHora = %s,
                    ValorHoraVirtual = %s,
                    CombinarMateria = %s,
                    verreporte = %s,
                    SecuenciaMateria = %s,
                    tipomateria = %s,
                    {status_assignments}
                WHERE LTRIM(RTRIM(CAST(Cod_AnioBasica AS varchar(20)))) = %s
                  AND LTRIM(RTRIM(CAST(codigo_materia AS varchar(50)))) = %s
                """,
                [
                    unidad_organiza,
                    nombre_materia,
                    semestre,
                    creditos,
                    orden,
                    num_malla,
                    cod_materia,
                    horas,
                    valor_hora,
                    valor_hora_virtual,
                    combinar_materia,
                    ver_reporte,
                    secuencia_materia,
                    tipo_materia,
                    *[estado for _ in status_columns],
                    cod_anio_basica,
                    codigo_materia,
                ],
            )
            action = 'actualizado'
        else:
            insert_columns = [
                'Cod_AnioBasica',
                'Unidad_Organiza',
                'Nomb_Materia',
                'Semestre',
                'Creditos',
                'Orden',
                'NumMalla',
                'cod_materia',
                'Horas',
                'ValorHora',
                'ValorHoraVirtual',
                'CombinarMateria',
                'verreporte',
                'SecuenciaMateria',
                'tipomateria',
                *[_quote_identifier(column) for column in status_columns],
            ]
            insert_values = [
                cod_anio_basica,
                unidad_organiza,
                nombre_materia,
                semestre,
                creditos,
                orden,
                num_malla,
                cod_materia,
                horas,
                valor_hora,
                valor_hora_virtual,
                combinar_materia,
                ver_reporte,
                secuencia_materia,
                tipo_materia,
                *[estado for _ in status_columns],
            ]

            if not codigo_materia_is_identity:
                insert_columns.insert(1, 'codigo_materia')
                insert_values.insert(1, codigo_materia)

            placeholders = ', '.join(['%s'] * len(insert_values))
            cursor.execute(
                f"""
                INSERT INTO dbo.PENSUM ({', '.join(insert_columns)})
                OUTPUT INSERTED.codigo_materia
                VALUES ({placeholders})
                """,
                insert_values,
            )
            inserted_row = cursor.fetchone()
            if inserted_row and inserted_row[0] is not None:
                codigo_materia = str(inserted_row[0]).strip()
            action = 'creado'

    return {
        'action': action,
        'cod_anio_basica': cod_anio_basica,
        'codigo_materia': codigo_materia,
        'unidad_organiza': unidad_organiza,
        'nombre_materia': nombre_materia,
        'semestre': str(semestre),
        'creditos': creditos,
        'orden': str(orden),
        'num_malla': str(num_malla),
        'cod_materia': cod_materia,
        'horas': str(horas),
        'valor_hora': valor_hora,
        'valor_hora_virtual': valor_hora_virtual,
        'combinar_materia': str(combinar_materia),
        'ver_reporte': str(ver_reporte),
        'secuencia_materia': secuencia_materia,
        'tipo_materia': tipo_materia,
        'estado_materia': 'Activo' if estado == 'A' else 'Inactivo',
        'es_activo': estado == 'A',
    }


def update_pensum_status(payload: dict[str, Any]) -> dict[str, Any]:
    cod_anio_basica = str(payload.get('cod_anio_basica') or '').strip()
    codigo_materia = str(payload.get('codigo_materia') or '').strip()
    estado = _status_to_db_value(payload.get('estado_materia'))

    if not cod_anio_basica or not codigo_materia:
        raise AcademicCatalogError('Debes seleccionar una materia del pensum para actualizarla.')

    status_columns = _ensure_pensum_status_columns()
    status_assignments = ', '.join(f'{_quote_identifier(column)} = %s' for column in status_columns)

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE dbo.PENSUM
            SET {status_assignments}
            WHERE LTRIM(RTRIM(CAST(Cod_AnioBasica AS varchar(20)))) = %s
              AND LTRIM(RTRIM(CAST(codigo_materia AS varchar(50)))) = %s
            """,
            [*[estado for _ in status_columns], cod_anio_basica, codigo_materia],
        )
        updated = cursor.rowcount

    if updated <= 0:
        raise AcademicCatalogError('No se encontró la materia seleccionada en PENSUM.')

    return {
        'cod_anio_basica': cod_anio_basica,
        'codigo_materia': codigo_materia,
        'estado_materia': 'Activo' if estado == 'A' else 'Inactivo',
        'es_activo': estado == 'A',
    }


def get_pensum_status_column() -> str | None:
    return _resolve_pensum_status_column()


def is_catalog_value_active(value: Any, default: bool = False) -> bool:
    return _is_active(value, default=default)


def _fetch_carreras(include_inactive: bool = False) -> list[dict[str, Any]]:
    query = """
        SELECT
            CAST(Num AS varchar(20)) AS num,
            CAST(Cod_AnioBasica AS varchar(20)) AS cod_anio_basica,
            RTRIM(ISNULL(Nombre_Basica, '')) AS nombre_basica,
            RTRIM(ISNULL(Estado, '')) AS estado,
            RTRIM(ISNULL(CAST(tp_escuela AS varchar(100)), '')) AS tp_escuela
        FROM dbo.CARRERAS
        ORDER BY Nombre_Basica ASC
    """

    rows = _fetch_all(query, [])
    carreras: list[dict[str, Any]] = []
    for row in rows:
        estado_raw = str(row.get('estado') or '').strip()
        es_activo = _is_active(estado_raw, default=False)
        if not include_inactive and not es_activo:
            continue

        carreras.append(
            {
                'num': str(row.get('num') or '').strip(),
                'cod_anio_basica': str(row.get('cod_anio_basica') or '').strip(),
                'nombre_basica': str(row.get('nombre_basica') or '').strip() or 'Sin nombre',
                'tp_escuela': str(row.get('tp_escuela') or '').strip(),
                'categoria': _category_label(row.get('tp_escuela')),
                'estado_raw': estado_raw,
                'estado': 'Activo' if es_activo else 'Inactivo',
                'es_activo': es_activo,
            }
        )
    return carreras


def _fetch_periodos() -> list[dict[str, Any]]:
    query = """
        SELECT
            CAST(cod_periodo AS varchar(20)) AS cod_periodo,
            RTRIM(ISNULL(Detalle_Periodo, '')) AS detalle_periodo,
            RTRIM(ISNULL(Periodo, '')) AS periodo,
            RTRIM(ISNULL(Estado, '')) AS estado,
            RTRIM(ISNULL(estado_ed, '')) AS estado_ed,
            CAST(ISNULL(Orden, 0) AS int) AS orden
        FROM dbo.PERIODO
        ORDER BY Orden DESC, cod_periodo DESC
    """

    rows = _fetch_all(query, [])
    periodos: list[dict[str, Any]] = []
    for row in rows:
        estado_raw = str(row.get('estado') or '').strip()
        estado_ed_raw = str(row.get('estado_ed') or '').strip()
        estado_fuente = estado_ed_raw or estado_raw
        es_activo = _is_active(estado_fuente, default=False)
        periodos.append(
            {
                'cod_periodo': str(row.get('cod_periodo') or '').strip(),
                'detalle_periodo': str(row.get('detalle_periodo') or '').strip() or 'Sin detalle',
                'periodo': str(row.get('periodo') or '').strip() or '',
                'estado_raw': estado_raw,
                'estado_ed_raw': estado_ed_raw,
                'estado': 'Activo' if es_activo else 'Inactivo',
                'es_activo': es_activo,
            }
        )
    return periodos


def _fetch_cursos_por_carrera(include_inactive: bool = False) -> dict[str, list[dict[str, str]]]:
    rows = _fetch_pensum_rows(include_inactive=include_inactive)
    grouped: dict[str, list[dict[str, str]]] = {}
    seen_by_career: dict[str, set[str]] = {}

    for row in rows:
        career_key = str(row.get('cod_anio_basica') or '').strip()
        subject_code = str(row.get('codigo_materia') or '').strip()
        subject_name = str(row.get('nombre_materia') or '').strip()
        if not career_key or not subject_code or not subject_name:
            continue

        if _normalize_text(subject_name) == 'INGLES INTENSIVO':
            continue

        if career_key not in grouped:
            grouped[career_key] = []
            seen_by_career[career_key] = set()

        dedup_key = f'{subject_code}|{subject_name.lower()}'
        if dedup_key in seen_by_career[career_key]:
            continue

        seen_by_career[career_key].add(dedup_key)
        grouped[career_key].append(
            {
                'codigo_materia': subject_code,
                'nombre_materia': subject_name,
                'semestre': str(row.get('semestre') or '').strip(),
                'orden': str(row.get('orden') or '').strip(),
                'horas': str(row.get('horas') or '0').strip(),
                'valor_hora': str(row.get('valor_hora') or '0').strip(),
                'monto_calculado': str(row.get('monto_calculado') or '0.00'),
                'unidad_organiza': str(row.get('unidad_organiza') or '').strip(),
                'tipo_materia': str(row.get('tipo_materia') or '').strip(),
                'categoria': str(row.get('categoria') or 'Sin categoría'),
                'creditos': str(row.get('creditos') or '0').strip(),
                'num_malla': str(row.get('num_malla') or '').strip(),
                'cod_materia': str(row.get('cod_materia') or '').strip(),
                'valor_hora_virtual': str(row.get('valor_hora_virtual') or '0').strip(),
                'combinar_materia': str(row.get('combinar_materia') or '0').strip(),
                'ver_reporte': str(row.get('ver_reporte') or '1').strip(),
                'secuencia_materia': str(row.get('secuencia_materia') or '0').strip(),
                'estado_materia': str(row.get('estado_materia') or ''),
                'es_activo': bool(row.get('es_activo')),
            }
        )

    return grouped


def _fetch_paralelos() -> list[dict[str, str]]:
    rows = _fetch_all(
        """
        SELECT codigo, paralelo, fuente
        FROM (
            SELECT
                CAST(codigo_paralelo AS varchar(20)) AS codigo,
                UPPER(LTRIM(RTRIM(ISNULL(nombre_paralelo, '')))) AS paralelo,
                'Paralelo' AS fuente,
                TRY_CONVERT(int, codigo_paralelo) AS orden
            FROM dbo.Paralelo
            WHERE LTRIM(RTRIM(ISNULL(nombre_paralelo, ''))) <> ''
              AND ISNULL(activo, 1) = 1

            UNION ALL

            SELECT
                CAST(num AS varchar(20)) AS codigo,
                UPPER(LTRIM(RTRIM(ISNULL(paralelo, '')))) AS paralelo,
                'PARALELOS' AS fuente,
                TRY_CONVERT(int, num) AS orden
            FROM dbo.PARALELOS
            WHERE LTRIM(RTRIM(ISNULL(paralelo, ''))) <> ''
        ) P
        ORDER BY
            CASE WHEN fuente = 'Paralelo' THEN 0 ELSE 1 END,
            orden ASC,
            paralelo ASC
        """,
        [],
    )
    paralelos: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        paralelo = str(row.get('paralelo') or '').strip().upper()
        if not paralelo or paralelo in seen:
            continue
        seen.add(paralelo)
        paralelos.append(
            {
                'codigo': str(row.get('codigo') or '').strip(),
                'paralelo': paralelo,
                'fuente': str(row.get('fuente') or '').strip(),
            }
        )
    return paralelos


def _fetch_jornadas() -> list[dict[str, str]]:
    rows = _fetch_all(
        """
        SELECT
            CAST(NumJ AS varchar(20)) AS codigo_jornada,
            LTRIM(RTRIM(ISNULL(DetalleJ, ''))) AS jornada,
            CAST(ISNULL(codmodalidad, 0) AS varchar(20)) AS cod_modalidad
        FROM dbo.JORNADA
        WHERE LTRIM(RTRIM(ISNULL(DetalleJ, ''))) <> ''
        ORDER BY NumJ ASC
        """,
        [],
    )
    return [
        {
            'codigo_jornada': str(row.get('codigo_jornada') or '').strip(),
            'jornada': str(row.get('jornada') or '').strip(),
            'cod_modalidad': str(row.get('cod_modalidad') or '').strip(),
        }
        for row in rows
        if str(row.get('codigo_jornada') or '').strip()
    ]


def _fetch_paralelos_por_materia() -> dict[str, list[dict[str, str]]]:
    rows = _fetch_all(
        """
        WITH ParalelosBase AS (
            SELECT
                CAST(CE.cod_anio_Basica AS varchar(20)) AS cod_anio_basica,
                CAST(CE.codigo_materia AS varchar(50)) AS codigo_materia,
                CAST(CE.codigo_periodo AS varchar(20)) AS codigo_periodo,
                UPPER(LTRIM(RTRIM(ISNULL(CE.paralelo, '')))) AS paralelo,
                CAST(ISNULL(CE.NumGrupo, 1) AS varchar(20)) AS cod_jornada,
                '' AS jornada,
                COUNT(DISTINCT CAST(CE.codigo_estud AS varchar(30))) AS total_estudiantes,
                CAST(0 AS int) AS total_docentes
            FROM dbo.CARRERAXESTUD CE
            WHERE LTRIM(RTRIM(ISNULL(CE.paralelo, ''))) <> ''
              AND CE.cod_anio_Basica IS NOT NULL
              AND CE.codigo_materia IS NOT NULL
              AND CE.codigo_periodo IS NOT NULL
            GROUP BY
                CE.cod_anio_Basica,
                CE.codigo_materia,
                CE.codigo_periodo,
                UPPER(LTRIM(RTRIM(ISNULL(CE.paralelo, '')))),
                CE.NumGrupo

            UNION ALL

            SELECT
                CAST(CXD.cod_Anio_Basica AS varchar(20)) AS cod_anio_basica,
                CAST(CXD.codigo_materia AS varchar(50)) AS codigo_materia,
                CAST(CXD.codigo_periodo AS varchar(20)) AS codigo_periodo,
                UPPER(LTRIM(RTRIM(ISNULL(CXD.Paralelo, '')))) AS paralelo,
                CAST(ISNULL(CXD.Cod_Jornada, 0) AS varchar(20)) AS cod_jornada,
                LTRIM(RTRIM(ISNULL(J.DetalleJ, ''))) AS jornada,
                CAST(0 AS int) AS total_estudiantes,
                COUNT(DISTINCT CAST(CXD.codigo_doc AS varchar(30))) AS total_docentes
            FROM dbo.CARRERAXDOCENTE CXD
            LEFT JOIN dbo.JORNADA J
              ON CAST(J.NumJ AS varchar(20)) = CAST(CXD.Cod_Jornada AS varchar(20))
            WHERE LTRIM(RTRIM(ISNULL(CXD.Paralelo, ''))) <> ''
              AND CXD.cod_Anio_Basica IS NOT NULL
              AND CXD.codigo_materia IS NOT NULL
              AND CXD.codigo_periodo IS NOT NULL
            GROUP BY
                CXD.cod_Anio_Basica,
                CXD.codigo_materia,
                CXD.codigo_periodo,
                UPPER(LTRIM(RTRIM(ISNULL(CXD.Paralelo, '')))),
                CXD.Cod_Jornada,
                LTRIM(RTRIM(ISNULL(J.DetalleJ, '')))
        )
        SELECT
            cod_anio_basica,
            codigo_materia,
            codigo_periodo,
            paralelo,
            cod_jornada,
            jornada,
            SUM(total_estudiantes) AS total_estudiantes,
            SUM(total_docentes) AS total_docentes
        FROM ParalelosBase
        GROUP BY
            cod_anio_basica,
            codigo_materia,
            codigo_periodo,
            paralelo,
            cod_jornada,
            jornada
        ORDER BY
            cod_anio_basica ASC,
            codigo_materia ASC,
            codigo_periodo DESC,
            paralelo ASC,
            cod_jornada ASC
        """,
        [],
    )
    grouped: dict[str, list[dict[str, str]]] = {}
    seen: dict[str, set[str]] = {}

    for row in rows:
        career_code = str(row.get('cod_anio_basica') or '').strip()
        subject_code = str(row.get('codigo_materia') or '').strip()
        period_code = str(row.get('codigo_periodo') or '').strip()
        paralelo = str(row.get('paralelo') or '').strip().upper()
        if not career_code or not subject_code or not period_code or not paralelo:
            continue

        exact_key = f'{career_code}|{subject_code}|{period_code}'
        subject_key = f'{career_code}|{subject_code}|*'
        option_key = f"{paralelo}|{str(row.get('cod_jornada') or '').strip()}"

        option = {
            'paralelo': paralelo,
            'cod_jornada': str(row.get('cod_jornada') or '').strip(),
            'jornada': str(row.get('jornada') or '').strip(),
            'codigo_periodo': period_code,
            'total_estudiantes': str(row.get('total_estudiantes') or '0').strip(),
            'total_docentes': str(row.get('total_docentes') or '0').strip(),
        }

        for key in (exact_key, subject_key):
            if key not in grouped:
                grouped[key] = []
                seen[key] = set()
            if option_key in seen[key]:
                continue

            seen[key].add(option_key)
            grouped[key].append(option)

    return grouped


def _fetch_pensum_rows(include_inactive: bool = False) -> list[dict[str, Any]]:
    status_select = _pensum_status_select_expression()

    query = """
        SELECT
            CAST(p.Cod_AnioBasica AS varchar(20)) AS cod_anio_basica,
            CAST(p.codigo_materia AS varchar(50)) AS codigo_materia,
            RTRIM(ISNULL(p.Nomb_Materia, '')) AS nombre_materia,
            CAST(p.Semestre AS varchar(20)) AS semestre,
            CAST(ISNULL(p.Creditos, 0) AS decimal(18, 2)) AS creditos,
            CAST(p.Orden AS varchar(20)) AS orden,
            CAST(ISNULL(p.NumMalla, 0) AS varchar(20)) AS num_malla,
            RTRIM(ISNULL(CAST(p.cod_materia AS varchar(50)), '')) AS cod_materia,
            CAST(ISNULL(p.Horas, 0) AS decimal(18, 0)) AS horas,
            CAST(ISNULL(p.ValorHora, 0) AS decimal(18, 5)) AS valor_hora,
            CAST(ISNULL(p.ValorHoraVirtual, 0) AS decimal(18, 5)) AS valor_hora_virtual,
            CAST(ISNULL(p.CombinarMateria, 0) AS varchar(20)) AS combinar_materia,
            CAST(ISNULL(p.verreporte, 1) AS varchar(20)) AS ver_reporte,
            RTRIM(ISNULL(CAST(p.SecuenciaMateria AS varchar(50)), '0')) AS secuencia_materia,
            RTRIM(ISNULL(CAST(p.Unidad_Organiza AS varchar(100)), '')) AS unidad_organiza,
            RTRIM(ISNULL(CAST(p.tipomateria AS varchar(100)), '')) AS tipo_materia,
            RTRIM(ISNULL(c.Nombre_Basica, '')) AS nombre_basica,
            {status_select}
        FROM dbo.PENSUM p
        LEFT JOIN dbo.CARRERAS c
          ON LTRIM(RTRIM(CAST(c.Cod_AnioBasica AS varchar(20)))) =
             LTRIM(RTRIM(CAST(p.Cod_AnioBasica AS varchar(20))))
        WHERE RTRIM(ISNULL(p.Nomb_Materia, '')) <> ''
        ORDER BY p.Cod_AnioBasica ASC, p.Orden ASC, p.Nomb_Materia ASC
    """.format(status_select=status_select)

    rows = _fetch_all(query, [])
    pensum: list[dict[str, Any]] = []

    for row in rows:
        career_key = str(row.get('cod_anio_basica') or '').strip()
        subject_code = str(row.get('codigo_materia') or '').strip()
        subject_name = str(row.get('nombre_materia') or '').strip()
        if not career_key or not subject_code or not subject_name:
            continue

        unidad_organiza = str(row.get('unidad_organiza') or '').strip()
        tipo_materia = str(row.get('tipo_materia') or '').strip()
        estado_raw = str(row.get('estado_materia_raw') or '').strip()
        es_activo = _is_active(estado_raw, default=True)
        if not include_inactive and not es_activo:
            continue

        pensum.append(
            {
                'row_key': f"{career_key}|{subject_code}|{row.get('orden') or ''}|{subject_name}",
                'cod_anio_basica': career_key,
                'codigo_materia': subject_code,
                'nombre_materia': subject_name,
                'semestre': str(row.get('semestre') or '').strip(),
                'creditos': str(row.get('creditos') or '0').strip(),
                'orden': str(row.get('orden') or '').strip(),
                'num_malla': str(row.get('num_malla') or '').strip(),
                'cod_materia': str(row.get('cod_materia') or '').strip(),
                'horas': str(row.get('horas') or '0').strip(),
                'valor_hora': str(row.get('valor_hora') or '0').strip(),
                'valor_hora_virtual': str(row.get('valor_hora_virtual') or '0').strip(),
                'combinar_materia': str(row.get('combinar_materia') or '0').strip(),
                'ver_reporte': str(row.get('ver_reporte') or '1').strip(),
                'secuencia_materia': str(row.get('secuencia_materia') or '0').strip(),
                'descuento_inscripcion': f'{INSCRIPTION_AMOUNT_DISCOUNT:.2f}',
                'monto_calculado': f"{calculate_inscription_amount(row.get('horas'), row.get('valor_hora_virtual')):.2f}",
                'unidad_organiza': unidad_organiza,
                'tipo_materia': tipo_materia,
                'categoria': _category_label(tipo_materia or unidad_organiza),
                'nombre_basica': str(row.get('nombre_basica') or '').strip(),
                'estado_materia_raw': estado_raw,
                'estado_materia': 'Activo' if es_activo else 'Inactivo',
                'es_activo': es_activo,
            }
        )

    return pensum


def _category_label(value: Any, fallback: str = 'Sin categoría') -> str:
    text = str(value or '').strip()
    return text or fallback


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize('NFD', str(value or ''))
    without_accents = ''.join(ch for ch in normalized if unicodedata.category(ch) != 'Mn')
    return without_accents.strip().upper()


def _is_active(value: Any, default: bool = False) -> bool:
    text = str(value or '').strip().lower()
    if not text:
        return default
    if text == 'p':
        return False
    return text in ACTIVE_VALUES


def _status_to_db_value(value: Any) -> str:
    text = str(value or '').strip().lower()
    if text in {'p', 'i', 'inactivo', 'inactive', '0', 'no', 'n', 'false'}:
        return 'P'
    return 'A'


def calculate_inscription_amount(horas: Any, valor_hora: Any) -> Decimal:
    return FIXED_INSCRIPTION_AMOUNT.quantize(Decimal('0.01'))


def _to_decimal_value(value: Any) -> Decimal:
    try:
        return Decimal(str(value or '0'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or '').strip()))
    except (TypeError, ValueError):
        return default


def _safe_decimal(value: Any, precision: str = '0.01') -> str:
    try:
        parsed = Decimal(str(value or '0').strip())
    except (InvalidOperation, TypeError, ValueError):
        parsed = Decimal('0')
    if parsed < 0:
        parsed = Decimal('0')
    return str(parsed.quantize(Decimal(precision)))


def _ensure_carrera_exists(cod_anio_basica: str) -> None:
    row = _fetch_one(
        """
        SELECT TOP (1) 1 AS found
        FROM dbo.CARRERAS
        WHERE LTRIM(RTRIM(CAST(Cod_AnioBasica AS varchar(20)))) = %s
        """,
        [cod_anio_basica],
    )
    if not row:
        raise AcademicCatalogError('La carrera seleccionada no existe.')


def _pensum_exists(cod_anio_basica: str, codigo_materia: str) -> bool:
    row = _fetch_one(
        """
        SELECT TOP (1) 1 AS found
        FROM dbo.PENSUM
        WHERE LTRIM(RTRIM(CAST(Cod_AnioBasica AS varchar(20)))) = %s
          AND LTRIM(RTRIM(CAST(codigo_materia AS varchar(50)))) = %s
        """,
        [cod_anio_basica, codigo_materia],
    )
    return bool(row)


def _next_pensum_order(cod_anio_basica: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ISNULL(MAX(CAST(Orden AS int)), 0) + 1
            FROM dbo.PENSUM
            WHERE LTRIM(RTRIM(CAST(Cod_AnioBasica AS varchar(20)))) = %s
            """,
            [cod_anio_basica],
        )
        row = cursor.fetchone()
    return _safe_int(row[0] if row else 1, default=1)


def _default_num_malla(cod_anio_basica: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ISNULL(MAX(CAST(NumMalla AS int)), 0)
            FROM dbo.PENSUM
            WHERE LTRIM(RTRIM(CAST(Cod_AnioBasica AS varchar(20)))) = %s
            """,
            [cod_anio_basica],
        )
        row = cursor.fetchone()
    return _safe_int(row[0] if row else 0, default=0)


def _next_subject_code() -> str:
    rows = _fetch_all(
        """
        SELECT CAST(codigo_materia AS varchar(50)) AS codigo_materia
        FROM dbo.PENSUM
        """,
        [],
    )
    numeric_codes = [
        str(row.get('codigo_materia') or '').strip()
        for row in rows
        if str(row.get('codigo_materia') or '').strip().isdigit()
    ]
    if not numeric_codes:
        return '1'

    next_code = max(int(code) for code in numeric_codes) + 1
    min_length = max(len(code) for code in numeric_codes)
    return str(next_code).zfill(min_length)


def _is_identity_column(table_name: str, column_name: str) -> bool:
    row = _fetch_one(
        """
        SELECT COLUMNPROPERTY(
            OBJECT_ID(TABLE_SCHEMA + '.' + TABLE_NAME),
            COLUMN_NAME,
            'IsIdentity'
        ) AS is_identity
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'dbo'
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        [table_name, column_name],
    )
    return bool(row and _safe_int(row.get('is_identity'), default=0) == 1)


def _resolve_pensum_status_column() -> str | None:
    status_columns = _pensum_status_columns()
    return status_columns[0] if status_columns else None


def _pensum_status_columns() -> list[str]:
    columns = _table_columns('PENSUM')
    lowered = {column.lower(): column for column in columns}
    status_columns = []
    for candidate in PENSUM_STATUS_COLUMN_CANDIDATES:
        if candidate.lower() in lowered:
            status_columns.append(lowered[candidate.lower()])
    return status_columns


def _ensure_pensum_status_columns() -> list[str]:
    status_columns = _pensum_status_columns()
    if status_columns:
        return status_columns

    with connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE dbo.PENSUM ADD {DEFAULT_PENSUM_STATUS_COLUMN} VARCHAR(50) NULL")
    return [DEFAULT_PENSUM_STATUS_COLUMN]


def _pensum_status_select_expression() -> str:
    status_columns = _pensum_status_columns()
    if not status_columns:
        return "CAST('A' AS varchar(20)) AS estado_materia_raw"

    status_values = [
        f"NULLIF(RTRIM(ISNULL(CAST(p.{_quote_identifier(column)} AS nvarchar(50)), '')), '')"
        for column in status_columns
    ]
    return f"COALESCE({', '.join(status_values)}, 'A') AS estado_materia_raw"


def _table_columns(table_name: str) -> list[str]:
    rows = _fetch_all(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'dbo'
          AND TABLE_NAME = %s
        """,
        [table_name],
    )
    return [str(row.get('COLUMN_NAME') or '').strip() for row in rows if row.get('COLUMN_NAME')]


def _quote_identifier(identifier: str) -> str:
    if not identifier.replace('_', '').isalnum():
        raise AcademicCatalogError('Nombre de columna inválido en el catálogo académico.')
    return f'[{identifier}]'


def _fetch_one(query: str, params: list[Any]) -> dict[str, Any] | None:
    rows = _fetch_all(query, params)
    if not rows:
        return None
    return rows[0]


def _fetch_all(query: str, params: list[Any]) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

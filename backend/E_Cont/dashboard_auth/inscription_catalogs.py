from __future__ import annotations

import unicodedata
from typing import Any

from django.db import connection


ACTIVE_VALUES = {'a', 'activo', 'active', '1', 'si', 's', 'true'}


def fetch_inscription_catalogs() -> dict[str, Any]:
    carreras = _fetch_carreras()
    periodos = _fetch_periodos()
    cursos_por_carrera = _fetch_cursos_por_carrera()

    return {
        'carreras': carreras,
        'periodos': periodos,
        'cursos_por_carrera': cursos_por_carrera,
    }


def _fetch_carreras() -> list[dict[str, Any]]:
    query = """
        SELECT
            CAST(Num AS varchar(20)) AS num,
            CAST(Cod_AnioBasica AS varchar(20)) AS cod_anio_basica,
            RTRIM(ISNULL(Nombre_Basica, '')) AS nombre_basica,
            RTRIM(ISNULL(Estado, '')) AS estado
        FROM dbo.CARRERAS
        ORDER BY Nombre_Basica ASC
    """

    rows = _fetch_all(query, [])
    carreras: list[dict[str, Any]] = []
    for row in rows:
        estado_raw = str(row.get('estado') or '').strip()
        carreras.append(
            {
                'num': str(row.get('num') or '').strip(),
                'cod_anio_basica': str(row.get('cod_anio_basica') or '').strip(),
                'nombre_basica': str(row.get('nombre_basica') or '').strip() or 'Sin nombre',
                'estado_raw': estado_raw,
                'estado': 'Activo' if _is_active(estado_raw) else 'Inactivo',
                'es_activo': _is_active(estado_raw),
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
        periodos.append(
            {
                'cod_periodo': str(row.get('cod_periodo') or '').strip(),
                'detalle_periodo': str(row.get('detalle_periodo') or '').strip() or 'Sin detalle',
                'periodo': str(row.get('periodo') or '').strip() or '',
                'estado_raw': estado_raw,
                'estado_ed_raw': estado_ed_raw,
                'estado': 'Activo' if _is_active(estado_fuente) else 'Inactivo',
                'es_activo': _is_active(estado_fuente),
            }
        )
    return periodos


def _fetch_cursos_por_carrera() -> dict[str, list[dict[str, str]]]:
    query = """
        SELECT
            CAST(Cod_AnioBasica AS varchar(20)) AS cod_anio_basica,
            CAST(codigo_materia AS varchar(50)) AS codigo_materia,
            RTRIM(ISNULL(Nomb_Materia, '')) AS nomb_materia,
            CAST(Semestre AS varchar(20)) AS semestre,
            CAST(Orden AS varchar(20)) AS orden,
            CAST(ISNULL(Horas, 0) AS decimal(18, 2)) AS horas,
            CAST(ISNULL(ValorHora, 0) AS decimal(18, 2)) AS valor_hora
        FROM dbo.PENSUM
        WHERE RTRIM(ISNULL(Nomb_Materia, '')) <> ''
        ORDER BY Cod_AnioBasica ASC, Orden ASC, Nomb_Materia ASC
    """

    rows = _fetch_all(query, [])
    grouped: dict[str, list[dict[str, str]]] = {}
    seen_by_career: dict[str, set[str]] = {}

    for row in rows:
        career_key = str(row.get('cod_anio_basica') or '').strip()
        subject_code = str(row.get('codigo_materia') or '').strip()
        subject_name = str(row.get('nomb_materia') or '').strip()
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
                'monto_calculado': f"{(_to_float(row.get('horas')) * _to_float(row.get('valor_hora'))):.2f}",
            }
        )

    return grouped


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize('NFD', str(value or ''))
    without_accents = ''.join(ch for ch in normalized if unicodedata.category(ch) != 'Mn')
    return without_accents.strip().upper()


def _is_active(value: str) -> bool:
    return str(value or '').strip().lower() in ACTIVE_VALUES


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fetch_all(query: str, params: list[Any]) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from django.conf import settings
from PIL import Image, UnidentifiedImageError


class CertificateTemplateError(Exception):
    pass


CERTIFICATE_TEMPLATE_DIR_NAME = 'certificados_config'
CERTIFICATE_TEMPLATE_FILE_NAME = 'plantilla_certificado.json'
CERTIFICATE_LOGO_DIR_NAME = 'logos_empresas'
CERTIFICATE_BACKGROUND_DIR_NAME = 'backgrounds'
DEFAULT_BACKGROUND_ID = 'intec_background_default'
DEFAULT_BACKGROUND_FILE_NAME = 'background_certificado_intec.png'
MAX_COMPLEMENT_LOGOS = 5
MAX_CERTIFICATE_BACKGROUNDS = 6
MAX_LOGO_SIZE_BYTES = 2 * 1024 * 1024
MAX_BACKGROUND_SIZE_BYTES = 6 * 1024 * 1024
SUPPORTED_LOGO_CONTENT_TYPES = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
}
CERTIFICATE_TEMPLATE_TYPES = {'EDUCACION_CONTINUA', 'REGULAR'}

DEFAULT_CERTIFICATE_TEMPLATE = {
    'use_default_logo': True,
    'show_complement_logos': True,
    'default_background_id': DEFAULT_BACKGROUND_ID,
    'backgrounds': [],
    'logos': [],
    'cut_settings': {},
}


def get_certificate_template_config(corte_id: Any = '') -> dict[str, Any]:
    config = _load_raw_config()
    return _public_config(config, corte_id=corte_id)


def save_certificate_template_config(payload: dict[str, Any], *, user_login: str = '') -> dict[str, Any]:
    current = _load_raw_config()
    existing = {
        _clean_text(item.get('id')): item
        for item in current.get('logos', [])
        if isinstance(item, dict) and _clean_text(item.get('id'))
    }
    remove_ids = {
        _clean_text(item)
        for item in payload.get('remove_logo_ids', [])
        if _clean_text(item)
    }
    existing_backgrounds = {
        _clean_text(item.get('id')): item
        for item in current.get('backgrounds', [])
        if isinstance(item, dict) and _clean_text(item.get('id')) and not item.get('built_in')
    }
    remove_background_ids = {
        _clean_text(item)
        for item in payload.get('remove_background_ids', [])
        if _clean_text(item) and _clean_text(item) != DEFAULT_BACKGROUND_ID
    }

    logos: list[dict[str, Any]] = []
    for logo_payload in payload.get('logos', []):
        if not isinstance(logo_payload, dict):
            continue
        logo_id = _clean_text(logo_payload.get('id'))
        if not logo_id or logo_id in remove_ids or logo_id not in existing:
            continue
        current_logo = dict(existing[logo_id])
        current_logo['display_name'] = _trim(_clean_text(logo_payload.get('display_name')), 90) or current_logo.get('display_name') or 'Logo'
        current_logo['enabled'] = bool(logo_payload.get('enabled', True))
        current_logo['updated_at'] = _utc_now()
        logos.append(current_logo)

    for logo_id in remove_ids:
        removed_logo = existing.get(logo_id)
        if removed_logo:
            _delete_logo_file(removed_logo)

    new_logo_id_map: dict[str, str] = {}
    for new_logo in payload.get('new_logos', []):
        if not isinstance(new_logo, dict):
            continue
        stored_logo = _store_asset(
            new_logo,
            storage_dir=_logo_storage_dir(),
            default_label='Logo empresa',
            max_bytes=MAX_LOGO_SIZE_BYTES,
            user_login=user_login,
        )
        client_id = _clean_text(new_logo.get('local_id') or new_logo.get('client_id'))
        if client_id:
            new_logo_id_map[client_id] = stored_logo['id']
        logos.append(stored_logo)

    if len(logos) > MAX_COMPLEMENT_LOGOS:
        raise CertificateTemplateError(f'Puedes registrar máximo {MAX_COMPLEMENT_LOGOS} logos complementarios.')

    backgrounds: list[dict[str, Any]] = [item for item in current.get('backgrounds', []) if item.get('built_in')]
    for background_payload in payload.get('backgrounds', []):
        if not isinstance(background_payload, dict):
            continue
        background_id = _clean_text(background_payload.get('id'))
        if not background_id or background_id == DEFAULT_BACKGROUND_ID or background_id in remove_background_ids:
            continue
        if background_id not in existing_backgrounds:
            continue
        current_background = dict(existing_backgrounds[background_id])
        current_background['display_name'] = (
            _trim(_clean_text(background_payload.get('display_name')), 90)
            or current_background.get('display_name')
            or 'Fondo de certificado'
        )
        current_background['enabled'] = bool(background_payload.get('enabled', True))
        current_background['updated_at'] = _utc_now()
        backgrounds.append(current_background)

    for background_id in remove_background_ids:
        removed_background = existing_backgrounds.get(background_id)
        if removed_background:
            _delete_asset_file(removed_background, _background_storage_dir())

    new_background_id_map: dict[str, str] = {}
    for new_background in payload.get('new_backgrounds', []):
        if not isinstance(new_background, dict):
            continue
        stored_background = _store_asset(
            new_background,
            storage_dir=_background_storage_dir(),
            default_label='Fondo de certificado',
            max_bytes=MAX_BACKGROUND_SIZE_BYTES,
            user_login=user_login,
        )
        client_id = _clean_text(new_background.get('local_id') or new_background.get('client_id'))
        if client_id:
            new_background_id_map[client_id] = stored_background['id']
        backgrounds.append(stored_background)

    if len(backgrounds) > MAX_CERTIFICATE_BACKGROUNDS:
        raise CertificateTemplateError(f'Puedes registrar máximo {MAX_CERTIFICATE_BACKGROUNDS} fondos de certificado.')

    default_background_id = _clean_text(payload.get('default_background_id')) or current.get('default_background_id') or DEFAULT_BACKGROUND_ID
    default_background_id = new_background_id_map.get(default_background_id, default_background_id)
    valid_background_ids = {_clean_text(item.get('id')) for item in backgrounds if item.get('enabled', True)}
    if default_background_id not in valid_background_ids:
        default_background_id = DEFAULT_BACKGROUND_ID

    cut_settings = _normalize_cut_settings(current.get('cut_settings'))
    _update_cut_settings_from_payload(
        cut_settings,
        payload,
        valid_logo_ids={_clean_text(item.get('id')) for item in logos if item.get('enabled', True)},
        valid_background_ids=valid_background_ids,
        logo_id_aliases=new_logo_id_map,
        background_id_aliases=new_background_id_map,
        user_login=user_login,
    )

    config = {
        'use_default_logo': bool(payload.get('use_default_logo', True)),
        'show_complement_logos': bool(payload.get('show_complement_logos', True)),
        'default_background_id': default_background_id,
        'backgrounds': backgrounds,
        'logos': logos,
        'cut_settings': cut_settings,
        'updated_at': _utc_now(),
        'updated_by': _trim(_clean_text(user_login), 120),
    }
    _write_raw_config(config)
    return _public_config(config, corte_id=payload.get('corte_id') or payload.get('CorteId'))


def build_certificate_template_preview(corte_id: Any = '') -> tuple[bytes, str]:
    from .inscription_certificate import build_inscription_certificate

    payload = {
        'source': 'preview_admin',
        'tipo_certificado': 'APROBACION',
        'certificate_type': 'APROBACION',
        'codigo_certificado': 'VISTA-PREVIA',
        'codigo_verificacion': 'VISTA-PREVIA',
        'nombre_materia': 'Diplomado de especialización para delegados de protección de datos',
        'codigo_materia': 'VGA-ED-2026',
        'matricula': '0000',
        'codigo_estud': '0000',
        'fecha_inscripcion': '10 de julio de 2026',
        'fecha_inicio': '20 de julio de 2026',
        'nombre': 'ESTUDIANTE DE PRUEBA',
        'cedula': '0000000000',
        'email': 'estudiante@intec.edu.ec',
        'telefono': '0999999999',
        'localidad': 'Machala',
        'direccion': 'Dirección de referencia',
        'codigo_periodo': 'ED-CONTINUA 2026',
        'cod_anio_basica': '13',
        'cod_curso': '2026',
        'corte_id': _clean_text(corte_id) or '0',
        'nombre_corte': 'Primera corte',
        'modalidad': 'Educación continua',
        'nota_final': '10.00',
        'porcentaje_asistencia': '100.00%',
    }
    content, _filename = build_inscription_certificate(payload)
    return content, 'previsualizacion_certificado.pdf'


def certificate_template_use_default_logo(corte_id: Any = '') -> bool:
    cut_setting = _cut_setting_for(_load_raw_config(), corte_id)
    if 'use_default_logo' in cut_setting:
        return bool(cut_setting.get('use_default_logo'))
    return bool(_load_raw_config().get('use_default_logo', True))


def certificate_template_complement_logo_paths(corte_id: Any = '') -> list[Path]:
    config = _load_raw_config()
    cut_key = _clean_text(corte_id)
    cut_settings = _normalize_cut_settings(config.get('cut_settings'))
    has_cut_setting = bool(cut_key and cut_key in cut_settings)
    cut_setting = cut_settings.get(cut_key, {})
    show_logos = cut_setting.get('show_complement_logos', config.get('show_complement_logos', True))
    if not show_logos:
        return []

    selected_ids = {
        _clean_text(item)
        for item in cut_setting.get('logo_ids', [])
        if _clean_text(item)
    }
    logo_paths: list[Path] = []
    for logo in config.get('logos', []):
        if not isinstance(logo, dict) or not logo.get('enabled', True):
            continue
        logo_id = _clean_text(logo.get('id'))
        if has_cut_setting and logo_id not in selected_ids:
            continue
        filename = _clean_text(logo.get('filename'))
        if not filename:
            continue
        path = _logo_storage_dir() / filename
        if path.exists() and _is_path_inside(path, _logo_storage_dir()):
            logo_paths.append(path)
    return logo_paths


def certificate_template_background_path(corte_id: Any = '') -> Path | None:
    config = _load_raw_config()
    cut_setting = _cut_setting_for(config, corte_id)
    background_id = _clean_text(cut_setting.get('background_id') or config.get('default_background_id') or DEFAULT_BACKGROUND_ID)
    backgrounds = {
        _clean_text(item.get('id')): item
        for item in config.get('backgrounds', [])
        if isinstance(item, dict) and item.get('enabled', True)
    }
    selected = backgrounds.get(background_id) or backgrounds.get(DEFAULT_BACKGROUND_ID)
    if not selected:
        return None
    storage_dir = _background_storage_dir()
    filename = _clean_text(selected.get('filename'))
    if not filename:
        return None
    path = storage_dir / filename
    if path.exists() and _is_path_inside(path, storage_dir):
        return path
    return None


def certificate_template_type(corte_id: Any = '') -> str:
    config = _load_raw_config()
    cut_setting = _cut_setting_for(config, corte_id)
    template_type = _clean_text(cut_setting.get('template_type')).upper()
    return template_type if template_type in CERTIFICATE_TEMPLATE_TYPES else 'EDUCACION_CONTINUA'


def _load_raw_config() -> dict[str, Any]:
    default_config = _default_config()
    config_path = _config_path()
    if not config_path.exists():
        return default_config
    try:
        loaded = json.loads(config_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return default_config
    if not isinstance(loaded, dict):
        return default_config

    backgrounds = loaded.get('backgrounds') if isinstance(loaded.get('backgrounds'), list) else []
    background_index = {
        _clean_text(item.get('id')): item
        for item in backgrounds
        if isinstance(item, dict) and _clean_text(item.get('id'))
    }
    default_background = _default_background_record()
    if default_background:
        background_index[DEFAULT_BACKGROUND_ID] = default_background

    return {
        **default_config,
        **loaded,
        'logos': loaded.get('logos') if isinstance(loaded.get('logos'), list) else [],
        'backgrounds': list(background_index.values()),
        'cut_settings': _normalize_cut_settings(loaded.get('cut_settings')),
    }


def _write_raw_config(config: dict[str, Any]) -> None:
    path = _config_path()
    temporary_path = path.with_suffix(f'{path.suffix}.{uuid4().hex}.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary_path.replace(path)
    except OSError as exc:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise CertificateTemplateError(
            'No fue posible guardar la plantilla en el servidor. Verifica los permisos de almacenamiento.'
        ) from exc


def _public_config(config: dict[str, Any], *, corte_id: Any = '') -> dict[str, Any]:
    cut_setting = _cut_setting_for(config, corte_id)
    return {
        'use_default_logo': bool(config.get('use_default_logo', True)),
        'show_complement_logos': bool(config.get('show_complement_logos', True)),
        'default_background_id': _clean_text(config.get('default_background_id')) or DEFAULT_BACKGROUND_ID,
        'updated_at': _clean_text(config.get('updated_at')),
        'updated_by': _clean_text(config.get('updated_by')),
        'cut_setting': _public_cut_setting(cut_setting),
        'cut_settings': {
            key: _public_cut_setting(value)
            for key, value in _normalize_cut_settings(config.get('cut_settings')).items()
        },
        'backgrounds': [
            {
                'id': _clean_text(background.get('id')),
                'display_name': _clean_text(background.get('display_name')) or 'Fondo de certificado',
                'filename': _clean_text(background.get('filename')),
                'content_type': _clean_text(background.get('content_type')),
                'size_bytes': int(background.get('size_bytes') or 0),
                'enabled': bool(background.get('enabled', True)),
                'built_in': bool(background.get('built_in')),
                'created_at': _clean_text(background.get('created_at')),
            }
            for background in config.get('backgrounds', [])
            if isinstance(background, dict)
        ],
        'logos': [
            {
                'id': _clean_text(logo.get('id')),
                'display_name': _clean_text(logo.get('display_name')) or 'Logo',
                'filename': _clean_text(logo.get('filename')),
                'content_type': _clean_text(logo.get('content_type')),
                'size_bytes': int(logo.get('size_bytes') or 0),
                'enabled': bool(logo.get('enabled', True)),
                'created_at': _clean_text(logo.get('created_at')),
            }
            for logo in config.get('logos', [])
            if isinstance(logo, dict)
        ],
        'limits': {
            'max_logos': MAX_COMPLEMENT_LOGOS,
            'max_backgrounds': MAX_CERTIFICATE_BACKGROUNDS,
            'max_logo_size_bytes': MAX_LOGO_SIZE_BYTES,
            'max_background_size_bytes': MAX_BACKGROUND_SIZE_BYTES,
            'content_types': sorted(SUPPORTED_LOGO_CONTENT_TYPES.keys()),
        },
    }


def _store_asset(
    payload: dict[str, Any],
    *,
    storage_dir: Path,
    default_label: str,
    max_bytes: int,
    user_login: str = '',
) -> dict[str, Any]:
    content_type = _clean_text(payload.get('content_type')).lower()
    extension = SUPPORTED_LOGO_CONTENT_TYPES.get(content_type)
    if not extension:
        raise CertificateTemplateError('Solo se permiten archivos en formato PNG o JPG.')

    content = _decode_asset_content(_clean_text(payload.get('data_url') or payload.get('content')), content_type)
    if not content:
        raise CertificateTemplateError('El archivo enviado no contiene datos válidos.')
    if len(content) > max_bytes:
        raise CertificateTemplateError('El archivo supera el tamaño máximo permitido.')
    _validate_image_content(content, extension)

    asset_id = uuid4().hex
    display_name = _trim(_clean_text(payload.get('display_name') or payload.get('name')), 90) or default_label
    filename = f'{asset_id}{extension}'
    storage_path = storage_dir / filename
    try:
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_bytes(content)
    except OSError as exc:
        try:
            storage_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise CertificateTemplateError(
            'No fue posible almacenar la imagen en el servidor. Verifica los permisos de almacenamiento.'
        ) from exc
    now = _utc_now()
    return {
        'id': asset_id,
        'display_name': display_name,
        'filename': filename,
        'content_type': content_type,
        'size_bytes': len(content),
        'enabled': True,
        'created_at': now,
        'updated_at': now,
        'created_by': _trim(_clean_text(user_login), 120),
    }


def _decode_asset_content(raw_value: str, content_type: str) -> bytes:
    value = raw_value.strip()
    if value.startswith('data:'):
        match = re.match(r'^data:([^;]+);base64,(.+)$', value, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            raise CertificateTemplateError('El archivo debe enviarse como base64 válido.')
        data_content_type = match.group(1).lower()
        if data_content_type != content_type:
            raise CertificateTemplateError('El tipo de archivo no coincide con el contenido enviado.')
        value = match.group(2)
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise CertificateTemplateError('El archivo debe enviarse como base64 válido.') from exc


def _validate_image_content(content: bytes, extension: str) -> None:
    expected_format = 'PNG' if extension == '.png' else 'JPEG'
    try:
        with Image.open(BytesIO(content)) as image:
            detected_format = str(image.format or '').upper()
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise CertificateTemplateError('La imagen enviada está dañada o no es un formato permitido.') from exc
    if detected_format != expected_format:
        raise CertificateTemplateError('El contenido de la imagen no coincide con el formato declarado.')


def _delete_logo_file(logo: dict[str, Any]) -> None:
    _delete_asset_file(logo, _logo_storage_dir())


def _delete_asset_file(asset: dict[str, Any], storage_dir: Path) -> None:
    filename = _clean_text(asset.get('filename'))
    if not filename:
        return
    path = storage_dir / filename
    if not _is_path_inside(path, storage_dir):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _config_path() -> Path:
    return _template_storage_dir() / CERTIFICATE_TEMPLATE_FILE_NAME


def _logo_storage_dir() -> Path:
    return _template_storage_dir() / CERTIFICATE_LOGO_DIR_NAME


def _background_storage_dir() -> Path:
    return _template_storage_dir() / CERTIFICATE_BACKGROUND_DIR_NAME


def _template_storage_dir() -> Path:
    return settings.BASE_DIR / CERTIFICATE_TEMPLATE_DIR_NAME


def _default_config() -> dict[str, Any]:
    config = dict(DEFAULT_CERTIFICATE_TEMPLATE)
    default_background = _default_background_record()
    if default_background:
        config['backgrounds'] = [default_background]
    return config


def _default_background_record() -> dict[str, Any] | None:
    path = _background_storage_dir() / DEFAULT_BACKGROUND_FILE_NAME
    if not path.exists():
        return None
    return {
        'id': DEFAULT_BACKGROUND_ID,
        'display_name': 'Formato INTEC educación continua',
        'filename': DEFAULT_BACKGROUND_FILE_NAME,
        'content_type': 'image/png',
        'size_bytes': path.stat().st_size,
        'enabled': True,
        'built_in': True,
        'created_at': '',
    }


def _normalize_cut_settings(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for raw_key, raw_setting in value.items():
        key = _clean_text(raw_key)
        if not key or not isinstance(raw_setting, dict):
            continue
        template_type = _clean_text(raw_setting.get('template_type')).upper()
        normalized[key] = {
            'template_type': template_type if template_type in CERTIFICATE_TEMPLATE_TYPES else 'EDUCACION_CONTINUA',
            'background_id': _clean_text(raw_setting.get('background_id')) or DEFAULT_BACKGROUND_ID,
            'logo_ids': [
                _clean_text(item)
                for item in raw_setting.get('logo_ids', [])
                if _clean_text(item)
            ] if isinstance(raw_setting.get('logo_ids'), list) else [],
            'use_default_logo': bool(raw_setting.get('use_default_logo', True)),
            'show_complement_logos': bool(raw_setting.get('show_complement_logos', True)),
            'updated_at': _clean_text(raw_setting.get('updated_at')),
            'updated_by': _clean_text(raw_setting.get('updated_by')),
        }
    return normalized


def _update_cut_settings_from_payload(
    cut_settings: dict[str, dict[str, Any]],
    payload: dict[str, Any],
    *,
    valid_logo_ids: set[str],
    valid_background_ids: set[str],
    logo_id_aliases: dict[str, str],
    background_id_aliases: dict[str, str],
    user_login: str,
) -> None:
    corte_id = _clean_text(payload.get('corte_id') or payload.get('CorteId'))
    if not corte_id:
        return
    template_type = _clean_text(payload.get('template_type') or payload.get('certificate_template_type')).upper()
    if template_type not in CERTIFICATE_TEMPLATE_TYPES:
        template_type = 'EDUCACION_CONTINUA'
    background_id = _clean_text(payload.get('background_id')) or DEFAULT_BACKGROUND_ID
    background_id = background_id_aliases.get(background_id, background_id)
    if background_id not in valid_background_ids:
        background_id = DEFAULT_BACKGROUND_ID
    raw_logo_ids = payload.get('logo_ids') if isinstance(payload.get('logo_ids'), list) else []
    logo_ids: list[str] = []
    for item in raw_logo_ids:
        raw_logo_id = _clean_text(item)
        logo_id = logo_id_aliases.get(raw_logo_id, raw_logo_id)
        if logo_id in valid_logo_ids and logo_id not in logo_ids:
            logo_ids.append(logo_id)
    cut_settings[corte_id] = {
        'template_type': template_type,
        'background_id': background_id,
        'logo_ids': logo_ids,
        'use_default_logo': bool(payload.get('cut_use_default_logo', payload.get('use_default_logo', True))),
        'show_complement_logos': bool(payload.get('cut_show_complement_logos', payload.get('show_complement_logos', True))),
        'updated_at': _utc_now(),
        'updated_by': _trim(_clean_text(user_login), 120),
    }


def _cut_setting_for(config: dict[str, Any], corte_id: Any) -> dict[str, Any]:
    key = _clean_text(corte_id)
    if not key:
        return {}
    return _normalize_cut_settings(config.get('cut_settings')).get(key, {})


def _public_cut_setting(setting: dict[str, Any]) -> dict[str, Any]:
    return {
        'template_type': _clean_text(setting.get('template_type')) or 'EDUCACION_CONTINUA',
        'background_id': _clean_text(setting.get('background_id')) or DEFAULT_BACKGROUND_ID,
        'logo_ids': [
            _clean_text(item)
            for item in setting.get('logo_ids', [])
            if _clean_text(item)
        ] if isinstance(setting.get('logo_ids'), list) else [],
        'use_default_logo': bool(setting.get('use_default_logo', True)),
        'show_complement_logos': bool(setting.get('show_complement_logos', True)),
        'updated_at': _clean_text(setting.get('updated_at')),
        'updated_by': _clean_text(setting.get('updated_by')),
    }


def _is_path_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _clean_text(value: Any) -> str:
    return ' '.join(str(value or '').strip().split())


def _trim(value: Any, max_length: int) -> str:
    return _clean_text(value)[:max_length]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

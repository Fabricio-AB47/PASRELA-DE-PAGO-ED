import base64
import tempfile
from io import BytesIO
from pathlib import Path
from unittest import TestCase

from django.test import override_settings
from PIL import Image

from dashboard_auth.certificate_template import (
    build_certificate_template_preview,
    certificate_template_complement_logo_paths,
    get_certificate_template_config,
    save_certificate_template_config,
)


class CertificateTemplateLogoTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(BASE_DIR=Path(self.temp_dir.name))
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        self.temp_dir.cleanup()

    def test_new_logo_is_persisted_and_assigned_to_selected_cut(self):
        config = save_certificate_template_config(
            {
                'corte_id': '1',
                'template_type': 'EDUCACION_CONTINUA',
                'use_default_logo': True,
                'show_complement_logos': True,
                'cut_show_complement_logos': True,
                'logo_ids': ['local-logo'],
                'logos': [],
                'new_logos': [{
                    'local_id': 'local-logo',
                    'name': 'empresa.png',
                    'display_name': 'Empresa aliada',
                    'content_type': 'image/png',
                    'data_url': f'data:image/png;base64,{self._png_base64()}',
                }],
                'backgrounds': [],
                'new_backgrounds': [],
            },
            user_login='administrador',
        )

        stored_logo_id = config['logos'][0]['id']
        self.assertEqual(config['cut_setting']['logo_ids'], [stored_logo_id])
        self.assertEqual(len(certificate_template_complement_logo_paths('1')), 1)
        preview, filename = build_certificate_template_preview('1')
        self.assertTrue(preview.startswith(b'%PDF'))
        self.assertEqual(filename, 'previsualizacion_certificado.pdf')

    def test_cut_without_selected_logos_does_not_inherit_every_global_logo(self):
        self.test_new_logo_is_persisted_and_assigned_to_selected_cut()
        current = get_certificate_template_config('1')

        save_certificate_template_config(
            {
                'corte_id': '2',
                'template_type': 'EDUCACION_CONTINUA',
                'use_default_logo': True,
                'show_complement_logos': True,
                'cut_show_complement_logos': True,
                'logo_ids': [],
                'logos': current['logos'],
                'new_logos': [],
                'backgrounds': current['backgrounds'],
                'new_backgrounds': [],
            },
            user_login='administrador',
        )

        self.assertEqual(certificate_template_complement_logo_paths('2'), [])

    @staticmethod
    def _png_base64() -> str:
        output = BytesIO()
        Image.new('RGBA', (20, 20), (155, 14, 14, 255)).save(output, format='PNG')
        return base64.b64encode(output.getvalue()).decode('ascii')

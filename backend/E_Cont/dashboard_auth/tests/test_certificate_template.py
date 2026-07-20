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
from dashboard_auth.inscription_certificate import _complement_logo_slots


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

    def test_one_complement_logo_uses_the_complete_available_area(self):
        self.assertEqual(_complement_logo_slots(1), [(0.0, 0.0, 1.0, 1.0)])

    def test_additional_complement_logos_are_smaller_and_below_the_first(self):
        slots = _complement_logo_slots(5)

        self.assertEqual(len(slots), 5)
        first = slots[0]
        self.assertEqual(first[:3], (0.0, 0.0, 1.0))
        for slot in slots[1:]:
            self.assertGreaterEqual(slot[1], first[3])
            self.assertLess(slot[2], first[2])
            self.assertLess(slot[3], first[3])
            self.assertLessEqual(slot[0] + slot[2], 1.0)
            self.assertLessEqual(slot[1] + slot[3], 1.0)

    def test_lower_complement_logos_always_fill_from_left_to_right(self):
        two_logo_slots = _complement_logo_slots(2)
        five_logo_slots = _complement_logo_slots(5)

        self.assertEqual(two_logo_slots[1][0], 0.0)
        self.assertEqual(two_logo_slots[1][2], five_logo_slots[1][2])
        self.assertEqual(
            [slot[0] for slot in five_logo_slots[1:]],
            sorted(slot[0] for slot in five_logo_slots[1:]),
        )

    @staticmethod
    def _png_base64() -> str:
        output = BytesIO()
        Image.new('RGBA', (20, 20), (155, 14, 14, 255)).save(output, format='PNG')
        return base64.b64encode(output.getvalue()).decode('ascii')

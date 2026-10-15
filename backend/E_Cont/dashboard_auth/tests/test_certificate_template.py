import base64
import tempfile
from io import BytesIO
from pathlib import Path
from unittest import TestCase

from django.test import override_settings
from PIL import Image

from dashboard_auth.certificate_template import (
    CERTIFICATE_BACKGROUND_DIR_NAME,
    CERTIFICATE_TEMPLATE_DIR_NAME,
    DEFAULT_BACKGROUND_FILE_NAME,
    build_certificate_template_preview,
    build_certificate_template_preview_image,
    certificate_template_complement_logo_paths,
    get_certificate_template_config,
    save_certificate_template_config,
)
from dashboard_auth.inscription_certificate import (
    CERTIFICATE_FIXED_SIGNATORIES,
    HEADER_DIVIDER_X_RATIO,
    HEADER_GROUP_LEFT_SHIFT_RATIO,
    HEADER_LOGO_AREA_LEFT_RATIO,
    HEADER_LOGO_AREA_WIDTH_RATIO,
    HEADER_LOGO_COLUMN_GAP_RATIO,
    HEADER_LOGO_GAP_RATIO,
    HEADER_LOGO_LEFT_MARGIN_RATIO,
    _approval_certificate_meta,
    _approval_certificate_narrative,
    _approval_certificate_result_lines,
    _apply_preview_fixed_signature_footer,
    _certificate_detail_label,
    _complement_logo_slots,
    _reference_certificate_canvas,
    _student_identity_label,
    _transparent_white_pil_image,
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

        image_preview, image_filename = build_certificate_template_preview_image('1')
        self.assertTrue(image_preview.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(image_filename, 'previsualizacion_certificado.png')

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

    def test_primary_logo_uses_the_reference_width(self):
        self.assertEqual(_complement_logo_slots(1), [(0.0, 0.0, 0.48, 1.0)])

    def test_certificate_uses_citizenship_id_label(self):
        self.assertEqual(
            _student_identity_label({'cedula': '1715988448'}),
            'Estudiante con cédula de ciudadanía N°. 1715988448',
        )

    def test_veronica_and_jaime_signatures_are_fixed_in_certificate_footer(self):
        self.assertEqual(
            CERTIFICATE_FIXED_SIGNATORIES,
            (
                'Ing. Verónica Cevallos Calderón Mgtr.',
                'Ing. Jaime Roder Ortega Pereira, Mgs.',
            ),
        )
        background_dir = (
            Path(self.temp_dir.name)
            / CERTIFICATE_TEMPLATE_DIR_NAME
            / CERTIFICATE_BACKGROUND_DIR_NAME
        )
        background_dir.mkdir(parents=True)
        source = Image.new('RGB', (100, 100), 'white')
        for x in range(100):
            for y in range(75, 100):
                source.putpixel((x, y), (155, 14, 14))
        source.save(background_dir / DEFAULT_BACKGROUND_FILE_NAME)

        preview = Image.new('RGB', (200, 100), 'blue')
        _apply_preview_fixed_signature_footer(preview)

        self.assertEqual(preview.getpixel((100, 10)), (0, 0, 255))
        self.assertEqual(preview.getpixel((100, 90)), (155, 14, 14))

    def test_certificate_meta_includes_dates_and_hours_but_hides_grade_and_attendance(self):
        self.assertEqual(
            _approval_certificate_meta({
                'fecha_inicio': '20 de julio de 2026',
                'fecha_fin': '10 de septiembre de 2026',
                'horas_curso': '160',
                'nota_final': '9.78',
                'porcentaje_asistencia': '100.00%',
            }),
            'Fecha de inicio: 20 de julio de 2026 · '
            'Finalización: 10 de septiembre de 2026\n'
            'Duración: 160 horas',
        )

    def test_certificate_meta_hides_grade_and_attendance_when_dates_are_absent(self):
        self.assertEqual(
            _approval_certificate_meta({
                'nota_final': '10.00/10.00',
                'porcentaje_asistencia': '100.00%',
            }),
            '',
        )

    def test_certificate_result_uses_compact_grade_and_attendance(self):
        self.assertEqual(
            _approval_certificate_result_lines({
                'nota_final': '10.00/10.00',
                'porcentaje_asistencia': '100.00%',
            }),
            ['Nota: 10/10 · Asistencia: 100%'],
        )

    def test_continuing_education_narrative_includes_completion_and_total_hours(self):
        self.assertEqual(
            _approval_certificate_narrative({
                'nombre_materia': 'Estadísticas para no estadísticos/as',
                'fecha_inicio': '27 de noviembre de 2025',
                'fecha_fin': '5 de diciembre de 2025',
                'horas_curso': '8',
            }),
            'Por haber participado en el curso de Estadísticas para no estadísticos/as '
            'dado del 27 de noviembre de 2025 al 5 de diciembre de 2025 '
            'con una duración total de 8 horas',
        )

    def test_diploma_narrative_does_not_repeat_course_word(self):
        narrative = _approval_certificate_narrative({
            'nombre_materia': 'Diplomado de protección de datos',
            'fecha_fin': '10 de septiembre de 2026',
            'horas_curso': '160 horas',
        })

        self.assertTrue(narrative.startswith('Por haber participado en el Diplomado'))
        self.assertNotIn('curso de Diplomado', narrative)

    def test_continuing_education_detail_line_is_hidden(self):
        self.assertEqual(
            _certificate_detail_label('EDUCACION_CONTINUA', 'Primera corte'),
            '',
        )

    def test_two_secondary_logos_form_a_column_next_to_the_primary_logo(self):
        slots = _complement_logo_slots(3)

        self.assertEqual(len(slots), 3)
        primary, secondary_top, secondary_bottom = slots
        self.assertEqual(primary[0], 0.0)
        self.assertAlmostEqual(primary[1], 0.0)
        self.assertAlmostEqual(primary[2], 0.48)
        self.assertEqual(primary[3], 1.0)
        self.assertEqual(secondary_top[0], secondary_bottom[0])
        self.assertEqual(secondary_top[2:], secondary_bottom[2:])
        self.assertGreater(secondary_top[1], 0.0)
        self.assertGreater(secondary_bottom[1], secondary_top[1])
        self.assertGreater(secondary_top[0], primary[0] + primary[2])
        self.assertAlmostEqual(secondary_top[3], 0.26)
        self.assertAlmostEqual(secondary_bottom[3], 0.26)
        self.assertEqual(primary, _complement_logo_slots(1)[0])

    def test_primary_logo_keeps_the_reference_gap_after_the_divider(self):
        self.assertGreater(HEADER_LOGO_AREA_LEFT_RATIO, HEADER_DIVIDER_X_RATIO)
        left_spacing = HEADER_LOGO_AREA_LEFT_RATIO - HEADER_DIVIDER_X_RATIO
        right_spacing = 1.0 - (HEADER_LOGO_AREA_LEFT_RATIO + HEADER_LOGO_AREA_WIDTH_RATIO)
        internal_spacing = HEADER_LOGO_AREA_WIDTH_RATIO * HEADER_LOGO_COLUMN_GAP_RATIO
        self.assertAlmostEqual(left_spacing, HEADER_LOGO_LEFT_MARGIN_RATIO)
        self.assertAlmostEqual(internal_spacing, HEADER_LOGO_GAP_RATIO)
        self.assertLess(left_spacing, internal_spacing)
        self.assertAlmostEqual(HEADER_LOGO_AREA_WIDTH_RATIO, 0.452)
        self.assertGreater(right_spacing, internal_spacing)

    def test_header_group_uses_its_original_position(self):
        self.assertAlmostEqual(HEADER_GROUP_LEFT_SHIFT_RATIO, 0.0)
        self.assertAlmostEqual(HEADER_DIVIDER_X_RATIO, 0.484)
        self.assertAlmostEqual(HEADER_LOGO_AREA_LEFT_RATIO, 0.502)

    def test_reference_header_shape_is_flush_with_the_top_edge(self):
        canvas = _reference_certificate_canvas((1000, 707))

        self.assertEqual(canvas.getpixel((0, 0)), (248, 240, 239))
        self.assertEqual(canvas.getpixel((385, 100)), (248, 240, 239))
        self.assertEqual(canvas.getpixel((500, 100)), (255, 255, 255))
        self.assertEqual(canvas.getpixel((100, 170)), (255, 255, 255))

    def test_header_logo_white_padding_is_removed_before_layout(self):
        logo = Image.new('RGB', (30, 20), 'white')
        for x in range(8, 22):
            for y in range(5, 15):
                logo.putpixel((x, y), (155, 14, 14))

        normalized = _transparent_white_pil_image(logo)

        self.assertEqual(normalized.size, (14, 10))

    def test_additional_header_logos_use_a_centered_second_row(self):
        four_logo_slots = _complement_logo_slots(4)
        five_logo_slots = _complement_logo_slots(5)

        self.assertEqual(len(four_logo_slots), 4)
        self.assertAlmostEqual(four_logo_slots[3][0], four_logo_slots[1][0])
        self.assertGreater(four_logo_slots[3][1], four_logo_slots[0][1])
        self.assertEqual(four_logo_slots[3][2:], four_logo_slots[0][2:])
        self.assertEqual(len(five_logo_slots), 5)
        self.assertLess(five_logo_slots[3][0], 0.5)
        self.assertGreater(five_logo_slots[4][0], 0.5)
        self.assertEqual(five_logo_slots[3][1:], five_logo_slots[4][1:])

    @staticmethod
    def _png_base64() -> str:
        output = BytesIO()
        Image.new('RGBA', (20, 20), (155, 14, 14, 255)).save(output, format='PNG')
        return base64.b64encode(output.getvalue()).decode('ascii')

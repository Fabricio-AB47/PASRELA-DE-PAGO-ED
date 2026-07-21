from django.test import SimpleTestCase

from dashboard_auth.list_exports import ListExportError, build_people_list_export


class PeopleListExportTests(SimpleTestCase):
    def setUp(self):
        self.payload = {
            'kind': 'students',
            'title': 'Listado de estudiantes',
            'rows': [
                {
                    'nombre': 'María Pérez',
                    'correo_personal': 'maria@example.com',
                    'correo_intec': 'maria.perez@intec.edu.ec',
                    'telefono': '072000000',
                    'movil': '0990000000',
                }
            ],
        }

    def test_builds_excel_compatible_xls_with_uppercase_name(self):
        content, content_type, filename = build_people_list_export({**self.payload, 'format': 'xls'})

        text = content.decode('utf-8')
        self.assertEqual(content_type, 'application/vnd.ms-excel')
        self.assertEqual(filename, 'listado-estudiantes.xls')
        self.assertIn('MARÍA PÉREZ', text)
        self.assertIn('maria.perez@intec.edu.ec', text)
        self.assertIn('072000000', text)
        self.assertIn('0990000000', text)

    def test_builds_pdf(self):
        content, content_type, filename = build_people_list_export({**self.payload, 'format': 'pdf'})

        self.assertTrue(content.startswith(b'%PDF'))
        self.assertEqual(content_type, 'application/pdf')
        self.assertEqual(filename, 'listado-estudiantes.pdf')

    def test_rejects_unknown_format(self):
        with self.assertRaises(ListExportError):
            build_people_list_export({**self.payload, 'format': 'csv'})

from unittest import TestCase
from unittest.mock import patch

from dashboard_auth.microsoft365 import (
    create_or_update_institutional_team,
    find_microsoft365_user_by_email,
    upsert_institutional_calendar_event,
)


class Microsoft365TeamTests(TestCase):
    @patch('dashboard_auth.microsoft365._graph_request_with_retry')
    @patch('dashboard_auth.microsoft365._graph_request')
    @patch('dashboard_auth.microsoft365._get_access_token', return_value='token')
    @patch('dashboard_auth.microsoft365._graph_config')
    def test_uses_one_primary_channel_and_all_teachers_as_owners(
        self, graph_config, _token, graph_request, graph_retry,
    ):
        graph_config.return_value = {
            'base_url': 'https://graph.microsoft.com/v1.0',
            'tenant_id': 'tenant-id',
        }
        graph_request.return_value = {
            'value': [{'id': 'group-1', 'displayName': 'Diplomado', 'mailNickname': 'ec-corte-1'}],
        }

        def graph_response(method, endpoint, _token_value, **_kwargs):
            if endpoint.endswith('/primaryChannel'):
                return {'id': 'channel-1', 'displayName': 'General', 'webUrl': 'https://teams/channel/general'}
            if endpoint.endswith('/teams/group-1'):
                return {'id': 'group-1', 'webUrl': 'https://teams/team/group-1'}
            return {}

        graph_retry.side_effect = graph_response

        result = create_or_update_institutional_team(
            cohort_id=1,
            display_name='Diplomado',
            description='Cohorte',
            visibility='Private',
            owner_emails=['docente1@intec.edu.ec', 'docente2@intec.edu.ec', 'docente3@intec.edu.ec'],
            member_emails=['estudiante@intec.edu.ec'],
        )

        self.assertEqual(len(result['owners']), 3)
        self.assertEqual(result['primary_channel']['display_name'], 'General')
        self.assertEqual(result['web_url'], 'https://teams/channel/general')


class Microsoft365ReadOnlyLookupTests(TestCase):
    @patch('dashboard_auth.microsoft365._graph_request')
    @patch('dashboard_auth.microsoft365._get_access_token', return_value='token')
    @patch('dashboard_auth.microsoft365._graph_config')
    def test_existing_email_lookup_uses_get_only(self, graph_config, _token, graph_request):
        graph_config.return_value = {
            'base_url': 'https://graph.microsoft.com/v1.0',
            'domain': 'intec.edu.ec',
        }
        graph_request.return_value = {
            'id': 'user-id',
            'displayName': 'Docente Existente',
            'userPrincipalName': 'existente@intec.edu.ec',
            'accountEnabled': True,
        }

        result = find_microsoft365_user_by_email('existente@intec.edu.ec')

        self.assertTrue(result['exists'])
        self.assertTrue(result['read_only'])
        self.assertEqual(graph_request.call_args.args[0], 'GET')


class Microsoft365CalendarTests(TestCase):
    @patch('dashboard_auth.microsoft365._graph_request_with_retry')
    @patch('dashboard_auth.microsoft365._get_access_token', return_value='token')
    @patch('dashboard_auth.microsoft365._graph_config')
    def test_creates_teams_event_for_institutional_attendees(self, graph_config, _token, graph_request):
        graph_config.return_value = {'base_url': 'https://graph.microsoft.com/v1.0'}
        graph_request.return_value = {'id': 'event-1'}

        result = upsert_institutional_calendar_event(
            organizer_email='docente@intec.edu.ec',
            transaction_id='transaction-1',
            subject='Diplomado - Módulo I',
            body_html='<p>Clase</p>',
            start_datetime='2026-07-20T19:00:00',
            end_datetime='2026-07-20T21:30:00',
            attendee_emails=[
                'docente@intec.edu.ec',
                'estudiante@intec.edu.ec',
                'correo.personal@gmail.com',
            ],
        )

        self.assertEqual(result['id'], 'event-1')
        args = graph_request.call_args.args
        payload = graph_request.call_args.kwargs['body']
        self.assertEqual(args[0], 'POST')
        self.assertTrue(payload['isOnlineMeeting'])
        self.assertEqual(payload['onlineMeetingProvider'], 'teamsForBusiness')
        self.assertEqual(payload['transactionId'], 'transaction-1')
        self.assertEqual(len(payload['attendees']), 1)
        self.assertEqual(payload['attendees'][0]['emailAddress']['address'], 'estudiante@intec.edu.ec')

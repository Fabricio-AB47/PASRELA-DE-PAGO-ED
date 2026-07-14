import json

from django.core.management.base import BaseCommand, CommandError

from dashboard_auth.payments import reconcile_pending_all_digital_payments


class Command(BaseCommand):
    help = 'Valida enlaces pendientes en AllDigital y aplica pagos confirmados en Educación Continua.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)

    def handle(self, *args, **options):
        try:
            result = reconcile_pending_all_digital_payments(
                limit=options['limit'],
                force=True,
                user_login='TAREA_CONCILIACION',
            )
        except Exception as exc:
            raise CommandError('La conciliación automática falló. Revisa el registro del servidor.') from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False, default=str))
        if result.get('errors'):
            self.stdout.write(self.style.WARNING('La conciliación terminó con transacciones pendientes de reintento.'))
        else:
            self.stdout.write(self.style.SUCCESS('Conciliación AllDigital completada.'))

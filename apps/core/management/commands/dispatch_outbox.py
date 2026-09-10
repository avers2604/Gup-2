from django.core.management.base import BaseCommand
from apps.core.outbox import dispatch_pending


class Command(BaseCommand):
    help = "Deliver pending task intents independently of broker/beat availability."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        self.stdout.write(str(dispatch_pending(max(1, min(options["limit"], 10000)))))

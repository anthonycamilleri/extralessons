"""Long-running worker: delivers queued notifications and expires stale offers.

Runs as its own container/process:  python manage.py run_notifier
"""
import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.notifications.worker import run_once

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Deliver queued notifications and expire waiting-list offers (loops forever)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single cycle and exit (useful for cron or tests).",
        )

    def handle(self, *args, **options):
        if options["once"]:
            processed = run_once()
            self.stdout.write(f"Processed {processed} notification(s).")
            return

        self._running = True

        def _stop(signum, frame):
            logger.info("Received signal %s, shutting down after current cycle", signum)
            self._running = False

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        logger.info("run_notifier started")
        while self._running:
            try:
                processed = run_once()
            except Exception:
                logger.exception("Worker cycle crashed; continuing")
                processed = 0
            if processed == 0 and self._running:
                time.sleep(settings.NOTIFIER_IDLE_SLEEP_SECONDS)
        logger.info("run_notifier stopped")

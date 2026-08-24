"""Delivers queued notifications and expires stale offers.

Three modes, because the same command serves two very different runtimes:

  --once            one cycle, then exit. Smallest possible unit of work.
  --drain [--max-seconds N]
                    keep cycling until the queue is empty or the time budget
                    runs out, then exit. This is the mode a scheduled
                    Serverless Job uses: a single cycle only moves
                    NOTIFIER_BATCH_SIZE rows, so a broadcast to 300 families
                    would otherwise trickle out one cron tick at a time.
  (no flag)         loop forever. The container/VM daemon mode.

Exiting on an empty queue is what makes this cheap on serverless: the job
stops billing the moment there is nothing to send.
"""
import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.notifications.worker import run_once

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Deliver queued notifications and expire waiting-list offers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single cycle and exit.",
        )
        parser.add_argument(
            "--drain",
            action="store_true",
            help="Cycle until the queue is empty or --max-seconds elapses, then exit. "
            "Intended for a scheduled Serverless Job.",
        )
        parser.add_argument(
            "--max-seconds",
            type=int,
            default=None,
            help="Time budget for --drain (default: NOTIFIER_DRAIN_MAX_SECONDS).",
        )

    def handle(self, *args, **options):
        if options["once"] and options["drain"]:
            raise CommandError("--once and --drain are mutually exclusive.")

        if options["once"]:
            processed = run_once()
            self.stdout.write(f"Processed {processed} notification(s).")
            return

        self._running = True
        self._install_signal_handlers()

        if options["drain"]:
            budget = options["max_seconds"]
            if budget is None:
                budget = getattr(settings, "NOTIFIER_DRAIN_MAX_SECONDS", 300)
            self._drain(budget)
            return

        logger.info("run_notifier started (daemon mode)")
        while self._running:
            processed = self._cycle()
            if processed == 0 and self._running:
                time.sleep(settings.NOTIFIER_IDLE_SLEEP_SECONDS)
        logger.info("run_notifier stopped")

    def _drain(self, max_seconds):
        deadline = time.monotonic() + max_seconds
        total = 0
        cycles = 0
        while self._running:
            processed = self._cycle()
            cycles += 1
            total += processed
            if processed == 0:
                # Nothing left to claim. Note this is also the only cycle that
                # ran expire_offers on an otherwise idle queue, so a tick that
                # sends nothing is still doing useful work.
                break
            if time.monotonic() >= deadline:
                logger.warning(
                    "Drain budget of %ss exhausted with work still queued; "
                    "the next scheduled run will continue.",
                    max_seconds,
                )
                break
        logger.info("Drained %s notification(s) over %s cycle(s)", total, cycles)
        self.stdout.write(f"Processed {total} notification(s) in {cycles} cycle(s).")

    def _cycle(self):
        """One worker cycle, surviving its own failures.

        A crashed cycle must not take the process down: in daemon mode that
        would stop delivery entirely, and in a scheduled job it would mark the
        run failed and hide the fact that most of the batch went out fine.
        """
        try:
            return run_once()
        except Exception:
            logger.exception("Worker cycle crashed; continuing")
            return 0

    def _install_signal_handlers(self):
        def _stop(signum, frame):
            logger.info("Received signal %s, shutting down after current cycle", signum)
            self._running = False

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

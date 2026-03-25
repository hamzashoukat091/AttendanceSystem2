"""
Background retry worker for PendingAttendanceSync.
Started once at app startup via AccountsConfig.ready() → _start_retry_worker().
Sweeps the DB every POLL_INTERVAL seconds and retries failed attendance API calls.
"""
import threading
import logging
import time

logger = logging.getLogger(__name__)

POLL_INTERVAL = 120   # seconds between sweeps
MAX_RETRIES   = 10    # give up after this many failures

_worker_started = False
_worker_lock    = threading.Lock()


def _backoff_seconds(retry_count: int) -> float:
    """Minimum wait after last attempt before next retry (exponential, capped at 30 min)."""
    if retry_count == 0:
        return 0
    return min(60 * (2 ** (retry_count - 1)), 1800)


def _process_pending():
    from django.utils import timezone as dj_tz
    from accounts.models import PendingAttendanceSync
    from accounts.api_service import post_attendance

    now = dj_tz.now()
    pending = PendingAttendanceSync.objects.filter(
        status=PendingAttendanceSync.STATUS_PENDING,
        retry_count__lt=MAX_RETRIES,
    ).order_by('created_at')

    if not pending.exists():
        return

    logger.info(f"Retry worker sweep: {pending.count()} pending record(s).")

    for record in pending:
        # Respect exponential back-off
        if record.last_attempted:
            elapsed = (now - record.last_attempted).total_seconds()
            required = _backoff_seconds(record.retry_count)
            if elapsed < required:
                logger.debug(
                    f"Skipping id={record.id} user={record.user_id} "
                    f"(back-off: {required:.0f}s required, {elapsed:.0f}s elapsed)"
                )
                continue

        logger.info(
            f"Retrying id={record.id} user={record.user_id} "
            f"type={record.attendance_type} attempt #{record.retry_count + 1}"
        )

        record.last_attempted = now
        result = post_attendance(record.user_id, record.attendance_type)

        if result.get('success'):
            logger.info(f"Retry SUCCESS: id={record.id} user={record.user_id} type={record.attendance_type} — deleting.")
            record.delete()
        else:
            record.retry_count += 1
            record.last_error = result.get('message', 'Unknown error')[:500]

            if record.retry_count >= MAX_RETRIES:
                record.status = PendingAttendanceSync.STATUS_ABANDONED
                logger.error(
                    f"ABANDONED id={record.id} user={record.user_id} type={record.attendance_type} "
                    f"after {MAX_RETRIES} attempts. Last error: {record.last_error}"
                )
            else:
                logger.warning(
                    f"Retry FAILED id={record.id} user={record.user_id} "
                    f"attempt #{record.retry_count}. Error: {record.last_error}"
                )

            record.save(update_fields=['retry_count', 'status', 'last_error', 'last_attempted'])


def _run_retry_loop():
    logger.info("Attendance retry worker started.")
    while True:
        try:
            _process_pending()
        except Exception as e:
            logger.error(f"Retry worker sweep error (non-fatal): {e}", exc_info=True)
        time.sleep(POLL_INTERVAL)


def start_retry_worker():
    """Start the background retry thread exactly once (idempotent)."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
    t = threading.Thread(target=_run_retry_loop, daemon=True, name="attendance-retry-worker")
    t.start()
    logger.info("Attendance retry worker thread launched.")

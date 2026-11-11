"""Background worker that processes pending sync queue items.

Runs in a separate thread, polls the database every few seconds, and processes
pending items with exponential backoff retry on failure.
"""

import json
import threading
import time
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.database import SessionLocal
from app.models.sync_queue import SyncQueueItem
from app.models.external_system import ExternalSystem
from app.services.external_sync_service import process_sync_event, ExternalSyncError

POLL_INTERVAL_SECONDS = 5
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 30  # 30s, 60s, 120s, 240s, 480s

_worker_thread = None
_stop_flag = threading.Event()


def _process_one_item(db: Session, item: SyncQueueItem) -> bool:
    """Process a single queue item. Returns True if successful."""
    system = db.query(ExternalSystem).filter(ExternalSystem.id == item.external_system_id).first()
    if not system or not system.enabled:
        # System disabled or deleted; mark as cancelled
        item.status = "cancelled"
        item.completed_at = datetime.utcnow()
        db.commit()
        return False

    try:
        source_data = json.loads(item.payload_json or "{}")
        process_sync_event(db, system, item.phanero_resource, item.phanero_id, item.operation, source_data)
        item.status = "success"
        item.completed_at = datetime.utcnow()
        item.last_error = ""
        db.commit()
        return True
    except ExternalSyncError as e:
        item.retry_count += 1
        item.last_error = str(e)[:1000]
        if item.retry_count >= MAX_RETRIES:
            item.status = "failed"
            item.completed_at = datetime.utcnow()
        else:
            backoff = BACKOFF_BASE_SECONDS * (2 ** (item.retry_count - 1))
            item.next_attempt_at = datetime.utcnow() + timedelta(seconds=backoff)
            item.status = "pending"
        db.commit()
        return False
    except Exception as e:
        item.retry_count += 1
        item.last_error = f"Unexpected error: {e}"[:1000]
        if item.retry_count >= MAX_RETRIES:
            item.status = "failed"
            item.completed_at = datetime.utcnow()
        else:
            backoff = BACKOFF_BASE_SECONDS * (2 ** (item.retry_count - 1))
            item.next_attempt_at = datetime.utcnow() + timedelta(seconds=backoff)
        db.commit()
        return False


def _worker_loop():
    """Main worker loop. Polls the queue and processes pending items."""
    while not _stop_flag.is_set():
        try:
            db = SessionLocal()
            now = datetime.utcnow()
            # Get pending items whose next_attempt_at has passed (or is null)
            pending = db.query(SyncQueueItem).filter(
                SyncQueueItem.status == "pending",
            ).order_by(SyncQueueItem.created_at).limit(50).all()

            for item in pending:
                if _stop_flag.is_set():
                    break
                if item.next_attempt_at and item.next_attempt_at > now:
                    continue
                _process_one_item(db, item)

            db.close()
        except Exception:
            pass  # Never let the worker thread die

        _stop_flag.wait(POLL_INTERVAL_SECONDS)


def start_worker():
    """Start the background worker thread."""
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_flag.clear()
    _worker_thread = threading.Thread(target=_worker_loop, daemon=True, name="sync-worker")
    _worker_thread.start()


def stop_worker():
    """Stop the background worker thread."""
    _stop_flag.set()


def enqueue_sync(
    db: Session,
    topology_id: int,
    phanero_resource: str,
    phanero_id: int,
    operation: str,
    source_data: dict,
):
    """Enqueue a sync event for all enabled external systems on this topology."""
    systems = db.query(ExternalSystem).filter(
        ExternalSystem.topology_id == topology_id,
        ExternalSystem.enabled == True,
    ).all()
    for system in systems:
        item = SyncQueueItem(
            external_system_id=system.id,
            phanero_resource=phanero_resource,
            phanero_id=phanero_id,
            operation=operation,
            payload_json=json.dumps(source_data),
            status="pending",
        )
        db.add(item)
    db.commit()

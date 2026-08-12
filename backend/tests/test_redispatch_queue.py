import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from app.services.re_dispatch_queue import ReDispatchQueueService

class MockJob:
    def __init__(self, id, priority, status="ASSIGNED", attempt_count=0, bumped_at=None, created_at=None):
        self.id = id
        self.priority = priority
        self.status = status
        self.attempt_count = attempt_count
        self.bumped_at = bumped_at
        self.created_at = created_at or datetime.now(timezone.utc)
        self.previous_priority = None
        self.assigned_technician_id = 123

def test_priority_bump_rules():
    # P1 never bumps
    assert ReDispatchQueueService.calculate_new_priority("P1", 1, False) == ("P1", False)
    assert ReDispatchQueueService.calculate_new_priority("P1", 5, False) == ("P1", False)
    
    # P2 bumps to P1 after 2nd attempt
    assert ReDispatchQueueService.calculate_new_priority("P2", 1, False) == ("P2", False)
    assert ReDispatchQueueService.calculate_new_priority("P2", 2, False) == ("P1", True)
    assert ReDispatchQueueService.calculate_new_priority("P2", 3, False) == ("P1", True)
    
    # P3 bumps to P2 after 1st attempt
    assert ReDispatchQueueService.calculate_new_priority("P3", 1, False) == ("P2", True)
    
    # P4 bumps to P3 after 1st attempt
    assert ReDispatchQueueService.calculate_new_priority("P4", 1, False) == ("P3", True)

def test_max_bump_one_level():
    # If already bumped once, shouldn't bump again even if attempt count says it should
    assert ReDispatchQueueService.calculate_new_priority("P2", 2, True) == ("P2", False)

def test_enqueue_failed_job_logic():
    mock_db = MagicMock()
    mock_redis = MagicMock()
    
    job = MockJob(id=1, priority="P3", attempt_count=0)
    
    res = ReDispatchQueueService.enqueue_failed_job(
        db=mock_db,
        redis_client=mock_redis,
        job=job,
        tenant_id="tenant1",
        reason="test rejection"
    )
    
    assert res["new_status"] == "QUEUED"
    assert res["attempt_count"] == 1
    assert res["priority"] == "P2"
    assert res["bumped"] is True
    
    # Check DB updates
    assert job.status == "QUEUED"
    assert job.attempt_count == 1
    assert job.priority == "P2"
    assert job.previous_priority == "P3"
    assert job.bumped_at is not None
    
    # Check Redis
    mock_redis.zadd.assert_called_once()
    mock_redis.incr.assert_called_with("metrics:queue_insertions")

def test_priority_score():
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=1)
    
    score_p1 = ReDispatchQueueService.calculate_priority_score("P1", now)
    score_p1_old = ReDispatchQueueService.calculate_priority_score("P1", old)
    score_p2 = ReDispatchQueueService.calculate_priority_score("P2", now)
    
    # Older P1 should have higher score than newer P1
    assert score_p1_old > score_p1
    
    # New P1 should have higher score than new P2
    assert score_p1 > score_p2

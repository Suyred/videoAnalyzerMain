import time
from backend import reports, persistence
from backend.security import make_download_token, verify_download_token


def test_expiring_download_token():
    # Configure module secret only for this unit test.
    import backend.security as security
    old = security.DOWNLOAD_SECRET
    security.DOWNLOAD_SECRET = b"unit-test-secret"
    try:
        expires = int(time.time()) + 60
        token = make_download_token("job-a", "user-a", expires)
        assert verify_download_token("job-a", "user-a", expires, token)
        assert not verify_download_token("job-a", "user-b", expires, token)
        assert not verify_download_token("job-a", "user-a", int(time.time()) - 1, token)
    finally:
        security.DOWNLOAD_SECRET = old


def test_csv_report_and_person_timeline():
    sample = {"fps": 10, "totals": {"associations": 2}, "frames": [
        {"frame_number": 0, "timestamp_seconds": 1.0, "associations": [
            {"person_track_id": 7, "phone_track_id": 9, "association_score": 0.8, "confidence": 0.7}
        ]},
        {"frame_number": 10, "timestamp_seconds": 2.0, "associations": [
            {"person_track_id": 7, "phone_track_id": 9, "association_score": 0.9, "confidence": 0.8}
        ]},
    ]}
    people = reports.person_statistics(sample)
    assert people[0]["person_track_id"] == 7
    assert people[0]["associated_frames"] == 2
    assert people[0]["first_seen_seconds"] == 1.0
    assert "person_track_id" in reports.csv_report(sample)


def test_sqlite_job_persistence_roundtrip():
    job_id = "unit-persist-v3"
    job = {"owner_id": "test-user", "status": "queued", "created_at": "2026-01-01T00:00:00+00:00"}
    persistence.save_job(job_id, job)
    try:
        loaded = persistence.load_jobs()
        assert loaded[job_id]["owner_id"] == "test-user"
        assert loaded[job_id]["status"] == "queued"
    finally:
        persistence.delete_job(job_id)


def test_base44_bridge_signature():
    import hashlib, hmac
    from types import SimpleNamespace
    import backend.security as security
    old = security.BRIDGE_SECRET
    security.BRIDGE_SECRET = b"bridge-test-secret"
    try:
        timestamp = str(int(time.time()))
        path = "/internal/base44/jobs"
        user = "base44-user-123"
        message = f"{user}\n{timestamp}\nPOST\n{path}".encode()
        signature = hmac.new(security.BRIDGE_SECRET, message, hashlib.sha256).hexdigest()
        request = SimpleNamespace(headers={"x-base44-user":user,"x-base44-timestamp":timestamp,"x-base44-signature":signature}, method="POST", url=SimpleNamespace(path=path))
        assert security.bridge_identity(request) == user
        request.headers["x-base44-signature"] = "bad"
        from fastapi import HTTPException
        try:
            security.bridge_identity(request)
            assert False, "Invalid signatures must be rejected"
        except HTTPException as exc:
            assert exc.status_code == 401
    finally:
        security.BRIDGE_SECRET = old

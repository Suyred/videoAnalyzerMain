from fastapi.testclient import TestClient

from backend.main import app


def test_health_and_jobs_endpoints_are_available():
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["service"] == "video-analyzer"
        assert isinstance(client.get("/jobs").json(), list)


def test_unknown_job_returns_404():
    with TestClient(app) as client:
        assert client.get("/results/not-a-real-job").status_code == 404
        assert client.get("/summary/not-a-real-job").status_code == 404
        assert client.post("/cancel/not-a-real-job").status_code == 404


def test_upload_rejects_unsupported_file_extension():
    with TestClient(app) as client:
        response = client.post(
            "/analyze",
            files={"file": ("malware.exe", b"not a video", "application/octet-stream")},
        )
        assert response.status_code == 400


def test_upload_settings_are_validated_before_file_is_saved():
    with TestClient(app) as client:
        response = client.post(
            "/analyze",
            files={"file": ("sample.mp4", b"placeholder", "video/mp4")},
            data={"temporal_window": "5", "temporal_min_hits": "7"},
        )
        assert response.status_code == 422


def test_base44_api_requires_bearer_key_and_user_identity(monkeypatch):
    monkeypatch.setenv("BACKEND_API_KEY", "test-secret")
    with TestClient(app) as client:
        missing = client.post("/analyses", json={})
        assert missing.status_code == 401
        no_user = client.post("/analyses", headers={"Authorization": "Bearer test-secret"}, json={})
        assert no_user.status_code == 401


def test_base44_api_rejects_unknown_detection_mode(monkeypatch):
    monkeypatch.setenv("BACKEND_API_KEY", "test-secret")
    headers = {"Authorization": "Bearer test-secret", "X-Base44-User": "user-a"}
    with TestClient(app) as client:
        response = client.post("/analyses", headers=headers, json={
            "video_url": "https://files.example.test/clip.mp4",
            "filename": "clip.mp4",
            "detection_mode": "unknown",
        })
        assert response.status_code == 422


def test_base44_api_creates_user_scoped_job(monkeypatch, tmp_path):
    import backend.main as main

    monkeypatch.setenv("BACKEND_API_KEY", "test-secret")
    monkeypatch.setenv("BASE44_FILE_HOSTS", "files.example.test")
    monkeypatch.setattr(main.persistence, "reserve_request", lambda *_: True)
    monkeypatch.setattr(main, "_download_base44_source", lambda _url, dest: dest.write_bytes(b"video"))

    monkeypatch.setattr(main, "_run_analysis", lambda *_args: None)
    headers = {"Authorization": "Bearer test-secret", "X-Base44-User": "user-a"}
    with TestClient(app) as client:
        created = client.post("/analyses", headers=headers, json={
            "video_url": "https://files.example.test/private/clip.mp4?token=abc",
            "filename": "clip.mp4",
            "detection_mode": "phone_usage",
            "settings": {"confidence": 0.5, "sample_rate": "2fps", "annotated_video": True},
        })
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]
        own = client.get(f"/analyses/{job_id}", headers=headers)
        assert own.status_code == 200
        assert own.json()["status"] == "queued"
        assert own.json()["detection_mode"] == "phone_usage"
        other = client.get(f"/analyses/{job_id}", headers={
            "Authorization": "Bearer test-secret", "X-Base44-User": "user-b"
        })
        assert other.status_code == 404
        cancelled = client.post(f"/analyses/{job_id}/cancel", headers=headers, json={})
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        with main.jobs_lock:
            main.jobs.pop(job_id, None)
            main.cancel_events.pop(job_id, None)


def test_signed_download_url_is_verified_without_legacy_bridge(monkeypatch, tmp_path):
    import time
    import backend.main as main

    monkeypatch.setattr(main.security, "BRIDGE_SECRET", b"")
    monkeypatch.setattr(main.security, "DOWNLOAD_SECRET", b"download-test-secret")
    result = tmp_path / "result.mp4"
    result.write_bytes(b"processed-video")
    job_id = "signed-download-test"
    with main.jobs_lock:
        main.jobs[job_id] = {
            "status": "completed", "owner_id": "user-a", "result_file": str(result),
            "created_at": main._now(),
        }
    expires = int(time.time()) + 300
    try:
        with TestClient(app) as client:
            bad = client.get(f"/download/{job_id}?expires={expires}&token=invalid")
            assert bad.status_code == 403
            token = main.security.make_download_token(job_id, "user-a", expires)
            good = client.get(f"/download/{job_id}?expires={expires}&token={token}")
            assert good.status_code == 200
            assert good.content == b"processed-video"
    finally:
        with main.jobs_lock:
            main.jobs.pop(job_id, None)

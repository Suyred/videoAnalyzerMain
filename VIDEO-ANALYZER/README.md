# VIDEO-ANALYZER — v3 hybrid Base44 + Python worker

A Python/OpenCV video worker with a Base44 integration scaffold. Base44 is intended to own sign-in, per-user job records and app-side permissions; Python performs CPU-heavy video inference.

## Architecture decision

Base44 provides a NoSQL database, built-in authentication, row/field-level permissions, backend functions and file storage. Its backend functions have a documented 5-minute execution limit and 128 MB memory ceiling, so the YOLO/OpenCV video pipeline stays in Python rather than running inside a Base44 function. See `BASE44_SETUP.md`.

## Implemented in this ZIP

- Existing YOLOv8n ONNX person/cell-phone detection and red boxes only around people conservatively associated with a phone.
- SQLite persistence for job metadata and per-user hourly request counters.
- Restart recovery for queued/processing jobs when uploads, results and SQLite database are on persistent storage.
- HMAC-signed Base44-to-worker identity headers with timestamp expiry.
- Owner checks on job status, listing, cancellation, deletion and report endpoints when bridge auth is configured.
- HMAC-signed temporary video download links (30 seconds to 1 hour).
- Per-user upload rate limit, queue limit, file size and duration limits.
- Batch video upload endpoint (`POST /analyze/batch`, up to `MAX_BATCH_FILES`).
- Start/end time selection and output width selection.
- Progress plus ETA estimate.
- JSON, CSV and per-person time summary endpoints.
- Motion-predicted lightweight tracking to reduce short-term ID switches.
- Association output includes detector confidences, spatial score, combined heuristic confidence and a `pose_hint`.

## Important honesty about detection

`pose_hint` is a geometric clue based on the phone's location inside a person bounding box. It is **not** learned hand-keypoint or full-body pose recognition. `confidence` is a combined heuristic score, not a calibrated probability. The system does not identify people by face or real-world identity; track IDs are temporary per video. Crowded scenes and occlusion can still cause missed or incorrect associations.

A dedicated pose/keypoint model is intentionally not bundled: selecting and licensing a separate model and benchmarking it against representative videos is a meaningful ML task, not a safe one-line add-on.

## Base44 resources

- `base44/entities/VideoJob.json`: owner-scoped job entity.
- `base44/functions/submitVideoJob/entry.ts`: requires a signed-in Base44 user and submits a stored video URL to the worker.
- `base44/functions/workerCallback/entry.ts`: receives authenticated status callbacks.
- `base44/functions/getVideoJobDownloadLink/entry.ts`: verifies ownership and creates a fresh temporary download URL.
- `BASE44_SETUP.md`: setup, secrets and frontend invocation instructions.

The ZIP cannot deploy to your Base44 workspace automatically: it has no app ID, workspace credentials or permission to modify your account. The actual storage hostname must be copied from your Base44 file-upload response and set in `BASE44_FILE_HOSTS`; the worker deliberately rejects arbitrary URLs to prevent SSRF.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Health/model status |
| POST | `/analyze` | Single video upload |
| POST | `/analyze/batch` | Batch upload |
| GET | `/results/{job_id}` | Status, progress and ETA |
| GET | `/jobs` | Current user's jobs |
| POST | `/cancel/{job_id}` | Cancel job |
| POST | `/download-link/{job_id}` | Create short-lived download URL |
| GET | `/download/{job_id}` | Download annotated MP4 |
| GET | `/summary/{job_id}` | JSON frame/association report |
| GET | `/report/{job_id}.csv` | CSV association report |
| GET | `/report/{job_id}/people` | Per-person timing statistics |
| DELETE | `/jobs/{job_id}` | Delete finished job |
| POST | `/internal/base44/jobs` | Signed Base44 worker bridge endpoint |
| POST | `/analyses` | Base44-compatible JSON submission (Bearer key + user header) |
| GET | `/analyses/{id}` | Base44-compatible status and temporary result URL |
| POST | `/analyses/{id}/cancel` | Base44-compatible cancellation |

Legacy upload remains compatible with the existing frontend when `AUTH_REQUIRED=false` and no bridge secret is configured. For deployment, use the Base44 bridge and set `AUTH_REQUIRED=true`.

## New upload settings

Additional multipart fields:

- `start_seconds` / `end_seconds`: selected time segment. `end_seconds=0` means through the end.
- `output_width`: `0` (original), `480`, `720`, `1080`, `1280` or `1920`. Output is never upscaled.
- Existing detector/tracker settings remain available on `/analyze`.

## Persistence, queue and deployment limits

- SQLite is a durable **single-instance** job store; the existing queue executes in the worker process with a concurrency semaphore. It is not a distributed multi-instance queue.
- Restart recovery works only if the SQLite database and upload files survive the restart.
- Render's free filesystem is ephemeral. A persistent disk or external object storage is required for reliable upload/output retention; persistent disks may require a paid Render plan. The included free Render blueprint is a starting template, not a guarantee of durable storage.
- The Base44 entity keeps job metadata. The output URL returned to Base44 is a 10-minute signed URL served by the worker; this version does not copy large MP4 results back into Base44 storage.
- Keep `MAX_CONCURRENT_JOBS=1` on a small CPU instance. Multiple simultaneous uploads are queued and processed sequentially.

## Local installation

Python 3.11 recommended:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Run tests:

```bash
pytest -q
```

## Render environment

Copy `.env.example` values into Render environment settings. Set `AUTH_REQUIRED=true`, use strong random secrets, set `PUBLIC_API_URL`, `BASE44_CALLBACK_URL`, and exact `BASE44_FILE_HOSTS`. Mount persistent storage before relying on restart recovery. Configure `CORS_ORIGINS` to the exact Base44 app origin.

## Model

`models/yolov8n.onnx` is bundled and SHA-256 verified by the detector. The upstream model repository identifies AGPL-3.0 licensing; review its license and any applicable terms before commercial/proprietary use.

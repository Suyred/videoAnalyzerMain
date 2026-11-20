# Base44 integration (hybrid architecture)

## Why this is hybrid

Base44 is the system of record for sign-in, users, job ownership, and job metadata. The Python service remains the video worker. Base44 backend functions have a documented 5-minute execution ceiling and 128 MB memory ceiling, so YOLO/OpenCV video inference must not run inside a Base44 function.

## Included Base44 resources

- `base44/entities/VideoJob.json`: private job records with owner-based row-level access.
- `base44/functions/submitVideoJob/entry.ts`: requires a signed-in Base44 user, creates the job, and forwards the uploaded Base44 file URL to the worker using an HMAC-signed bridge request.
- `base44/functions/workerCallback/entry.ts`: accepts worker status callbacks using a separate secret and updates the matching job via service role.

## Setup checklist

1. In the existing Base44 app, add the `VideoJob` entity schema and the two functions from this folder. Do not create a second app or replace the existing frontend.
2. In Base44 Secrets, set `VIDEO_WORKER_URL`, `BASE44_BRIDGE_SECRET`, and `WORKER_CALLBACK_SECRET`.
3. On the Python worker, set the matching `BASE44_BRIDGE_SECRET` and `WORKER_CALLBACK_SECRET`, plus `DOWNLOAD_SIGNING_SECRET`, `PUBLIC_API_URL`, and `BASE44_CALLBACK_URL`.
4. Upload a video to Base44's file storage using the existing app upload flow. The upload flow must return an HTTPS file URL. Call `submitVideoJob` with `{ "file_url": "...", "settings": { ... } }`.
5. Set `BASE44_FILE_HOSTS` on the worker to the exact hostname in the returned file URL. This is an SSRF safety allow-list; do not set it to `*`.
6. Mount a persistent disk on the Python worker and set `UPLOAD_DIR`, `RESULTS_DIR`, and `DB_PATH` to that disk. Without persistent storage, no local queue/database can survive an instance replacement.
7. Configure CORS to the exact Base44 app origin. Never put bridge or callback secrets in frontend code.
8. Run Base44's security scan and test two different user accounts: each must see only their own `VideoJob` records.

## Frontend call

The frontend should upload the selected video to Base44 storage, then call:

```ts
await base44.functions.invoke("submitVideoJob", {
  file_url: uploadedFileUrl,
  settings: {
    start_seconds: 0,
    end_seconds: 0,
    output_width: 720,
    frame_stride: 2,
    max_width: 960
  }
});
```

The Base44 SDK passes the signed-in user's authentication to the function. The function, not the browser, signs the request sent to the Python worker. To create a fresh result link, call `base44.functions.invoke("getVideoJobDownloadLink", { job_id: base44JobId })`.

## Important limitations / items requiring account-side configuration

- This ZIP cannot deploy into your Base44 workspace because it does not contain your app ID, workspace secrets, or permission to access your Base44 account.
- The exact Base44 storage hostname varies by app/storage response; set `BASE44_FILE_HOSTS` only after observing the actual returned URL.
- The callback stores the worker's expiring result URL in `VideoJob`. The link expires after 10 minutes; request a new link through the worker when needed. The current callback does not yet copy the output file back into Base44 storage.
- SQLite + a persistent disk gives durable single-instance job state and restart recovery. It is not a distributed multi-instance queue. Run exactly one worker instance unless a shared queue/locking service is added.
- The current worker can process several queued uploads sequentially; `MAX_CONCURRENT_JOBS` controls actual parallel inference. On a free/small CPU instance, keep it at 1.
- Pose/hand evidence is currently a geometric heuristic around the detected body box, not a learned keypoint detector. It is reported as `pose_hint` and must not be described as verified hand-pose recognition. A real pose model would need a separately selected, licensed model artifact and benchmark on target videos.
- Detection confidence is a combined heuristic from person confidence, phone confidence, and spatial association score. It is not a calibrated probability.

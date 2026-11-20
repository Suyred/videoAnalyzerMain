# Changelog

## 3.0.0 — Base44 hybrid backend upgrade

- Added Base44 `VideoJob` entity and authenticated submit/callback/download-link functions.
- Added HMAC-authenticated Base44-to-Python worker bridge and owner-scoped job access.
- Added SQLite persistence, per-user hourly request limits, signed temporary download links, batch upload, time-range selection, output width selection, ETA, CSV export and per-person timeline reports.
- Improved lightweight tracking with motion prediction and added detector/association confidence metadata.
- Added restart recovery when the database and working files are on persistent storage.
- Preserved the original single-video API for local/backward-compatible use.
- Important limits: queue is single-instance (not distributed); pose hint is geometric only, not learned hand/body keypoint recognition; Base44/Render deployment still requires account secrets and storage configuration.


## Version 2.0.0

### Video analysis and matching
- Kept the OpenCV DNN + ONNX runtime; removed the PyTorch/Ultralytics runtime dependency.
- Kept class-aware temporary person/phone tracking.
- Replaced broad proximity matching with a normalized person-box position score and configurable margins.
- Added conservative ambiguity rejection when two people are nearly equally plausible for one phone.
- Kept temporal confirmation and preserved association history through short tracker gaps.
- Kept the strict rendering rule: only currently visible, confirmed people get red boxes; no phone boxes and no boxes around unassociated people.

### Processing controls
- Added per-upload person and phone confidence thresholds.
- Added horizontal/vertical association expansion, ambiguity margin, temporal window/hits and tracker gap/movement settings.
- Added per-upload frame stride, inference width and maximum duration controls.
- Added frame-level diagnostics and aggregate counts in a JSON summary.
- Added processing progress and cooperative cancellation.

### API and job management
- Preserved `GET /health`, `POST /analyze`, `GET /results/{job_id}` and `GET /download/{job_id}`.
- Added `GET /jobs`, `POST /cancel/{job_id}`, `GET /summary/{job_id}` and `DELETE /jobs/{job_id}`.
- Added bounded in-process job queue and configurable concurrency.
- Added atomic local job metadata persistence and explicit interrupted-job handling after restart.
- Added age-based cleanup of finished job files when API requests arrive.

### Reliability and deployment
- Added `.env` loading and startup configuration validation.
- Improved model download cleanup and checksum failure handling.
- Added Docker and Render deployment settings for a low-resource single worker.
- Expanded tests to include detector output decoding, generated-video processing, progress, cancellation, API behavior and crowded-scene ambiguity.

### Known limitations
- The model file is downloaded during setup; it is not embedded in the ZIP.
- Real ONNX inference against varied real-world videos and an actual Render deployment have not been validated in this build session.
- The tracker and phone/person association are geometric heuristics, not pose/hand understanding.
- The job queue and JSON metadata store are local to one process/instance. Render free storage is ephemeral.
- The API does not include authentication or per-user authorization. Do not expose private videos publicly without adding an authenticated access layer.


## v4 — Base44 API compatibility

- Added `POST /analyses`, `GET /analyses/{id}`, and `POST /analyses/{id}/cancel` to match the current Base44 service contract.
- Added server-to-server Bearer authentication using `BACKEND_API_KEY` and mandatory `X-Base44-User` scoping for every analysis request.
- Added HTTPS host allow-listing for Base44 private video URLs, including redirect destination checks and upload-size enforcement.
- Added temporary signed result URLs and normalized Base44 status fields.
- Kept the previous `/analyze`, `/results`, HMAC bridge, and existing detector pipeline intact.
- Important: detection mode is recorded, but both modes currently share the existing phone/person association algorithm; phone usage is not yet independently classified.

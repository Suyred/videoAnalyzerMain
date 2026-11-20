# Verification report — 2026-11-19

## Automated tests

- `python -m compileall -q backend`: passed.
- `pytest -q`: **28 passed**.
- Model SHA-256 matches the pinned expected digest: `fa2c856b0effedce4f49fffd121f438247a64c8f633bf0e6d5e5960e869ccc5a`.

## Real model/video test

Input: `7652287-sd_960_540_30fps.mp4` (224 frames, 960x540, 30 FPS).

- ONNX model loaded with OpenCV DNN.
- Processed all 224 frames; inference ran on 112 sampled frames.
- Output MP4 created successfully at 720px width.
- Summary JSON created successfully.
- Observed 19 phone/person association pairs across 17 frames; maximum 7 people and 2 phones detected in a frame.
- Separate segment test for seconds 1–4 created a 3-second, 480x270 MP4.

These counts describe one sample video only and are not an accuracy benchmark or guarantee.

## API end-to-end test

- Uploaded the sample video through `POST /analyze`.
- Job completed and `GET /results/{job_id}` returned `completed`.
- CSV report, per-person report and MP4 download endpoints returned HTTP 200.

## Security test

- Signed Base44 bridge identity accepted for the owner.
- Another user received HTTP 404 for the owner's job.
- A valid temporary signed download URL returned the output successfully.

## Not tested / not deployed

- No live Base44 workspace was connected or modified; the ZIP contains the entity/function integration files only.
- No live Render deployment was performed.
- The Base44 storage hostname must be set in `BASE44_FILE_HOSTS` after checking the actual uploaded-file URL.
- The worker uses a persistent single-instance SQLite queue, not a distributed queue service.
- Learned hand/body pose keypoint detection is not included. `pose_hint` is a geometric heuristic only.
- On Render Free, local files are ephemeral; restart recovery requires persistent storage or an external object store.


## v4 compatibility checks

- Added tests for missing/invalid Bearer credentials and missing Base44 user identity.
- Added tests for unsupported detection mode and owner isolation across `/analyses/{id}`.
- Added tests for the create/status/cancel lifecycle using a mocked source download (no external network).
- `phone_in_hand` and `phone_usage` are accepted and stored as requested mode metadata; they currently share the same underlying detector pipeline.

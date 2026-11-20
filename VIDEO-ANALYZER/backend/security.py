"""Base44-to-worker request authentication and expiring download signatures."""
from __future__ import annotations
import hashlib, hmac, os, time
from fastapi import HTTPException, Request

BRIDGE_SECRET = os.getenv('BASE44_BRIDGE_SECRET', '').encode()
DOWNLOAD_SECRET = os.getenv('DOWNLOAD_SIGNING_SECRET', '').encode() or BRIDGE_SECRET
AUTH_REQUIRED = os.getenv('AUTH_REQUIRED', 'false').lower() == 'true'
RATE_LIMIT = int(os.getenv('RATE_LIMIT_PER_HOUR', '20'))
MAX_BATCH = int(os.getenv('MAX_BATCH_FILES', '5'))


def bridge_identity(request: Request) -> str:
    """Verify identity headers signed by a trusted Base44 server function."""
    user_id = request.headers.get('x-base44-user', '').strip()
    timestamp = request.headers.get('x-base44-timestamp', '').strip()
    signature = request.headers.get('x-base44-signature', '').strip()
    if not BRIDGE_SECRET:
        if AUTH_REQUIRED:
            raise HTTPException(503, 'Base44 bridge is not configured.')
        return 'local'
    if not user_id or not timestamp or not signature:
        raise HTTPException(401, 'Authentication required.')
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(401, 'Invalid authentication timestamp.')
    if abs(time.time() - ts) > 300:
        raise HTTPException(401, 'Authentication signature expired.')
    message = f'{user_id}\n{timestamp}\n{request.method}\n{request.url.path}'.encode()
    expected = hmac.new(BRIDGE_SECRET, message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, 'Invalid authentication signature.')
    return user_id


def make_download_token(job_id: str, owner_id: str, expires_at: int) -> str:
    if not DOWNLOAD_SECRET:
        raise RuntimeError('DOWNLOAD_SIGNING_SECRET is not configured')
    payload = f'{job_id}:{owner_id}:{expires_at}'.encode()
    return hmac.new(DOWNLOAD_SECRET, payload, hashlib.sha256).hexdigest()


def verify_download_token(job_id: str, owner_id: str, expires_at: int, token: str) -> bool:
    if expires_at < int(time.time()) or not DOWNLOAD_SECRET:
        return False
    return hmac.compare_digest(make_download_token(job_id, owner_id, expires_at), token)

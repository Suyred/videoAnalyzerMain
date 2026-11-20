import { createClientFromRequest } from "npm:@base44/sdk";
import { secrets } from "base44:runtime";

// Creates a fresh 10-minute download URL after verifying Base44 ownership.
export default async function (req: Request): Promise<Response> {
  try {
    const base44 = createClientFromRequest(req);
    const user = await base44.auth.me();
    if (!user) return Response.json({ error: "Unauthorized" }, { status: 401 });
    const { job_id } = await req.json();
    if (!job_id) return Response.json({ error: "job_id is required" }, { status: 400 });

    const job = await base44.entities.VideoJob.get(String(job_id));
    if (!job || job.owner_id !== user.id) return Response.json({ error: "Not found" }, { status: 404 });
    if (job.status !== "completed" || !job.worker_job_id) return Response.json({ error: "Result is not ready" }, { status: 409 });

    const workerUrl = secrets.get("VIDEO_WORKER_URL");
    const bridgeSecret = secrets.get("BASE44_BRIDGE_SECRET");
    if (!workerUrl || !bridgeSecret) return Response.json({ error: "Worker integration is not configured" }, { status: 503 });

    const path = `/download-link/${job.worker_job_id}`;
    const timestamp = String(Math.floor(Date.now() / 1000));
    const message = `${user.id}\n${timestamp}\nPOST\n${path}`;
    const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(bridgeSecret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const signatureBytes = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
    const signature = [...new Uint8Array(signatureBytes)].map((b) => b.toString(16).padStart(2, "0")).join("");

    const response = await fetch(`${workerUrl.replace(/\/$/, "")}${path}?ttl_seconds=600`, {
      method: "POST",
      headers: { "X-Base44-User": user.id, "X-Base44-Timestamp": timestamp, "X-Base44-Signature": signature }
    });
    if (!response.ok) return Response.json({ error: "Could not create download link" }, { status: response.status });
    const link = await response.json();
    await base44.entities.VideoJob.update(job.id, { result_file_url: link.url });
    return Response.json(link);
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Unexpected error" }, { status: 500 });
  }
}

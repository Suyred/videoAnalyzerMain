import { createClientFromRequest } from "npm:@base44/sdk";
import { secrets } from "base44:runtime";

// Base44 owns user identity and job records. Heavy video inference stays on the Python worker.
export default async function (req: Request): Promise<Response> {
  try {
    const base44 = createClientFromRequest(req);
    const user = await base44.auth.me();
    if (!user) return Response.json({ error: "Unauthorized" }, { status: 401 });

    const body = await req.json();
    const fileUrl = String(body.file_url || "");
    if (!fileUrl.startsWith("https://")) {
      return Response.json({ error: "Upload the video to Base44 storage first and pass its HTTPS file URL." }, { status: 400 });
    }
    const workerUrl = secrets.get("VIDEO_WORKER_URL");
    const bridgeSecret = secrets.get("BASE44_BRIDGE_SECRET");
    if (!workerUrl || !bridgeSecret) {
      return Response.json({ error: "Worker integration secrets are not configured." }, { status: 503 });
    }

    const job = await base44.asServiceRole.entities.VideoJob.create({
      owner_id: user.id,
      status: "queued",
      progress: 0,
      source_file_url: fileUrl,
      settings: body.settings || {}
    });

    const timestamp = String(Math.floor(Date.now() / 1000));
    const path = "/internal/base44/jobs";
    const message = `${user.id}\n${timestamp}\nPOST\n${path}`;
    const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(bridgeSecret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const signatureBytes = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
    const signature = [...new Uint8Array(signatureBytes)].map((b) => b.toString(16).padStart(2, "0")).join("");

    const response = await fetch(`${workerUrl.replace(/\/$/, "")}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Base44-User": user.id, "X-Base44-Timestamp": timestamp, "X-Base44-Signature": signature },
      body: JSON.stringify({ base44_job_id: job.id, file_url: fileUrl, settings: body.settings || {} })
    });
    if (!response.ok) {
      await base44.asServiceRole.entities.VideoJob.update(job.id, { status: "failed", error_message: `Worker rejected the job (${response.status}).` });
      return Response.json({ error: "Worker could not accept the job." }, { status: 502 });
    }
    const accepted = await response.json();
    await base44.asServiceRole.entities.VideoJob.update(job.id, { worker_job_id: accepted.job_id || "" });
    return Response.json({ id: job.id, status: "queued" }, { status: 202 });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Unexpected error" }, { status: 500 });
  }
}

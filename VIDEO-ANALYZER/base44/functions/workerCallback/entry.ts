import { createClientFromRequest } from "npm:@base44/sdk";
import { secrets } from "base44:runtime";

// Called only by the Python worker using a separate callback secret.
export default async function (req: Request): Promise<Response> {
  const expected = secrets.get("WORKER_CALLBACK_SECRET");
  const supplied = req.headers.get("x-worker-callback-secret");
  if (!expected || !supplied || supplied !== expected) return Response.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const base44 = createClientFromRequest(req);
    const body = await req.json();
    if (!body.job_id || !body.status) return Response.json({ error: "Invalid payload" }, { status: 400 });
    const rows = await base44.asServiceRole.entities.VideoJob.filter({ worker_job_id: body.job_id });
    if (!rows.length) return Response.json({ error: "Job not found" }, { status: 404 });
    const job = rows[0];
    await base44.asServiceRole.entities.VideoJob.update(job.id, {
      status: body.status,
      progress: body.progress ?? job.progress ?? 0,
      eta_seconds: body.eta_seconds ?? null,
      result_file_url: body.result_file_url ?? job.result_file_url ?? "",
      report_file_url: body.report_file_url ?? job.report_file_url ?? "",
      error_message: body.error ?? "",
      summary: body.summary ?? job.summary ?? {}
    });
    return Response.json({ ok: true });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Unexpected error" }, { status: 500 });
  }
}

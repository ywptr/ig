import type { Job } from "../types/Job";

export function jobDurationSeconds(job: Job, now = Date.now()): number | null {
    if (!job.started_at) return null;
    const start = Date.parse(job.started_at);
    const end = job.completed_at ? Date.parse(job.completed_at) : now;
    if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
    return Math.max(0, Math.round((end - start) / 1000));
}

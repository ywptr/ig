import type { Job, JobDetail } from "../types/Job";
import type { JobExecution } from "./executions";
import { requestJSON } from "./client";

export function getJobs(signal?: AbortSignal): Promise<Job[]> { return requestJSON("/v2/jobs", { signal }); }
export function getJob(id: string, signal?: AbortSignal): Promise<JobDetail> {
  return requestJSON(`/v2/jobs/${encodeURIComponent(id)}`, { signal });
}
export function getJobExecutions(id: string, signal?: AbortSignal): Promise<JobExecution[]> {
  return requestJSON(`/v2/jobs/${encodeURIComponent(id)}/executions`, { signal });
}

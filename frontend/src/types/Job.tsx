import type { JobStatus } from "./media";
export interface Job {
  job_id: string;
  capability: string;
  status: JobStatus;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}
export interface JobDetail extends Job { input: Record<string, unknown> }

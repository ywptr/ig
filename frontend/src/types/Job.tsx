import type { JobStatus } from "./media";
export interface Job {
  job_id: string;
  capability: string;
  status: JobStatus;
  error_message: string | null;
  error_code?: string | null;
  editor_attempt_id?: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  cancel_requested_at?: string | null;
  canceled_at?: string | null;
}
export interface JobDetail extends Job { input: Record<string, unknown> }

import { requestJSON } from "./client";
import type { JobStatus } from "../types/media";
export interface JobExecution {
    execution_id: string;
    status: JobStatus;
    attempt: number;
    provider: string | null;
    model: string | null;
    metadata: {
        capability?: string;
        artifact_id?: string;
        request_id?: string;
        [key: string]: unknown;
    } | null;
    started_at: string | null;
    completed_at: string | null;
    error_message: string | null;
}
export interface Execution extends JobExecution {
    job_id: string;
    user_id: string | null;
    created_at: string;
}


export function getExecutions(signal?: AbortSignal): Promise<Execution[]> {
    return requestJSON("/v2/executions", { signal });
}

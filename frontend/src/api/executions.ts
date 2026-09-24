export interface Execution {
    execution_id: string;
    job_id: string;
    user_id: string;
    status: string;
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
    created_at: string;
}


export async function getExecutions(): Promise<Execution[]> {
    const response = await fetch(
        "/v2/executions"
    );

    if (!response.ok) {
        throw new Error(
            `Failed to load executions: ${response.status}`
        );
    }

    return response.json();
}
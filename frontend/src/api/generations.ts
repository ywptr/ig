import { getJob, getJobExecutions, getJobs } from "./jobs";
import { getArtifact } from "./artifacts";
import { ApiError, safeMessage } from "./client";
import { executionArtifactId, generationMediaType, isActiveStatus, latestExecution, resultFromJob } from "../lib/generation";
import type { Job, JobDetail } from "../types/Job";
import type { GenerationResult } from "../types/media";

export interface GenerationSnapshot { jobs: Job[]; results: GenerationResult[] }
export const EMPTY_SNAPSHOT: GenerationSnapshot = { jobs: [], results: [] };

export function createGenerationLoader() {
    // Owned by one authenticated workspace. Inputs and completed artifacts are
    // immutable; no global/user-crossing cache and no permanent browser storage.
    const details = new Map<string, JobDetail>();
    const completed = new Map<string, GenerationResult>();
    return async (signal: AbortSignal): Promise<GenerationSnapshot> => {
        const jobs = await getJobs(signal);
        const media = jobs.filter(job => generationMediaType(job.capability));
        const tracked = [...media.filter(job => isActiveStatus(job.status)),
            ...media.filter(job => !isActiveStatus(job.status)).slice(0, 20)];
        const results: GenerationResult[] = new Array(tracked.length);
        let cursor = 0;
        // Bound request fan-out when recovering a busy user's recent Jobs.
        await Promise.all(Array.from({ length: Math.min(4, tracked.length) }, async () => {
            while (cursor < tracked.length) {
                const index = cursor++;
                const summary = tracked[index];
                let detail: JobDetail = { ...summary, input: {} };
                try {
                    const cached = completed.get(summary.job_id);
                    if (cached && cached.status === summary.status && cached.error === summary.error_message) {
                        results[index] = cached;
                        continue;
                    }
                    const input = details.get(summary.job_id) ?? await getJob(summary.job_id, signal);
                    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
                    details.set(summary.job_id, input);
                    detail = { ...input, ...summary };
                    const terminal = !isActiveStatus(summary.status);
                    const execution = terminal ? latestExecution(await getJobExecutions(summary.job_id, signal)) : undefined;
                    const artifactId = executionArtifactId(execution);
                    const artifact = artifactId ? await getArtifact(artifactId, signal) : null;
                    if (artifact && artifact.media_type !== generationMediaType(summary.capability)) throw new Error("IG returned a different media type for this result.");
                    const result = resultFromJob(detail, execution, artifact);
                    if (summary.status === "completed" && !artifact) result.error = "Completed Job has no linked Artifact. Refresh or inspect its Executions.";
                    results[index] = result;
                    if (terminal && (artifact || summary.status === "failed" || summary.status === "canceled")) completed.set(summary.job_id, result);
                } catch (error) {
                    if (signal.aborted || error instanceof ApiError && error.status === 401) throw error;
                    results[index] = { ...resultFromJob(detail), error: safeMessage(error instanceof Error ? error.message : null, "Unable to load this result. Refresh to try again.") };
                }
            }
        }));
        // Keep the cache bounded to the same recent window as the server.
        const ids = new Set(jobs.map(job => job.job_id));
        for (const id of details.keys()) if (!ids.has(id)) details.delete(id);
        for (const id of completed.keys()) if (!ids.has(id)) completed.delete(id);
        return { jobs, results: results.sort((a, b) => b.created_at.localeCompare(a.created_at)) };
    };
}

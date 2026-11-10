import { useState } from "react";
import { cancelJob } from "../api/jobs";
import { jobDurationSeconds } from "../lib/jobDuration";
import { humanize, isActiveStatus } from "../lib/generation";
import { safeMessage } from "../api/client";
import type { Job } from "../types/Job";


function formatTime(value: string | null) {
    if (!value) {
        return "—";
    }

    return new Date(value).toLocaleString();
}


function formatDuration(job: Job) {
    const seconds = jobDurationSeconds(job);
    return seconds === null ? "—" : `${seconds}s`;
}


export function Jobs({ jobs, error, loading, onRefresh }: {
    jobs: Job[]; error: string | null; loading: boolean; onRefresh: () => void;
}) {
    const [busy, setBusy] = useState<Record<string, boolean>>({});
    const [cancelError, setCancelError] = useState<string | null>(null);
    const [updates, setUpdates] = useState<Record<string, Job>>({});
    async function cancel(id: string) {
        setBusy(previous => ({ ...previous, [id]: true }));
        setCancelError(null);
        try {
            const updated = await cancelJob(id);
            setUpdates(previous => ({ ...previous, [id]: updated }));
            onRefresh();
        } catch (failure) {
            setCancelError(safeMessage(failure instanceof Error ? failure.message : null, "Unable to cancel Job."));
            onRefresh();
        } finally {
            setBusy(previous => ({ ...previous, [id]: false }));
        }
    }
    const displayedJobs = jobs.map(job => isActiveStatus(job.status) && updates[job.job_id]
        ? { ...job, ...updates[job.job_id] } : job);

    return (
        <div className="jobs">

            <div className="section-heading">
                <h2>Jobs</h2>
                <button className="secondary-button" onClick={onRefresh} disabled={loading}>{loading ? "Refreshing…" : "Refresh Jobs"}</button>

                <span className="image-count">
                    {jobs.length}
                    {" "}
                    {jobs.length === 1
                        ? "job"
                        : "jobs"}
                </span>
            </div>


            {(error || cancelError) && (
                <div className="error" role="alert">
                    {cancelError || error}
                </div>
            )}


            <div className="job-list">

                {displayedJobs.map((job) => (

                    <div
                        className="job-card"
                        key={job.job_id}
                    >

                        <div className="job-header">

                            <strong>
                                {job.capability}
                            </strong>

                            <span
                                className={
                                    `job-status ${job.status}`
                                }
                            >
                                {humanize(job.status)}
                            </span>

                        </div>


                        {["queued", "running", "waiting", "polling"].includes(job.status) && (
                            <button className="secondary-button" disabled={busy[job.job_id]}
                                aria-label={`Cancel job ${job.job_id}`} onClick={() => void cancel(job.job_id)}>
                                {busy[job.job_id] ? "Requesting cancellation…" : "Cancel"}
                            </button>
                        )}
                        {job.status === "cancel_requested" && <p role="status">Cancel requested; waiting for a safe checkpoint. The provider may still be processing.</p>}

                        <div className="job-id">
                            {job.job_id}
                        </div>


                        <div className="job-details">

                            <div>
                                <span>Created</span>
                                {formatTime(job.created_at)}
                            </div>

                            <div>
                                <span>Started</span>
                                {formatTime(job.started_at)}
                            </div>

                            <div>
                                <span>Duration</span>
                                {formatDuration(job)}
                            </div>

                        </div>


                        {job.error_message && (
                            <div className="job-error">
                                <strong>Error</strong>
                                {safeMessage(job.error_message)}
                            </div>
                        )}

                    </div>

                ))}

            </div>

        </div>
    );
}

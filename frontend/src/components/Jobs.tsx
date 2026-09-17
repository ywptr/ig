import { useEffect, useState } from "react";
import { getJobs } from "../api/jobs";
import type { Job } from "../types/Job";


function formatTime(value: string | null) {
    if (!value) {
        return "—";
    }

    return new Date(value).toLocaleString();
}


function formatDuration(job: Job) {
    if (!job.started_at) {
        return "—";
    }

    const start = new Date(job.started_at).getTime();

    const end = job.completed_at
        ? new Date(job.completed_at).getTime()
        : Date.now();

    const seconds = Math.max(
        0,
        Math.round((end - start) / 1000)
    );

    return `${seconds}s`;
}


export function Jobs() {
    const [jobs, setJobs] = useState<Job[]>([]);
    const [error, setError] =
        useState<string | null>(null);


    async function loadJobs() {
        try {
            const result = await getJobs();

            setJobs(result);
            setError(null);

        } catch (error) {
            setError(
                error instanceof Error
                    ? error.message
                    : "Failed to load jobs"
            );
        }
    }


    useEffect(() => {
        loadJobs();

        const interval = window.setInterval(
            loadJobs,
            3000
        );

        return () => {
            window.clearInterval(interval);
        };
    }, []);


    return (
        <div className="jobs">

            <div className="section-heading">
                <h2>Jobs</h2>

                <span className="image-count">
                    {jobs.length}
                    {" "}
                    {jobs.length === 1
                        ? "job"
                        : "jobs"}
                </span>
            </div>


            {error && (
                <div className="error">
                    {error}
                </div>
            )}


            <div className="job-list">

                {jobs.map((job) => (

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
                                {job.status}
                            </span>

                        </div>


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
                                {job.error_message}
                            </div>
                        )}

                    </div>

                ))}

            </div>

        </div>
    );
}
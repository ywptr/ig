import { useActivePolling } from "../hooks/useActivePolling";
import { isActiveStatus } from "../lib/generation";
import { safeMessage } from "../api/client";

import {
    getExecutions,
    type Execution,
} from "../api/executions";


function formatDate(
    value: string | null
) {
    if (!value) {
        return "—";
    }

    return new Date(value).toLocaleString();
}


function formatDuration(
    execution: Execution
) {
    if (
        !execution.started_at ||
        !execution.completed_at
    ) {
        return "—";
    }

    const started = new Date(
        execution.started_at
    ).getTime();

    const completed = new Date(
        execution.completed_at
    ).getTime();

    const seconds = (
        completed - started
    ) / 1000;

    return `${seconds.toFixed(1)}s`;
}


export function Executions() {
    const { data: executions, loading, error, refresh } = useActivePolling<Execution[]>(
        getExecutions, rows => rows.some(row => isActiveStatus(row.status)), [],
    );

    return (
        <>
            <div className="section-heading">
                <button className="secondary-button" onClick={refresh} disabled={loading}>{loading ? "Refreshing…" : "Refresh Executions"}</button>
                <h2>
                    Executions
                </h2>

                <span className="image-count">
                    {executions.length}
                </span>
            </div>


            {error && (
                <div className="error">
                    {error}
                </div>
            )}


            <div className="execution-list">

                {executions.map(
                    (execution) => (
                        <div
                            className="execution-card"
                            key={
                                execution.execution_id
                            }
                        >

                            <div className="execution-header">

                                <div>
                                    <strong>
                                        {
                                            execution.metadata
                                                ?.capability
                                            ?? "Unknown capability"
                                        }
                                    </strong>

                                    <span className="execution-id">
                                        {
                                            execution.execution_id
                                        }
                                    </span>
                                </div>

                                <span
                                    className={
                                        `execution-status ` +
                                        execution.status
                                    }
                                >
                                    {execution.status}
                                </span>

                            </div>


                            <div className="execution-details">

                                <div>
                                    <span>
                                        Attempt
                                    </span>
                                    <strong>
                                        {
                                            execution.attempt
                                        }
                                    </strong>
                                </div>

                                <div>
                                    <span>
                                        Provider
                                    </span>
                                    <strong>
                                        {
                                            execution.provider
                                            ?? "—"
                                        }
                                    </strong>
                                </div>

                                <div>
                                    <span>
                                        Model
                                    </span>
                                    <strong>
                                        {
                                            execution.model
                                            ?? "—"
                                        }
                                    </strong>
                                </div>

                                <div>
                                    <span>
                                        Duration
                                    </span>
                                    <strong>
                                        {
                                            formatDuration(
                                                execution
                                            )
                                        }
                                    </strong>
                                </div>

                                <div>
                                    <span>
                                        Started
                                    </span>
                                    <strong>
                                        {
                                            formatDate(
                                                execution.started_at
                                            )
                                        }
                                    </strong>
                                </div>

                                <div>
                                    <span>
                                        Artifact
                                    </span>
                                    <strong>
                                        {
                                            execution.metadata
                                                ?.artifact_id
                                            ?? "—"
                                        }
                                    </strong>
                                </div>

                            </div>


                            {execution.error_message && (
                                <div className="execution-error">
                                    {
                                            safeMessage(execution.error_message)
                                    }
                                </div>
                            )}

                        </div>
                    )
                )}

            </div>
        </>
    );
}

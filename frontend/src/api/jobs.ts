import type { Job } from "../types/Job";

export async function getJobs(): Promise<Job[]> {
  const response = await fetch("/v2/jobs");

  if (!response.ok) {
    throw new Error("Failed to load jobs");
  }

  return response.json();
}
/** The AI surface: start analyses, poll jobs, read findings, decide on proposals. */

import { api } from "@/api/client";
import { orgId, projectId } from "@/api/context";
import type { AIJob, AIFinding, AIRecommendation, Paginated } from "@/types/api";

function base(): string {
  return `/orgs/${orgId()}/projects/${projectId()}`;
}

export interface StartAnalysis {
  agent: string;
  target_type?: string;
  target_id?: string;
  params?: Record<string, unknown>;
  idempotency_key?: string;
}

/** Returns 202 with a job. Nothing here waits for a model. */
export async function startAnalysis(body: StartAnalysis): Promise<AIJob> {
  const { data } = await api.post<AIJob>(`${base()}/ai/analyses`, body);
  return data;
}

export async function fetchJob(jobId: string): Promise<AIJob> {
  const { data } = await api.get<AIJob>(`${base()}/ai/jobs/${jobId}`);
  return data;
}

export async function fetchFindings(
  query: Record<string, string> = {},
): Promise<Paginated<AIFinding>> {
  const { data } = await api.get<Paginated<AIFinding>>(`${base()}/ai/findings`, {
    params: query,
  });
  return data;
}

export async function fetchRecommendations(
  query: Record<string, string> = {},
): Promise<Paginated<AIRecommendation>> {
  const { data } = await api.get<Paginated<AIRecommendation>>(`${base()}/ai/recommendations`, {
    params: query,
  });
  return data;
}

export async function confirmRecommendation(
  recommendationId: string,
  editedPayload?: Record<string, unknown>,
): Promise<AIRecommendation> {
  const { data } = await api.post<AIRecommendation>(
    `${base()}/ai/recommendations/${recommendationId}/confirm`,
    editedPayload ? { edited_payload: editedPayload } : {},
  );
  return data;
}

export async function rejectRecommendation(
  recommendationId: string,
  reason = "",
): Promise<AIRecommendation> {
  const { data } = await api.post<AIRecommendation>(
    `${base()}/ai/recommendations/${recommendationId}/reject`,
    { reason },
  );
  return data;
}

export async function fetchAvailableAgents(): Promise<{ code: string; description: string }[]> {
  const { data } = await api.get<{ code: string; description: string }[]>(
    `${base()}/ai/analyses/agents`,
  );
  return data;
}

/** The statuses that mean the job will not change again. */
export function isFinished(job: AIJob): boolean {
  return job.status === "succeeded" || job.status === "failed" || job.status === "cancelled";
}

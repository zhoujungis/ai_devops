/** Code, correlation and risk: the "what did this change touch" surface. */

import { api } from "@/api/client";
import { orgId, projectId } from "@/api/context";
import type { Commit, Module, Paginated, RiskAssessment } from "@/types/api";

function base(): string {
  return `/orgs/${orgId()}/projects/${projectId()}`;
}

export async function fetchCommits(
  query: Record<string, string> = {},
): Promise<Paginated<Commit>> {
  const { data } = await api.get<Paginated<Commit>>(`${base()}/commits`, { params: query });
  return data;
}

export async function fetchCommit(commitId: string): Promise<Commit> {
  const { data } = await api.get<Commit>(`${base()}/commits/${commitId}`);
  return data;
}

export async function fetchModules(): Promise<Paginated<Module>> {
  const { data } = await api.get<Paginated<Module>>(`${base()}/modules`);
  return data;
}

export async function fetchCommitRisk(commitId: string): Promise<RiskAssessment> {
  const { data } = await api.get<RiskAssessment>(`${base()}/commits/${commitId}/risk`);
  return data;
}

/** Auth and tenancy: who you are, and which project you are working in. */

import { api, setAccessToken } from "@/api/client";
import type { Organization, Paginated, Project, User } from "@/types/api";

interface TokenPair {
  access: string;
  refresh: string;
  user: User;
}

const ACCESS_KEY = "copilot.access";
const REFRESH_KEY = "copilot.refresh";

export function loadTokens(): { access: string; refresh: string } | null {
  const access = localStorage.getItem(ACCESS_KEY);
  const refresh = localStorage.getItem(REFRESH_KEY);
  return access && refresh ? { access, refresh } : null;
}

export function storeTokens(tokens: TokenPair): void {
  localStorage.setItem(ACCESS_KEY, tokens.access);
  localStorage.setItem(REFRESH_KEY, tokens.refresh);
  setAccessToken(tokens.access);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
  setAccessToken(null);
}

export async function login(email: string, password: string): Promise<TokenPair> {
  const { data } = await api.post<TokenPair>("/auth/login", { email, password });
  storeTokens(data);
  return data;
}

export async function refreshTokens(): Promise<string | null> {
  const refresh = localStorage.getItem(REFRESH_KEY);
  if (!refresh) {
    return null;
  }
  try {
    const { data } = await api.post<{ access: string }>("/auth/refresh", { refresh });
    localStorage.setItem(ACCESS_KEY, data.access);
    setAccessToken(data.access);
    return data.access;
  } catch {
    clearTokens();
    return null;
  }
}

export async function logout(refreshToken: string | null): Promise<void> {
  if (refreshToken) {
    // Fire and forget: a failed logout must not block the UI from clearing state.
    await api.post("/auth/logout", { refresh: refreshToken }).catch(() => undefined);
  }
  clearTokens();
}

export async function fetchMe(): Promise<User> {
  const { data } = await api.get<User>("/me");
  return data;
}

export async function fetchOrganizations(): Promise<Paginated<Organization>> {
  const { data } = await api.get<Paginated<Organization>>("/orgs");
  return data;
}

export async function fetchProjects(orgId: string): Promise<Paginated<Project>> {
  const { data } = await api.get<Paginated<Project>>(`/orgs/${orgId}/projects`);
  return data;
}

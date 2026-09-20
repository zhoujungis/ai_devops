/**
 * The project the UI is currently working in.
 *
 * Every project-scoped URL needs both the organization and the project id. Rather
 * than threading both through every call, the project switcher sets it here once and
 * the API modules read from it. Throwing when unset is deliberate: a page that
 * renders without a project selected is a routing bug, and it should fail loudly.
 */

import type { Project } from "@/types/api";

let current: Project | null = null;

export function setCurrentProject(project: Project | null): void {
  current = project;
}

export function currentProject(): Project {
  if (current === null) {
    throw new Error("No project selected. Select one before calling project-scoped APIs.");
  }
  return current;
}

export function projectId(): string {
  return currentProject().id;
}

export function orgId(): string {
  return currentProject().org;
}

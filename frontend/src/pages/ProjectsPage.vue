<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { NButton, NCard, NSpace, useMessage } from "naive-ui";

import { fetchOrganizations, fetchProjects } from "@/api/auth";
import { setCurrentProject } from "@/api/context";
import { useAuthStore } from "@/stores/auth";
import type { Organization, Project } from "@/types/api";

const auth = useAuthStore();
const router = useRouter();
const message = useMessage();

const organizations = ref<Organization[]>([]);
const projects = ref<Project[]>([]);
const selectedOrg = ref<string | null>(null);
const loading = ref(false);

onMounted(async () => {
  loading.value = true;
  try {
    const page = await fetchOrganizations();
    organizations.value = page.results;
    if (page.results.length > 0) {
      await selectOrg(page.results[0].id);
    }
  } catch (cause) {
    message.error(cause instanceof Error ? cause.message : "Could not load organizations");
  } finally {
    loading.value = false;
  }
});

async function selectOrg(orgId: string): Promise<void> {
  selectedOrg.value = orgId;
  const page = await fetchProjects(orgId);
  projects.value = page.results;
}

function open(project: Project): void {
  setCurrentProject(project);
  void router.push({ name: "dashboard", params: { projectId: project.id } });
}

async function signOut(): Promise<void> {
  await auth.signOut();
  await router.push({ name: "login" });
}
</script>

<template>
  <div class="mx-auto max-w-4xl space-y-6 p-6">
    <NSpace justify="space-between" align="center">
      <h1 class="text-lg font-semibold">Projects</h1>
      <NButton quaternary size="small" @click="signOut">Sign out ({{ auth.email }})</NButton>
    </NSpace>

    <div v-if="loading" class="text-sm text-slate-500">Loading…</div>
    <div v-else-if="projects.length === 0" class="panel text-sm text-slate-500">
      No projects yet.
    </div>

    <div class="grid gap-3 sm:grid-cols-2">
      <NCard
        v-for="project in projects"
        :key="project.id"
        hoverable
        class="cursor-pointer"
        @click="open(project)"
      >
        <div class="space-y-1">
          <p class="font-medium">{{ project.name }}</p>
          <p class="text-xs text-slate-500">{{ project.slug }}</p>
          <p class="text-xs text-slate-600">{{ project.role }}</p>
        </div>
      </NCard>
    </div>
  </div>
</template>

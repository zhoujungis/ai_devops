<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { NSpace } from "naive-ui";

import { fetchOrganizations, fetchProjects } from "@/api/auth";
import { setCurrentProject } from "@/api/context";

const route = useRoute();
const projectId = route.params.projectId as string;
const name = ref("Project");

// Recover the project on a hard refresh. The URL is the source of truth for which
// project is open; scanning the user's organizations is two cheap calls and avoids
// trusting anything in local storage.
onMounted(async () => {
  const organizations = (await fetchOrganizations()).results;
  for (const organization of organizations) {
    const found = (await fetchProjects(organization.id)).results.find(
      (project) => project.id === projectId,
    );
    if (found) {
      setCurrentProject(found);
      name.value = found.name;
      return;
    }
  }
});
</script>

<template>
  <div class="mx-auto max-w-6xl space-y-6 p-6">
    <NSpace justify="space-between" align="center">
      <h1 class="text-lg font-semibold">{{ name }}</h1>
      <NSpace>
        <router-link :to="{ name: 'dashboard' }" class="text-sm text-slate-400 hover:text-slate-200">
          Dashboard
        </router-link>
        <router-link
          :to="{ name: 'code-impact' }"
          class="text-sm text-slate-400 hover:text-slate-200"
        >
          Code impact
        </router-link>
      </NSpace>
    </NSpace>

    <router-view />
  </div>
</template>

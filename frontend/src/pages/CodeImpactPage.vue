<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { NButton, NSelect } from "naive-ui";

import { fetchCommits, fetchCommitRisk } from "@/api/code";
import type { Commit, RiskAssessment } from "@/types/api";

const commits = ref<Commit[]>([]);
const selectedId = ref<string | null>(null);
const assessment = ref<RiskAssessment | null>(null);
const loading = ref(false);

const selected = computed(
  () => commits.value.find((commit) => commit.id === selectedId.value) ?? null,
);

const options = computed(() =>
  commits.value.map((commit) => ({
    label: `${commit.short_sha} — ${firstLine(commit.message)}`,
    value: commit.id,
  })),
);

function firstLine(message: string): string {
  return message.split("\n")[0] ?? "";
}

async function load(): Promise<void> {
  loading.value = true;
  try {
    commits.value = (await fetchCommits()).results;
  } finally {
    loading.value = false;
  }
}

const rows = computed(() =>
  assessment.value
    ? [...assessment.value.breakdown].sort((a, b) => b.contribution - a.contribution)
    : [],
);

const tone = computed(() => {
  switch (assessment.value?.level) {
    case "critical":
      return "text-red-400";
    case "high":
      return "text-orange-400";
    case "medium":
      return "text-amber-400";
    default:
      return "text-emerald-400";
  }
});

async function assess(): Promise<void> {
  if (!selected.value) {
    return;
  }
  loading.value = true;
  try {
    assessment.value = await fetchCommitRisk(selected.value.id);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-4">
    <NSpace align="center">
    <NSelect
      v-model:value="selectedId"
      :options="options"
      placeholder="Pick a commit"
      class="min-w-[28rem]"
      filterable
    />
      <NButton type="primary" :loading="loading" :disabled="!selected" @click="assess">
        Assess risk
      </NButton>
    </NSpace>

    <div v-if="assessment" class="panel space-y-3">
      <div class="flex items-baseline gap-3">
        <span :class="['text-3xl font-semibold', tone]">{{ assessment.score.toFixed(1) }}</span>
        <span :class="['text-sm font-medium uppercase tracking-wide', tone]">
          {{ assessment.level }}
        </span>
      </div>

      <table class="w-full text-left text-sm">
        <thead class="text-xs uppercase text-slate-500">
          <tr>
            <th class="py-1 pr-3">Signal</th>
            <th class="py-1 pr-3">What we measured</th>
            <th class="py-1 text-right">Contribution</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.signal"
            class="border-t border-[#1f242b] align-top"
          >
            <td class="py-2 pr-3 font-medium">{{ row.label }}</td>
            <td class="py-2 pr-3 text-slate-400">{{ row.detail }}</td>
            <td class="py-2 text-right font-mono">{{ row.contribution.toFixed(2) }}</td>
          </tr>
        </tbody>
      </table>

      <p class="text-xs text-slate-500">
        The contributions add up to the score. A signal you disagree with is one you can
        go and measure again.
      </p>
    </div>
  </div>
</template>

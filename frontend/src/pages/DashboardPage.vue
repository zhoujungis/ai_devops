<script setup lang="ts">
import { NButton, NTag } from "naive-ui";
import { computed, onMounted, ref } from "vue";

import DataGaps from "@/components/DataGaps.vue";
import { fetchFindings } from "@/api/ai";
import type { AIFinding } from "@/types/api";

const findings = ref<AIFinding[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

const severityTone: Record<string, string> = {
  critical: "bg-red-950 text-red-300",
  high: "bg-orange-950 text-orange-300",
  medium: "bg-amber-950 text-amber-300",
  low: "bg-slate-800 text-slate-300",
  info: "bg-slate-800 text-slate-400",
};

const grouped = computed(() => {
  const byAgent = new Map<string, AIFinding[]>();
  for (const finding of findings.value) {
    const list = byAgent.get(finding.agent_code) ?? [];
    list.push(finding);
    byAgent.set(finding.agent_code, list);
  }
  return [...byAgent.entries()];
});

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    findings.value = (await fetchFindings()).results;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-base font-medium">What the AI found</h2>
      <NButton quaternary size="small" :loading="loading" @click="load">Refresh</NButton>
    </div>

    <div v-if="error" class="panel text-sm text-red-400">{{ error }}</div>
    <div v-else-if="loading" class="text-sm text-slate-500">Loading findings…</div>
    <div v-else-if="findings.length === 0" class="panel text-sm text-slate-500">
      No findings yet. Run an analysis from the code impact page.
    </div>

    <div v-for="[agent, rows] in grouped" :key="agent" class="space-y-2">
      <h3 class="text-sm font-medium uppercase tracking-wide text-slate-500">{{ agent }}</h3>

      <div v-for="finding in rows" :key="finding.id" class="panel space-y-2">
        <div class="flex items-start justify-between gap-3">
          <p class="font-medium">{{ finding.title }}</p>
          <NTag :class="severityTone[finding.severity] ?? ''" size="small" :bordered="false">
            {{ finding.severity }}
          </NTag>
        </div>

        <p class="text-sm text-slate-400">{{ finding.summary }}</p>

        <p class="text-xs text-slate-600">confidence {{ (finding.confidence * 100).toFixed(0) }}%</p>

        <DataGaps :gaps="(finding.payload?.dropped_evidence as string[] | undefined) ?? []" />
      </div>
    </div>
  </div>
</template>

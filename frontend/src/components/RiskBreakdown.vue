<script setup lang="ts">
/**
 * Renders a risk score the way the plan demands: the number, then *why*.
 *
 * Every row shows its label, its detail, and what it contributed. If a reader cannot
 * argue with a row, the breakdown has failed.
 */
import { computed } from "vue";

import type { RiskAssessment } from "@/types/api";

const props = defineProps<{ assessment: RiskAssessment }>();

// Ordered by contribution, so the biggest driver is always the first row.
const rows = computed(() =>
  [...props.assessment.breakdown].sort((a, b) => b.contribution - a.contribution),
);

const tone = computed(() => {
  switch (props.assessment.level) {
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
</script>

<template>
  <div class="space-y-3">
    <div class="flex items-baseline gap-3">
      <span :class="['text-3xl font-semibold', tone]">{{ assessment.score.toFixed(1) }}</span>
      <span :class="['text-sm font-medium uppercase tracking-wide', tone]">
        {{ assessment.level }}
      </span>
      <span class="text-xs text-slate-500">out of 100, from measurable signals</span>
    </div>

    <table class="w-full text-left text-sm">
      <thead class="text-xs uppercase text-slate-500">
        <tr>
          <th class="py-1 pr-3">Signal</th>
          <th class="py-1 pr-3">What we measured</th>
          <th class="py-1 pr-3 text-right">Share</th>
          <th class="py-1 text-right">Contribution</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.signal"
          class="border-t border-[#1f242b] align-top"
        >
          <td class="py-2 pr-3 font-medium text-slate-200">{{ row.label }}</td>
          <td class="py-2 pr-3 text-slate-400">{{ row.detail }}</td>
          <td class="py-2 pr-3 text-right text-slate-400">{{ row.normalized * 100 }}%</td>
          <td class="py-2 text-right font-mono text-slate-200">
            {{ row.contribution.toFixed(2) }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

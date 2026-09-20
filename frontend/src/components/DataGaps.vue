<script setup lang="ts">
/**
 * Renders `data_gaps` explicitly.
 *
 * The plan is adamant about this: "we found nothing" and "we have no data" must not
 * look the same. A gap is a limitation the reader deserves to see, not an empty
 * section to hide.
 */
defineProps<{ gaps: string[] }>();
</script>

<template>
  <div v-if="gaps.length" class="rounded-lg border border-amber-900/50 bg-amber-950/20 p-3">
    <p class="mb-2 text-xs font-medium uppercase tracking-wide text-amber-400">
      What this analysis could not determine
    </p>
    <ul class="space-y-1 text-sm text-amber-200/80">
      <li v-for="gap in gaps" :key="gap" class="flex gap-2">
        <span aria-hidden="true">·</span>
        <span>{{ gap }}</span>
      </li>
    </ul>
  </div>
</template>

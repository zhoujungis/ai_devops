/**
 * The one UX primitive behind every AI capability.
 *
 * The shape is always the same: submit, poll until the job stops changing, then show
 * the result or the failure. Centralising it means the "AI is thinking" state, the
 * error surface and the polling interval behave identically on every page — and a
 * looping model cannot burn budget indefinitely because the poll has a bound.
 */

import { computed, ref, type Ref } from "vue";

import { fetchJob, isFinished, startAnalysis, type StartAnalysis } from "@/api/ai";
import type { AIJob } from "@/types/api";

export type JobPhase = "idle" | "submitting" | "polling" | "done" | "error";

const POLL_INTERVAL_MS = 2000;
const MAX_POLLS = 150;

export interface UseAsyncJob {
  job: Ref<AIJob | null>;
  phase: Ref<JobPhase>;
  error: Ref<string | null>;
  busy: Ref<boolean>;
  run: (body: StartAnalysis) => Promise<AIJob | null>;
  reset: () => void;
}

export function useAsyncJob(): UseAsyncJob {
  const job = ref<AIJob | null>(null);
  const phase = ref<JobPhase>("idle");
  const error = ref<string | null>(null);

  const busy = computed(() => phase.value === "submitting" || phase.value === "polling");

  async function run(body: StartAnalysis): Promise<AIJob | null> {
    phase.value = "submitting";
    error.value = null;

    try {
      let current = await startAnalysis(body);
      job.value = current;
      phase.value = "polling";

      let attempts = 0;
      while (!isFinished(current)) {
        attempts += 1;
        if (attempts > MAX_POLLS) {
          throw new Error("Analysis did not finish in time. Check the job status later.");
        }
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        current = await fetchJob(current.id);
        job.value = current;
      }

      phase.value = "done";
      if (current.status === "failed") {
        error.value = current.error || "The analysis failed.";
      }
      return current;
    } catch (cause) {
      phase.value = "error";
      error.value = cause instanceof Error ? cause.message : String(cause);
      return null;
    }
  }

  function reset(): void {
    job.value = null;
    phase.value = "idle";
    error.value = null;
  }

  return { job, phase, error, busy, run, reset };
}

export function message(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

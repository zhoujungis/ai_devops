<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { NButton, NForm, NFormItem, NInput, useMessage } from "naive-ui";

import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const router = useRouter();
const message = useMessage();

const email = ref("");
const password = ref("");
const busy = ref(false);

const canSubmit = computed(() => email.value.includes("@") && password.value.length >= 8);

async function submit(): Promise<void> {
  if (!canSubmit.value || busy.value) {
    return;
  }
  busy.value = true;
  try {
    await auth.signIn(email.value, password.value);
    await router.push({ name: "projects" });
  } catch (cause) {
    // The backend already returns "invalid email or password" for both cases, so
    // relaying its message is safe and does not leak which accounts exist.
    message.error(cause instanceof Error ? cause.message : "Sign in failed");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center">
    <div class="panel w-full max-w-sm space-y-6">
      <div class="space-y-1">
        <h1 class="text-lg font-semibold">AI DevOps / QA Copilot</h1>
        <p class="text-sm text-slate-500">Sign in to your organization.</p>
      </div>

      <NForm @submit.prevent="submit">
        <NFormItem label="Email">
          <NInput v-model:value="email" placeholder="you@example.com" autocomplete="email" />
        </NFormItem>
        <NFormItem label="Password">
          <NInput
            v-model:value="password"
            type="password"
            show-password-on="click"
            autocomplete="current-password"
            @keydown.enter="submit"
          />
        </NFormItem>
        <NButton type="primary" block :loading="busy" :disabled="!canSubmit" attr-type="submit">
          Sign in
        </NButton>
      </NForm>
    </div>
  </div>
</template>

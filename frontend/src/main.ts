import { createApp } from "vue";
import { createPinia } from "pinia";

import App from "@/App.vue";
import { router } from "@/router";
import { useAuthStore } from "@/stores/auth";
import "@/styles/main.css";

const app = createApp(App);
app.use(createPinia());
app.use(router);

// Bind the HTTP client to auth state before the first request goes out.
const auth = useAuthStore();
auth.bind();
void auth.restore();

app.mount("#app");

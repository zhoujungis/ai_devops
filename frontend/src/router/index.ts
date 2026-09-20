/** Routing. Guards read from the auth store, never from a raw token. */

import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";

import { useAuthStore } from "@/stores/auth";

const routes: RouteRecordRaw[] = [
  { path: "/login", name: "login", component: () => import("@/pages/LoginPage.vue") },
  { path: "/projects", name: "projects", component: () => import("@/pages/ProjectsPage.vue") },
  {
    path: "/projects/:projectId",
    component: () => import("@/layouts/ProjectLayout.vue"),
    children: [
      {
        path: "",
        name: "dashboard",
        component: () => import("@/pages/DashboardPage.vue"),
      },
      {
        path: "code-impact",
        name: "code-impact",
        component: () => import("@/pages/CodeImpactPage.vue"),
      },
    ],
  },
  { path: "/", redirect: "/projects" },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to) => {
  const auth = useAuthStore();
  if (to.name !== "login" && !auth.isAuthenticated) {
    return { name: "login" };
  }
  if (to.name === "login" && auth.isAuthenticated) {
    return { name: "projects" };
  }
  return true;
});

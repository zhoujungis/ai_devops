/** Auth state. The single place that holds tokens and decides "is anyone in?". */

import { defineStore } from "pinia";

import { api, configureAuth, setAccessToken } from "@/api/client";
import {
  fetchMe,
  loadTokens,
  login as loginApi,
  logout as logoutApi,
  refreshTokens,
} from "@/api/auth";
import type { User } from "@/types/api";

interface AuthState {
  user: User | null;
  ready: boolean;
}

export const useAuthStore = defineStore("auth", {
  state: (): AuthState => ({ user: null, ready: false }),

  getters: {
    isAuthenticated: (state) => state.user !== null,
    /** Re-derived from the user, so a stale token cannot grant a stale role. */
    email: (state) => state.user?.email ?? "",
  },

  actions: {
    /** Wire the HTTP client to this store. Called once, at app start. */
    bind() {
      configureAuth({
        getToken: () => localStorage.getItem("copilot.access"),
        refresh: () => refreshTokens(),
        onSessionExpired: () => this.signOut(),
      });
      const tokens = loadTokens();
      if (tokens) {
        setAccessToken(tokens.access);
      }
    },

    async signIn(email: string, password: string): Promise<void> {
      await loginApi(email, password);
      this.user = await fetchMe();
    },

    async restore(): Promise<void> {
      // Only trust a stored token once it has actually resolved a user.
      if (!loadTokens()) {
        this.ready = true;
        return;
      }
      try {
        this.user = await fetchMe();
      } catch {
        this.user = null;
      } finally {
        this.ready = true;
      }
    },

    async signOut(): Promise<void> {
      const refresh = localStorage.getItem("copilot.refresh");
      await logoutApi(refresh);
      this.user = null;
      api.defaults.headers.common.Authorization = "";
    },
  },
});

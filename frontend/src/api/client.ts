/**
 * The single HTTP client.
 *
 * Three behaviours it owns so pages never have to:
 *
 * 1. the access token is attached and refreshed once on expiry;
 * 2. the backend's error envelope is unwrapped into a typed exception;
 * 3. every request carries `X-Request-ID`, so a failure in the UI can be matched to
 *    a server log line.
 */

import axios, {
  AxiosError,
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from "axios";

import type { ApiErrorBody } from "@/types/api";

const API_BASE = "/api/v1";
const REQUEST_ID_HEADER = "X-Request-ID";

export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
    readonly requestId: string = "",
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

type Refresh = () => Promise<string | null>;

let accessToken: string | null = null;
let refreshHandler: Refresh | null = null;
let sessionExpiredHandler: (() => void) | null = null;
let refreshing: Promise<string | null> | null = null;

/** Wire the auth callbacks. Call once, at store creation. */
export function configureAuth(handlers: {
  getToken: () => string | null;
  refresh: Refresh;
  onSessionExpired: () => void;
}): void {
  refreshHandler = handlers.refresh;
  sessionExpiredHandler = handlers.onSessionExpired;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

function randomId(): string {
  return crypto.randomUUID().replace(/-/g, "");
}

export const api: AxiosInstance = axios.create({ baseURL: API_BASE, timeout: 30_000 });

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  config.headers[REQUEST_ID_HEADER] = randomId();
  return config;
});

api.interceptors.response.use(
  (response) => {
    // Surface the id even on success, so a user can quote it in a bug report.
    const requestId = response.headers?.[REQUEST_ID_HEADER.toLowerCase()];
    if (requestId) {
      response.data = { ...(response.data as object), request_id: requestId };
    }
    return response;
  },
  async (error: AxiosError<ApiErrorBody>) => {
    const status = error.response?.status ?? 0;
    const config = error.config as InternalAxiosRequestConfig & { _retried?: boolean };

    if (status === 401 && !config._retried && refreshHandler) {
      config._retried = true;
      refreshing = refreshing ?? refreshHandler();
      const token = await refreshing.finally(() => {
        refreshing = null;
      });
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
        return api.request(config);
      }
      sessionExpiredHandler?.();
    }

    const body = error.response?.data;
    const envelope: ApiErrorBody["error"] =
      body && typeof body === "object" && "error" in body
        ? (body as ApiErrorBody).error
        : {
            code: `http_${status}`,
            message: error.message || "Request failed",
            details: {},
            request_id: "",
          };

    return Promise.reject(
      new ApiRequestError(
        status,
        envelope.code,
        envelope.message,
        envelope.details,
        envelope.request_id,
      ),
    );
  },
);

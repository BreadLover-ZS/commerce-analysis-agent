import { API_BASE_URL } from "./config";
import type { User } from "../types";
let csrf = "";
export function setAuth(user: User | null) {
  csrf = user?.csrf_token || "";
}
export function apiUrl(path: string) {
  return `${API_BASE_URL}${path}`;
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (csrf && options.method && options.method !== "GET")
    headers.set("X-CSRF-Token", csrf);
  const response = await fetch(apiUrl(path), {
    ...options,
    headers,
    credentials: "include",
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/auth/login")
      window.dispatchEvent(new Event("commerce:unauthorized"));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : Array.isArray(data.detail)
          ? data.detail.map((item: { msg: string }) => item.msg).join("；")
          : `请求失败 (${response.status})`,
    );
  }
  return data as T;
}

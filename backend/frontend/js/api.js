(() => {
  "use strict";

  const ACCESS_KEY = "bbstore_access_token";
  const REFRESH_KEY = "bbstore_refresh_token";
  const ROLE_KEY = "bbstore_role";
  const NAME_KEY = "bbstore_display_name";

  function getAccessToken() { return localStorage.getItem(ACCESS_KEY); }
  function getRefreshToken() { return localStorage.getItem(REFRESH_KEY); }
  function getRole() { return localStorage.getItem(ROLE_KEY); }
  function getDisplayName() { return localStorage.getItem(NAME_KEY); }
  function isAdmin() { return getRole() === "admin"; }

  function storeSession(tokenResponse) {
    localStorage.setItem(ACCESS_KEY, tokenResponse.access_token);
    localStorage.setItem(REFRESH_KEY, tokenResponse.refresh_token);
    localStorage.setItem(ROLE_KEY, tokenResponse.role);
    localStorage.setItem(NAME_KEY, tokenResponse.display_name);
  }

  function clearSession() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(ROLE_KEY);
    localStorage.removeItem(NAME_KEY);
  }

  async function login(email, password) {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Đăng nhập thất bại.");
    }
    const data = await res.json();
    storeSession(data);
    return data;
  }

  function logout() {
    clearSession();
    window.location.reload();
  }

  async function tryRefresh() {
    const refreshToken = getRefreshToken();
    if (!refreshToken) return false;
    try {
      const res = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      storeSession(data);
      return true;
    } catch (e) {
      return false;
    }
  }

  // Attaches the bearer token, retries once after a silent refresh on 401.
  // On unrecoverable auth failure, clears the session and reloads to the
  // login screen rather than leaving the app in a half-authenticated state.
  async function apiFetch(path, options = {}) {
    const doFetch = () => {
      const headers = { ...(options.headers || {}) };
      const token = getAccessToken();
      if (token) headers.Authorization = "Bearer " + token;
      return fetch(path, { ...options, headers });
    };

    let res = await doFetch();
    if (res.status === 401) {
      const refreshed = await tryRefresh();
      if (refreshed) {
        res = await doFetch();
      } else {
        clearSession();
        window.location.reload();
        throw new Error("Phiên đăng nhập đã hết hạn.");
      }
    }
    return res;
  }

  async function apiJson(path, options = {}) {
    const res = await apiFetch(path, options);
    if (!res.ok) {
      // Falls back to a raw-body snippet when there's no JSON "detail" —
      // covers infra-level error pages (e.g. a gateway timeout) too.
      const raw = await res.text().catch(() => "");
      let detail;
      try { detail = JSON.parse(raw).detail; } catch (e) { /* not JSON */ }
      const snippet = raw ? raw.replace(/\s+/g, " ").trim().slice(0, 200) : "";
      throw new Error(detail || `Lỗi ${res.status}${snippet ? " — " + snippet : ""}`);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  window.API = {
    login, logout, getAccessToken, getRole, getDisplayName, isAdmin,
    apiFetch, apiJson,
  };
})();

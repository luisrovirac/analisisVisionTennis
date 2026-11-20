"use strict";

// Cliente OIDC mínimo: Authorization Code + PKCE (S256) para un cliente público.
// La API publica el issuer y el client_id en /auth/config; los endpoints salen del discovery.
// Los tokens viven en localStorage para que la sesión sobreviva a cerrar la pestaña o el
// navegador; su duración real la fija el IdP (caducidad del refresh token). El estado PKCE,
// que solo dura un login, sigue en sessionStorage.
const Auth = (() => {
  const TOKENS_KEY = "tennis.tokens";
  const PKCE_KEY = "tennis.pkce";
  const REFRESH_MARGIN_MS = 30_000;
  let config = { enabled: false };
  let endpoints = null;

  async function init() {
    config = await fetchJSON("/auth/config");
    if (!config.enabled) return;
    endpoints = await fetchJSON(`${config.issuer}/.well-known/openid-configuration`);
    const params = new URLSearchParams(location.search);
    if (params.has("code")) await completeLogin(params);
    else if (params.has("error")) throw new Error(params.get("error_description") || "No se pudo iniciar sesión.");
  }

  const enabled = () => config.enabled;
  const isLoggedIn = () => !config.enabled || load() !== null;

  async function login() {
    const verifier = randomString();
    const state = randomString();
    sessionStorage.setItem(PKCE_KEY, JSON.stringify({ verifier, state }));
    const query = new URLSearchParams({
      response_type: "code",
      client_id: config.client_id,
      redirect_uri: redirectUri(),
      scope: "openid email profile",
      state,
      code_challenge: await sha256Base64Url(verifier),
      code_challenge_method: "S256",
    });
    location.assign(`${endpoints.authorization_endpoint}?${query}`);
  }

  async function completeLogin(params) {
    const pending = JSON.parse(sessionStorage.getItem(PKCE_KEY) || "null");
    sessionStorage.removeItem(PKCE_KEY);
    history.replaceState(null, "", redirectUri()); // quita code/state de la URL y del historial
    if (!pending || pending.state !== params.get("state")) {
      throw new Error("La respuesta de inicio de sesión no es válida. Inténtalo de nuevo.");
    }
    await requestTokens({
      grant_type: "authorization_code",
      code: params.get("code"),
      redirect_uri: redirectUri(),
      code_verifier: pending.verifier,
    });
  }

  // Devuelve un access token vigente (refrescándolo si está a punto de caducar) o null.
  async function accessToken() {
    if (!config.enabled) return null;
    const tokens = load();
    if (!tokens) return null;
    if (Date.now() < tokens.expires_at - REFRESH_MARGIN_MS) return tokens.access_token;
    try {
      return (await requestTokens({ grant_type: "refresh_token", refresh_token: tokens.refresh_token })).access_token;
    } catch {
      localStorage.removeItem(TOKENS_KEY);
      return null;
    }
  }

  function logout() {
    const tokens = load();
    localStorage.removeItem(TOKENS_KEY);
    if (!endpoints?.end_session_endpoint) return location.reload();
    const query = new URLSearchParams({ client_id: config.client_id, post_logout_redirect_uri: redirectUri() });
    if (tokens?.id_token) query.set("id_token_hint", tokens.id_token);
    location.assign(`${endpoints.end_session_endpoint}?${query}`);
  }

  // Solo para mostrar el nombre: la API no confía en esto, verifica la firma del access token.
  function displayName() {
    const idToken = load()?.id_token;
    if (!idToken) return "";
    const claims = JSON.parse(base64UrlDecode(idToken.split(".")[1]));
    return claims.preferred_username || claims.email || claims.sub;
  }

  async function requestTokens(form) {
    const res = await fetch(endpoints.token_endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ client_id: config.client_id, ...form }),
    });
    if (!res.ok) throw new Error("El proveedor de identidad rechazó la petición de tokens.");
    const t = await res.json();
    const previous = load();
    const tokens = {
      access_token: t.access_token,
      refresh_token: t.refresh_token || previous?.refresh_token,
      id_token: t.id_token || previous?.id_token,
      expires_at: Date.now() + t.expires_in * 1000,
    };
    localStorage.setItem(TOKENS_KEY, JSON.stringify(tokens));
    return tokens;
  }

  function load() {
    try {
      return JSON.parse(localStorage.getItem(TOKENS_KEY) || "null");
    } catch {
      return null;
    }
  }

  async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`No se pudo cargar ${url} (${res.status}).`);
    return res.json();
  }

  const redirectUri = () => `${location.origin}${location.pathname}`;

  function randomString() {
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    return base64UrlEncode(bytes);
  }

  async function sha256Base64Url(text) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return base64UrlEncode(new Uint8Array(digest));
  }

  function base64UrlEncode(bytes) {
    return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function base64UrlDecode(text) {
    const b64 = text.replace(/-/g, "+").replace(/_/g, "/");
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  }

  return { init, enabled, isLoggedIn, login, logout, accessToken, displayName };
})();

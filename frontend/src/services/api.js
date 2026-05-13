const BASE_URL = import.meta.env.VITE_API_URL || "/api";

/** Eski sürüm localStorage anahtarları — bir kez silinir */
const LEGACY_TOKEN_KEY = "kooperatif_token";
const LEGACY_KOOP_KEY = "kooperatif_bilgi";

/** Satıcı JWT yalnızca RAM'de: tam sayfa yenilenince oturum düşer (şimdilik). */
let _sellerToken = null;
let _sellerKooperatif = null;

(function purgeStaleSellerLocalStorage() {
  try {
    localStorage.removeItem(LEGACY_TOKEN_KEY);
    localStorage.removeItem(LEGACY_KOOP_KEY);
  } catch (_) {
    /* ignore */
  }
})();

export class SellerAuthRequiredError extends Error {
  constructor(message = "Satıcı oturumu gerekli veya süresi doldu.") {
    super(message);
    this.name = "SellerAuthRequiredError";
  }
}

export function saveToken(token, kooperatifBilgi) {
  _sellerToken = token;
  _sellerKooperatif = kooperatifBilgi ?? null;
}

export function getToken() {
  return _sellerToken;
}

export function getKooperatif() {
  return _sellerKooperatif;
}

export function clearToken() {
  _sellerToken = null;
  _sellerKooperatif = null;
  try {
    localStorage.removeItem(LEGACY_TOKEN_KEY);
    localStorage.removeItem(LEGACY_KOOP_KEY);
  } catch (_) {
    /* ignore */
  }
}

export function isLoggedIn() {
  return !!getToken();
}

async function parseErrorBody(res) {
  try {
    const j = await res.json();
    const d = j.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d))
      return d.map((x) => (typeof x === "string" ? x : x.msg || JSON.stringify(x))).join(" ");
    if (d && typeof d === "object") return JSON.stringify(d);
    return "İstek başarısız";
  } catch {
    return res.statusText || "Hata";
  }
}

export async function login(kooperatifAdi, sifre) {
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kooperatif_adi: kooperatifAdi,
      sifre: sifre,
    }),
  });
  if (!res.ok) {
    throw new Error(await parseErrorBody(res));
  }
  return res.json();
}

export async function logout() {
  try {
    await fetch(`${BASE_URL}/auth/logout`, { method: "POST" });
  } finally {
    clearToken();
  }
}

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers,
  });
  if (!res.ok) {
    const err = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${err || res.statusText}`);
  }
  return res.json();
}

/**
 * Sohbet gönderir. Satıcı modunda Bearer eklenir.
 * history: [{ role, content }, ...] → backend `messages`
 */
export async function sendMessage(message, mode, history) {
  const headers = { "Content-Type": "application/json" };
  if (mode === "satici") {
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  const body =
    history && history.length > 0 ? { messages: history, mode } : { message, mode };
  const res = await fetch(`${BASE_URL}/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (res.status === 401 && mode === "satici") {
    clearToken();
    throw new SellerAuthRequiredError();
  }
  if (!res.ok) {
    throw new Error(await parseErrorBody(res));
  }
  return res.json();
}

export const api = {
  chat: async (payload, mode, orderCtx = {}) => {
    const body =
      typeof payload === "string"
        ? { message: payload, mode }
        : { messages: payload, mode };
    if (orderCtx.order_step && orderCtx.order_step !== "idle") {
      body.order_step = orderCtx.order_step;
    }
    if (
      orderCtx.order_draft &&
      typeof orderCtx.order_draft === "object" &&
      Object.keys(orderCtx.order_draft).length > 0
    ) {
      body.order_draft = orderCtx.order_draft;
    }

    const headers = { "Content-Type": "application/json" };
    if (mode === "satici") {
      const token = getToken();
      if (token) headers.Authorization = `Bearer ${token}`;
    }

    const res = await fetch(`${BASE_URL}/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });

    if (res.status === 401 && mode === "satici") {
      clearToken();
      throw new SellerAuthRequiredError();
    }

    if (!res.ok) {
      throw new Error(await parseErrorBody(res));
    }
    return res.json();
  },

  products: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/products${qs ? `?${qs}` : ""}`);
  },
  order: (id) => request(`/orders/${id}`),
  lowStock: () => request("/stock/low"),
  health: () => request("/health"),
  notifications: () => request("/notifications"),
  notificationAction: (id) =>
    request(`/notifications/${id}/action`, { method: "POST" }),
  supplyDraftMail: (payload) =>
    request(`/supply/draft-mail`, {
      method: "POST",
      body: JSON.stringify(
        typeof payload === "string" ? { urun_adi: payload } : payload,
      ),
    }),
};

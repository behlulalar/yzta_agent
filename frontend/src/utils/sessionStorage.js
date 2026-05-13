/**
 * Sohbet oturumları — localStorage (tarayıcıya özel)
 */

const STORAGE_KEY = "yzta_koop_chat_sessions_v1";
const MAX_SESSIONS = 40;

export function getSessions() {
  try {
    const data = localStorage.getItem(STORAGE_KEY);
    if (!data) return [];
    const sessions = JSON.parse(data);
    return sessions.map((s) => ({
      ...s,
      createdAt: new Date(s.createdAt),
      updatedAt: new Date(s.updatedAt),
      order_step: s.order_step || "idle",
      order_draft:
        s.order_draft && typeof s.order_draft === "object" ? s.order_draft : {},
      messages: (s.messages || []).map((m) => ({
        ...m,
        timestamp: new Date(m.timestamp),
      })),
    }));
  } catch {
    return [];
  }
}

export function saveSession(session) {
  try {
    const sessions = getSessions();
    const idx = sessions.findIndex((x) => x.id === session.id);
    if (idx >= 0) sessions[idx] = session;
    else sessions.unshift(session);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions.slice(0, MAX_SESSIONS)));
  } catch (e) {
    console.error("saveSession", e);
  }
}

export function deleteSession(sessionId) {
  try {
    const filtered = getSessions().filter((s) => s.id !== sessionId);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(filtered));
  } catch (e) {
    console.error("deleteSession", e);
  }
}

export function getSession(sessionId) {
  return getSessions().find((s) => s.id === sessionId) || null;
}

export function generateSessionTitle(firstMessage) {
  const t = (firstMessage || "").trim().substring(0, 42);
  return t.length < (firstMessage || "").trim().length ? `${t}…` : t || "Yeni sohbet";
}

export function generateSessionId() {
  return `sess_${Date.now()}_${Math.random().toString(36).slice(2, 11)}`;
}

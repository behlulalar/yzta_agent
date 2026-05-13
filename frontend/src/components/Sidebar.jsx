import {
  MessagesSquare,
  PlusCircle,
  Trash2,
  X,
} from "lucide-react";
import "../styles/sidebar.css";
import {
  deleteSession,
  getSessions,
} from "../utils/sessionStorage";

export default function Sidebar({
  isOpen,
  onToggle,
  onStartNewChat,
  onLoadSession,
  currentSessionId,
  refreshVersion = 0,
  onSessionsMutated,
}) {
  // refreshVersion değişince yeniden oku (React state ile tetiklenir)
  void refreshVersion;
  const sessions = getSessions();

  if (!isOpen) return null;

  const handleDeleteSession = (sessionId, e) => {
    e.stopPropagation();
    deleteSession(sessionId);
    onSessionsMutated?.();
    if (sessionId === currentSessionId) {
      onStartNewChat();
    }
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <h2>
          <MessagesSquare size={20} /> Sohbetler
        </h2>
        <div className="sidebar-header-actions">
          <button
            type="button"
            className="new-chat-button"
            onClick={onStartNewChat}
            title="Yeni sohbet"
          >
            <PlusCircle size={18} /> Yeni
          </button>
          <button
            type="button"
            className="sidebar-close-button"
            onClick={onToggle}
            title="Kapat"
            aria-label="Kenar çubuğunu kapat"
          >
            <X size={20} />
          </button>
        </div>
      </div>

      <div className="sidebar-section">
        <h3>Geçmiş</h3>
        <div className="sessions-list">
          {sessions.length === 0 ? (
            <p className="empty-message">Henüz kayıtlı sohbet yok</p>
          ) : (
            sessions.map((session) => (
              <div
                key={session.id}
                role="button"
                tabIndex={0}
                className={`session-item ${session.id === currentSessionId ? "active" : ""}`}
                onClick={() => onLoadSession(session.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onLoadSession(session.id);
                  }
                }}
              >
                <div className="session-content">
                  <div className="session-title">{session.title}</div>
                  <div className="session-date">
                    {new Date(session.updatedAt).toLocaleDateString("tr-TR", {
                      day: "numeric",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </div>
                </div>
                <button
                  type="button"
                  className="delete-session-button"
                  onClick={(e) => handleDeleteSession(session.id, e)}
                  title="Sohbeti sil"
                >
                  <Trash2 size={18} />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="sidebar-footer">
        <div className="sidebar-total-questions">
          <span className="sidebar-footer-note">
            Created By Team 320
          </span>
        </div>
        <div className="version-info">
          <small>Kadın Kooperatifleri AI</small>
          <small className="creator-info">v0.1</small>
        </div>
      </div>
    </aside>
  );
}

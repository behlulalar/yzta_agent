import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { User, MessageCircle } from "lucide-react";
import "../styles/chat-message.css";

function formatTime(date) {
  return date.toLocaleTimeString("tr-TR", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ChatMessageRow({ message }) {
  if (message.role === "user") {
    return (
      <div className="message-wrapper user-message-wrapper">
        <div className="message user-message">
          <div className="message-content">
            <p>{message.content}</p>
          </div>
          <div className="message-time">{formatTime(message.timestamp)}</div>
        </div>
        <div className="message-avatar user-avatar" aria-hidden>
          <User size={20} />
        </div>
      </div>
    );
  }

  return (
    <div className="message-wrapper assistant-message-wrapper">
      <div className="message-avatar assistant-avatar" aria-hidden>
        <MessageCircle size={18} />
      </div>
      <div className="message assistant-message">
        <div className="message-content">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: (props) => (
                <a {...props} target="_blank" rel="noopener noreferrer" />
              ),
            }}
          >
            {message.content}
          </ReactMarkdown>
        </div>
        <div className="message-time">{formatTime(message.timestamp)}</div>
      </div>
    </div>
  );
}

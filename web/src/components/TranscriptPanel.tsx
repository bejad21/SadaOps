import { motion } from "framer-motion";
import { MessageSquareText, Send } from "lucide-react";
import { type FormEvent, useState } from "react";
import { UI_STRINGS, detectTextDirection } from "../i18n/strings";
import type { TranscriptMessage, UiLocale } from "../types";

interface Props {
  messages: TranscriptMessage[];
  locale: UiLocale;
  canSendText?: boolean;
  onSendText?: (text: string) => Promise<void> | void;
}

export function TranscriptPanel({ messages, locale, canSendText = false, onSendText }: Props) {
  const t = UI_STRINGS[locale];
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  const handleSubmit = async (ev: FormEvent<HTMLFormElement>) => {
    ev.preventDefault();
    const cleaned = draft.trim();
    if (!cleaned || !onSendText || !canSendText || sending) return;
    setSending(true);
    try {
      await onSendText(cleaned);
      setDraft("");
    } finally {
      setSending(false);
    }
  };

  return (
    <section className="panel" aria-label={t.transcriptTitle}>
      <div className="panel-header">
        <h2>
          <MessageSquareText size={16} aria-hidden="true" />
          <span>{t.transcriptTitle}</span>
        </h2>
        <span className="pill pill-mono">{messages.length}</span>
      </div>

      <div className="transcript-list">
        {messages.length === 0 ? (
          <p className="panel-empty">{t.transcriptEmpty}</p>
        ) : (
          messages.map((msg) => (
            <motion.article
              key={msg.id}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
              className={`msg ${msg.role === "user" ? "msg-user" : ""}`}
              dir={detectTextDirection(msg.text)}
            >
              <div className="msg-meta">
                <strong>{msg.role === "user" ? "Operator" : "SadaOps"}</strong>
                <span style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem" }}>
                  {msg.source === "text" && <span className="pill">{t.typedBadge}</span>}
                  {msg.interrupted && <span className="pill pill-warn">{t.interruptedBadge}</span>}
                  <span>{msg.timestamp}</span>
                </span>
              </div>
              <div className="msg-body">{msg.text}</div>
            </motion.article>
          ))
        )}
      </div>

      {onSendText && (
        <form className="chat-bar" data-testid="chat-form" onSubmit={handleSubmit}>
          <input
            type="text"
            className="input-field"
            data-testid="chat-text-input"
            dir="auto"
            value={draft}
            disabled={!canSendText || sending}
            placeholder={canSendText ? t.chatPlaceholder : t.chatDisconnectedPlaceholder}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button
            type="submit"
            className="btn btn-primary"
            data-testid="chat-send-btn"
            disabled={!canSendText || sending || !draft.trim()}
          >
            <Send size={15} aria-hidden="true" />
            <span>{sending ? t.chatSendingBtn : t.chatSendBtn}</span>
          </button>
        </form>
      )}
    </section>
  );
}

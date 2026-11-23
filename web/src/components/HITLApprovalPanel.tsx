import { motion } from "framer-motion";
import { Check, Lock, Mic, MicOff, Pencil, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { UI_STRINGS } from "../i18n/strings";
import type { PendingActionItem, UiLocale } from "../types";

interface Props {
  actions: PendingActionItem[];
  locale: UiLocale;
  busyActionId: string | null;
  actionFeedback: string | null;
  voiceHitlEnabled?: boolean;
  onToggleVoiceHitl?: () => void;
  onApprove: (action: PendingActionItem) => void;
  onReject: (action: PendingActionItem) => void;
  onEdit?: (
    action: PendingActionItem,
    updatedPayload: Record<string, unknown>
  ) => Promise<boolean> | boolean;
}

const LTR_FIELDS = new Set(["to", "repo", "event_id", "start_iso", "duration_minutes"]);
const MULTILINE_FIELDS = new Set(["body", "notes"]);

function toEditableMap(payload: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(payload)) {
    if (Array.isArray(v)) {
      out[k] = v.join(", ");
    } else if (v === null || v === undefined) {
      out[k] = "";
    } else {
      out[k] = String(v);
    }
  }
  return out;
}

function buildUpdatedPayload(
  original: Record<string, unknown>,
  draft: Record<string, string>
): Record<string, unknown> {
  const updated: Record<string, unknown> = {};
  for (const [k, origVal] of Object.entries(original)) {
    const raw = draft[k] ?? "";
    if (Array.isArray(origVal)) {
      updated[k] = raw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    } else if (typeof origVal === "number") {
      const parsed = Number(raw);
      updated[k] = Number.isFinite(parsed) ? parsed : raw;
    } else {
      updated[k] = raw;
    }
  }
  return updated;
}

export function HITLApprovalPanel({
  actions,
  locale,
  busyActionId,
  actionFeedback,
  voiceHitlEnabled = true,
  onToggleVoiceHitl,
  onApprove,
  onReject,
  onEdit,
}: Props) {
  const t = UI_STRINGS[locale];
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftFields, setDraftFields] = useState<Record<string, string>>({});

  const startEditing = (act: PendingActionItem) => {
    setEditingId(act.action_id);
    setDraftFields(toEditableMap(act.payload));
  };

  const cancelEditing = () => {
    setEditingId(null);
    setDraftFields({});
  };

  const submitEdit = async (act: PendingActionItem) => {
    if (!onEdit) return;
    const updated = buildUpdatedPayload(act.payload, draftFields);
    const ok = await onEdit(act, updated);
    if (ok !== false) {
      setEditingId(null);
      setDraftFields({});
    }
  };

  return (
    <section className="panel" aria-label={t.hitlTitle}>
      <div className="panel-header">
        <h2>
          <ShieldAlert size={16} aria-hidden="true" />
          <span>{t.hitlTitle}</span>
        </h2>
        <div style={{ display: "inline-flex", alignItems: "center", gap: "0.5rem" }}>
          {onToggleVoiceHitl && (
            <button
              type="button"
              className={`chip-btn ${voiceHitlEnabled ? "chip-active" : ""}`}
              data-testid="voice-hitl-toggle"
              aria-pressed={voiceHitlEnabled}
              onClick={onToggleVoiceHitl}
            >
              {voiceHitlEnabled ? (
                <Mic size={13} aria-hidden="true" />
              ) : (
                <MicOff size={13} aria-hidden="true" />
              )}
              <span>{voiceHitlEnabled ? t.voiceHitlOn : t.voiceHitlOff}</span>
            </button>
          )}
          <span className={`pill pill-mono ${actions.length > 0 ? "pill-warn" : ""}`}>
            {actions.length}
          </span>
        </div>
      </div>

      {actionFeedback && (
        <p className="hitl-feedback" data-testid="hitl-feedback" role="status" aria-atomic="true">
          {actionFeedback}
        </p>
      )}

      {actions.length === 0 ? (
        <p className="panel-empty" data-testid="hitl-empty">
          {t.hitlEmpty}
        </p>
      ) : (
        <div className="rail-list" style={{ gap: "0.85rem" }}>
          {actions.map((act) => {
            const isBusy = busyActionId === act.action_id;
            const isEditing = editingId === act.action_id;
            return (
              <motion.div
                key={act.action_id}
                data-testid={`hitl-card-${act.action_id}`}
                className="rail-item rail-item-amber"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ type: "spring", duration: 0.35, bounce: 0 }}
              >
                <div className="rail-row-head">
                  <div style={{ display: "inline-flex", alignItems: "center", gap: "0.5rem" }}>
                    <strong>{act.tool_name}</strong>
                    <code className="mono-code">{act.action_id}</code>
                  </div>
                  <span className="pill pill-warn" data-testid={`hitl-hash-${act.action_id}`}>
                    <Lock size={12} aria-hidden="true" />
                    <span>
                      {t.lockedHashLabel}: <code dir="ltr">{act.payload_hash.slice(0, 12)}…</code>
                    </span>
                  </span>
                </div>

                {isEditing ? (
                  <div className="hitl-edit-grid">
                    {Object.entries(act.payload).map(([field]) => (
                      <label key={field} className="hitl-edit-label">
                        <span dir="ltr">{field}</span>
                        {MULTILINE_FIELDS.has(field) ? (
                          <textarea
                            rows={3}
                            className="input-field"
                            data-testid={`hitl-input-${act.action_id}-${field}`}
                            dir={LTR_FIELDS.has(field) ? "ltr" : "auto"}
                            value={draftFields[field] ?? ""}
                            disabled={isBusy}
                            onChange={(e) =>
                              setDraftFields((prev) => ({ ...prev, [field]: e.target.value }))
                            }
                          />
                        ) : (
                          <input
                            type={typeof act.payload[field] === "number" ? "number" : "text"}
                            className="input-field"
                            data-testid={`hitl-input-${act.action_id}-${field}`}
                            dir={LTR_FIELDS.has(field) ? "ltr" : "auto"}
                            value={draftFields[field] ?? ""}
                            disabled={isBusy}
                            onChange={(e) =>
                              setDraftFields((prev) => ({ ...prev, [field]: e.target.value }))
                            }
                          />
                        )}
                      </label>
                    ))}
                  </div>
                ) : (
                  <pre dir="ltr" className="code-block">
                    {JSON.stringify(act.payload, null, 2)}
                  </pre>
                )}

                <div className="hitl-actions">
                  {isEditing ? (
                    <>
                      <button
                        type="button"
                        className="btn btn-primary"
                        data-testid={`hitl-save-${act.action_id}`}
                        disabled={isBusy}
                        onClick={() => void submitEdit(act)}
                      >
                        <Check size={15} aria-hidden="true" />
                        <span>{t.saveRelockBtn}</span>
                      </button>
                      <button
                        type="button"
                        className="btn"
                        data-testid={`hitl-cancel-edit-${act.action_id}`}
                        disabled={isBusy}
                        onClick={cancelEditing}
                      >
                        <X size={15} aria-hidden="true" />
                        <span>{t.cancelEditBtn}</span>
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="btn btn-primary"
                        data-testid={`hitl-approve-${act.action_id}`}
                        disabled={isBusy}
                        onClick={() => onApprove(act)}
                      >
                        <Check size={15} aria-hidden="true" />
                        <span>{t.approveBtn}</span>
                      </button>
                      {onEdit && (
                        <button
                          type="button"
                          className="btn"
                          data-testid={`hitl-edit-${act.action_id}`}
                          disabled={isBusy}
                          onClick={() => startEditing(act)}
                        >
                          <Pencil size={15} aria-hidden="true" />
                          <span>{t.editBtn}</span>
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn btn-danger"
                        data-testid={`hitl-reject-${act.action_id}`}
                        disabled={isBusy}
                        onClick={() => onReject(act)}
                      >
                        <X size={15} aria-hidden="true" />
                        <span>{t.rejectBtn}</span>
                      </button>
                    </>
                  )}
                </div>
              </motion.div>
            );
          })}
        </div>
      )}
    </section>
  );
}

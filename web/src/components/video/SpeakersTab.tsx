import { Check, Pencil } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type SpeakerStat } from "../../lib/api";
import { clock } from "../../lib/format";
import { SpeakerChip } from "./SummaryTab";

interface Props {
  videoId: string;
  speakers: SpeakerStat[];
  colors: Record<string, string>;
  onSaved: () => void;
}

export default function SpeakersTab({ videoId, speakers, colors, onSaved }: Props) {
  const [editing, setEditing] = useState(false);
  const [names, setNames] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);

  useEffect(() => {
    setNames(Object.fromEntries(speakers.map((s) => [s.label, s.name ?? ""])));
  }, [speakers]);

  if (speakers.length === 0) return <p className="muted">No speakers found.</p>;

  const save = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const res = await api.setSpeakers(videoId, names);
      setEditing(false);
      setMessage(res.indexed || !res.index_error
        ? { text: "Names saved. The summary, daily journal and search now use them." }
        : { text: `Names saved, but search wasn't updated: ${res.index_error}`, error: true });
      onSaved();
    } catch (e) {
      setMessage({ text: (e as Error).message, error: true });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div className="speakers-head">
        <p className="muted" style={{ margin: 0 }}>
          {speakers.length} speaker{speakers.length === 1 ? "" : "s"} identified from their voices.
          {!editing && " Name them to use real names in summaries and search."}
        </p>
        {!editing && <button className="btn" onClick={() => setEditing(true)}><Pencil size={15} /> Name speakers</button>}
      </div>
      {speakers.map((s) => (
        <div className="speaker-row" key={s.label}>
          <div>
            {editing ? (
              <label className="name-field">
                <span className="muted">{s.label}</span>
                <input value={names[s.label] ?? ""} placeholder="Name" aria-label={`Name for ${s.label}`}
                       onChange={(e) => setNames({ ...names, [s.label]: e.target.value })} maxLength={60} />
              </label>
            ) : (
              <>
                <SpeakerChip speaker={s.speaker} colors={colors} />
                {s.name && <div className="muted small">{s.label}</div>}
              </>
            )}
          </div>
          <div className="progress" title={`${Math.round(s.share * 100)}% of speaking time`}>
            <div style={{ width: `${s.share * 100}%`, background: colors[s.speaker] }} />
          </div>
          <div className="muted">{clock(s.seconds)} · {s.turns} turn{s.turns === 1 ? "" : "s"}</div>
        </div>
      ))}
      {editing && (
        <div className="speakers-actions">
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            <Check size={16} /> {saving ? "Saving…" : "Save names"}
          </button>
          <button className="btn" onClick={() => setEditing(false)} disabled={saving}>Cancel</button>
        </div>
      )}
      {message && <p className={message.error ? "error-text" : "success-text"} role="status">{message.text}</p>}
    </div>
  );
}

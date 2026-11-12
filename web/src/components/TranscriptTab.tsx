import { useMemo, useState } from "react";
import type { ScriptSegment } from "../api";
import { clock } from "../format";
import { SpeakerChip } from "./DiaryTab";

interface Props {
  script: ScriptSegment[];
  colors: Record<string, string>;
  currentTime: number;
  onSeek: (s: number) => void;
}

export default function TranscriptTab({ script, colors, currentTime, onSeek }: Props) {
  const [query, setQuery] = useState("");
  const lines = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? script.filter((s) => s.text.toLowerCase().includes(q) || s.speaker.toLowerCase().includes(q)) : script;
  }, [script, query]);

  if (script.length === 0) return <p className="muted">No speech found in this video.</p>;
  return (
    <div>
      <input className="transcript-search" placeholder="Search the transcript…" value={query}
             onChange={(e) => setQuery(e.target.value)} aria-label="Search the transcript" />
      {lines.length === 0 && <p className="muted">No lines match "{query}".</p>}
      {lines.map((s, i) => (
        <div key={i} className={`line${currentTime >= s.start && currentTime < s.end ? " current" : ""}`}>
          <button className="ts-btn" onClick={() => onSeek(s.start)}>{clock(s.start)}</button>
          <div><SpeakerChip speaker={s.speaker} colors={colors} />{s.text}</div>
        </div>
      ))}
    </div>
  );
}

import type { SpeakerStat } from "../api";
import { clock } from "../format";
import { SpeakerChip } from "./DiaryTab";

export default function SpeakersTab({ speakers, colors }: { speakers: SpeakerStat[]; colors: Record<string, string> }) {
  if (speakers.length === 0) return <p className="muted">No speakers found.</p>;
  return (
    <div>
      <p className="muted" style={{ marginTop: 0 }}>
        {speakers.length} speaker{speakers.length === 1 ? "" : "s"} identified from their voices.
        Naming speakers comes in Stage 2.
      </p>
      {speakers.map((s) => (
        <div className="speaker-row" key={s.speaker}>
          <div><SpeakerChip speaker={s.speaker} colors={colors} /></div>
          <div className="progress" title={`${Math.round(s.share * 100)}% of speaking time`}>
            <div style={{ width: `${s.share * 100}%`, background: colors[s.speaker] }} />
          </div>
          <div className="muted">{clock(s.seconds)} · {s.turns} turn{s.turns === 1 ? "" : "s"}</div>
        </div>
      ))}
    </div>
  );
}

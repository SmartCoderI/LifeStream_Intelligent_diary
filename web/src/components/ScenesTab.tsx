import type { Keyframe } from "../api";
import { clock } from "../format";

export default function ScenesTab({ keyframes, onSeek }: { keyframes: Keyframe[]; onSeek: (s: number) => void }) {
  if (keyframes.length === 0) return <p className="muted">No keyframes.</p>;
  return (
    <div className="scenes">
      {keyframes.map((k) => (
        <button className="scene" key={k.path} onClick={() => onSeek(k.time)} title="Play from here">
          <img src={k.url} alt={`Scene at ${clock(k.time)}`} loading="lazy" />
          <span>{clock(k.time)}</span>
        </button>
      ))}
    </div>
  );
}

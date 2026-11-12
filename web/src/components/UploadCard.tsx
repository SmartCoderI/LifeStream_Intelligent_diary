import { CloudUpload, FileVideo, X } from "lucide-react";
import { useRef, useState } from "react";
import { api, type VideoItem } from "../api";
import { fileSize, todayIso } from "../format";
import { ProgressBar } from "./StatusBadge";

const ACCEPT = ".mp4,.mov,.mkv,.m4v,.webm,.avi";
const MAX_BYTES = 1024 ** 3;

export default function UploadCard({ onUploaded }: { onUploaded: (v: VideoItem) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [date, setDate] = useState(todayIso());
  const [drag, setDrag] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const choose = (f: File | undefined | null) => {
    setError(null);
    if (!f) return;
    const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
    if (!ACCEPT.split(",").includes(ext)) return setError(`"${f.name}" is not a supported video type.`);
    if (f.size > MAX_BYTES) return setError(`"${f.name}" is larger than 1 GB.`);
    setFile(f);
  };

  const submit = async () => {
    if (!file) return;
    setError(null);
    setProgress(0);
    try {
      const video = await api.uploadVideo(file, date, setProgress);
      onUploaded(video);
      setFile(null);
      if (input.current) input.current.value = "";
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setProgress(null);
    }
  };

  const uploading = progress !== null;
  return (
    <section
      className={`upload${drag ? " drag" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); choose(e.dataTransfer.files[0]); }}
    >
      <div className="drop-zone" role="button" tabIndex={0} onClick={() => input.current?.click()}
           onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}>
        <CloudUpload size={44} className="upload-icon" />
        <h2>Upload a Video</h2>
        <div className="muted">Drag and drop or click to select</div>
        <div className="muted">MP4, MOV, MKV (up to 1 GB)</div>
      </div>
      <input ref={input} type="file" accept={ACCEPT} hidden aria-label="Video file"
             onChange={(e) => choose(e.target.files?.[0])} />

      <div className="upload-form">
        {file && (
          <div className="chosen-file">
            <FileVideo size={18} /> <span>{file.name}</span>
            <span className="muted">{fileSize(file.size)}</span>
            {!uploading && (
              <button className="icon-btn" aria-label="Remove file" onClick={() => setFile(null)}><X size={16} /></button>
            )}
          </div>
        )}
        <label className="field">
          Recording date
          <input type="date" value={date} max={todayIso()} onChange={(e) => setDate(e.target.value)} disabled={uploading} />
        </label>
        {uploading && <ProgressBar value={Math.round(progress! * 100)} />}
        {error && <div className="error-text" role="alert">{error}</div>}
        <button className="btn btn-primary" disabled={!file || !date || uploading} onClick={submit}>
          {uploading ? `Uploading… ${Math.round(progress! * 100)}%` : "Upload and process"}
        </button>
      </div>
    </section>
  );
}

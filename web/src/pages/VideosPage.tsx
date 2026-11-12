import { api, type VideoItem } from "../lib/api";
import UploadCard from "../components/library/UploadCard";
import VideoList from "../components/library/VideoList";
import { usePolling } from "../lib/usePolling";

const busy = (videos: VideoItem[]) => videos.some((v) => v.status === "queued" || v.status === "processing");

export default function VideosPage() {
  const { data: videos, error, refresh } = usePolling(api.listVideos, busy);

  const remove = async (v: VideoItem) => {
    if (!window.confirm(`Delete "${v.title}" and its diary? This can't be undone.`)) return;
    try {
      await api.deleteVideo(v.video_id);
    } catch (e) {
      window.alert((e as Error).message);
    }
    refresh();
  };

  const retry = async (v: VideoItem) => {
    try {
      await api.retryVideo(v.video_id);
    } catch (e) {
      window.alert((e as Error).message);
    }
    refresh();
  };

  return (
    <>
      <header className="page-header">
        <h1>Videos</h1>
        <p>Upload videos, track processing, and read your diaries.</p>
      </header>
      <div className="videos-grid">
        <UploadCard onUploaded={() => refresh().then(() => setTimeout(refresh, 2000))} />
        <div>
          {error && <div className="card empty error-text" role="alert">Can't reach the server: {error}</div>}
          {!error && videos === null && <div className="card empty">Loading…</div>}
          {videos && <VideoList videos={videos} onDelete={remove} onRetry={retry} />}
        </div>
      </div>
    </>
  );
}

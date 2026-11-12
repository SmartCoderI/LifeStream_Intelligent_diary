import { Navigate, Route, Routes, useParams } from "react-router-dom";
import Sidebar from "./components/layout/Sidebar";
import AskPage from "./pages/AskPage";
import JournalPage from "./pages/JournalPage";
import VideoPage from "./pages/VideoPage";
import VideosPage from "./pages/VideosPage";

function OldDiaryRedirect() {
  const { date } = useParams();
  return <Navigate to={`/journal/${date}`} replace />;
}

export default function App() {
  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/videos" replace />} />
          <Route path="/videos" element={<VideosPage />} />
          <Route path="/videos/:id" element={<VideoPage />} />
          <Route path="/journal" element={<JournalPage />} />
          <Route path="/journal/:date" element={<JournalPage />} />
          {/* old links */}
          <Route path="/diaries" element={<Navigate to="/journal" replace />} />
          <Route path="/diaries/:date" element={<OldDiaryRedirect />} />
          <Route path="/ask" element={<AskPage />} />
          <Route path="*" element={<p className="empty">Page not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}

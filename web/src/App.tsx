import { Navigate, Route, Routes } from "react-router-dom";
import KeyGate from "./components/layout/KeyGate";
import Sidebar from "./components/layout/Sidebar";
import AskPage from "./pages/AskPage";
import LatencyPage from "./pages/LatencyPage";
import VideoPage from "./pages/VideoPage";
import VideosPage from "./pages/VideosPage";

export default function App() {
  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <KeyGate>
          <Routes>
            <Route path="/" element={<Navigate to="/videos" replace />} />
            <Route path="/videos" element={<VideosPage />} />
            <Route path="/videos/:id" element={<VideoPage />} />
            {/* the daily journal page was removed; old links go to the library */}
            <Route path="/journal/*" element={<Navigate to="/videos" replace />} />
            <Route path="/diaries/*" element={<Navigate to="/videos" replace />} />
            <Route path="/diary/*" element={<Navigate to="/videos" replace />} />
            <Route path="/ask" element={<AskPage />} />
            <Route path="/latency" element={<LatencyPage />} />
            <Route path="*" element={<p className="empty">Page not found.</p>} />
          </Routes>
        </KeyGate>
      </main>
    </div>
  );
}

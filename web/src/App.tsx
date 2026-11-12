import { Navigate, Route, Routes } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import DiariesPage from "./pages/DiariesPage";
import VideoPage from "./pages/VideoPage";
import VideosPage from "./pages/VideosPage";

export default function App() {
  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/videos" replace />} />
          <Route path="/videos" element={<VideosPage />} />
          <Route path="/videos/:id" element={<VideoPage />} />
          <Route path="/diaries" element={<DiariesPage />} />
          <Route path="/diaries/:date" element={<DiariesPage />} />
          <Route path="*" element={<p className="empty">Page not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}

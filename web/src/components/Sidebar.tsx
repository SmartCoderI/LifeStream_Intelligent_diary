import { BookOpen, Play, Search, Video } from "lucide-react";
import { NavLink } from "react-router-dom";

export default function Sidebar() {
  const link = ({ isActive }: { isActive: boolean }) => `nav-link${isActive ? " active" : ""}`;
  return (
    <nav className="sidebar" aria-label="Main">
      <div className="brand">
        <div className="brand-logo"><Play size={20} fill="white" /></div>
        <div>
          <div className="brand-name">LifeStream</div>
          <div className="brand-sub">Intelligent Video Diary</div>
        </div>
      </div>
      <NavLink to="/videos" className={link}><Video size={19} /> Videos</NavLink>
      <NavLink to="/diaries" className={link}><BookOpen size={19} /> Diaries</NavLink>
      <span className="nav-link disabled" title="Semantic search arrives in Stage 2">
        <Search size={19} /> Ask / Search <span className="nav-tag">Stage 2</span>
      </span>
    </nav>
  );
}

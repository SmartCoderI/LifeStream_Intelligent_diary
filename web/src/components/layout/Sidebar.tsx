import { Gauge, Play, Search, Video } from "lucide-react";
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
      <NavLink to="/ask" className={link}><Search size={19} /> Ask / Search</NavLink>
      <NavLink to="/latency" className={link}><Gauge size={19} /> Latency</NavLink>
    </nav>
  );
}

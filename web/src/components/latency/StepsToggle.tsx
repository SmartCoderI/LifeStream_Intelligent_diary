import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

/** The detailed step timeline, collapsed by default. */
export default function StepsToggle({ children, note }: { children: React.ReactNode; note?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="steps-toggle">
      <button className="link-btn" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />} {open ? "Hide" : "Show"} step timeline
      </button>
      {note && <span className="muted small"> · {note}</span>}
      {open && children}
    </div>
  );
}

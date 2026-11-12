export interface Kpi { label: string; value: string; note?: string; tone?: "warn" }

/** A short row of headline numbers. Keep it to 3-4. */
export default function Kpis({ items }: { items: Kpi[] }) {
  return (
    <div className="kpis">
      {items.map((k) => (
        <div className={`kpi${k.tone === "warn" ? " kpi-warn" : ""}`} key={k.label}>
          <div className="kpi-label">{k.label}</div>
          <div className="kpi-value">{k.value}</div>
          {k.note && <div className="kpi-note">{k.note}</div>}
        </div>
      ))}
    </div>
  );
}

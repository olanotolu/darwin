"use client";
import type { ReactNode } from "react";
import type { Feasibility, Kind } from "@/lib/api";
import { f } from "@/lib/format";

export function Panel({ title, right, children, className }: { title: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={`panel ${className ?? ""}`}>
      <div className="ph">{title}<span className="spacer" />{right}</div>
      {children}
    </div>
  );
}

/** Provenance kind tag. Every number on screen sits next to one of these. */
export function K({ kind, title }: { kind: Kind | string; title?: string }) {
  const k = (["measured", "predicted", "assumed", "computed", "simulated"].includes(kind) ? kind : "computed") as Kind;
  return <span className={`kind ${k}`} title={title}><span className="glyph" />{kind}</span>;
}

export function N({ v, unit, d = 1, pm }: { v: number | null | undefined; unit?: string; d?: number; pm?: number | null }) {
  return (
    <span className="num">
      {f(v, d)}
      {pm !== undefined && pm !== null ? <span className="sec"> ± {f(pm, d)}</span> : null}
      {unit ? <span className="unit">{unit}</span> : null}
    </span>
  );
}

const FEAS: Record<string, { cls: string; ic: string; label: string }> = {
  COMPUTATIONALLY_FEASIBLE: { cls: "good", ic: "✓", label: "Computationally feasible" },
  REQUIRES_LAB_VALIDATION: { cls: "warning", ic: "!", label: "Requires lab validation" },
  OUTSIDE_SUPPORTED_DOMAIN: { cls: "critical", ic: "✕", label: "Outside supported domain" },
};
export function FeasBadge({ status }: { status: string }) {
  const s = FEAS[status] ?? { cls: "serious", ic: "?", label: status };
  return <span className={`status ${s.cls}`}><span className="ic">{s.ic}</span>{s.label}</span>;
}

export function Reasons({ feas }: { feas: Feasibility }) {
  if (!feas.rejection_reasons.length && !feas.validation_flags.length)
    return <div className="small muted">No rejection reasons or validation flags.</div>;
  return (
    <table className="t">
      <thead><tr><th>Code</th><th>Field</th><th className="r">Value</th><th className="r">Limit</th></tr></thead>
      <tbody>
        {feas.rejection_reasons.map((r, i) => (
          <tr key={`r${i}`}><td><span className="status critical"><span className="ic">✕</span><span className="mono small">{r.code}</span></span></td>
            <td className="small">{r.field}</td><td className="r mono small">{fmtAny(r.value)}</td><td className="r mono small">{fmtAny(r.limit)}</td></tr>
        ))}
        {feas.validation_flags.map((r, i) => (
          <tr key={`v${i}`}><td><span className="status warning"><span className="ic">!</span><span className="mono small">{r.code}</span></span></td>
            <td className="small">{r.field}</td><td className="r mono small">{fmtAny(r.value)}</td><td className="r mono small">{fmtAny(r.limit)}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

export function fmtAny(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3);
  if (Array.isArray(v)) return `[${v.map(fmtAny).join(", ")}]`;
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function ErrorBox({ error }: { error: string | null | undefined }) {
  return error ? <div className="err">{error}</div> : null;
}

export function Busy({ label }: { label: string | null }) {
  return label ? <span className="small sec">⟳ {label}</span> : null;
}

export function Fictional({ children }: { children?: ReactNode }) {
  return (
    <div className="fictional">
      <b>FICTIONAL</b> — demo fixtures, not real facilities. Inventories, prices, suppliers, lab budgets and facility GWP
      factors are assumed values. {children}
    </div>
  );
}

/** Legend glyphs mirror the chart marks (solid = measured, hollow = predicted). */
export function Glyph({ type, color }: { type: "solid" | "hollow" | "ring" | "line" | "dashed" | "dim"; color: string }) {
  return (
    <svg width="14" height="10" aria-hidden>
      {type === "solid" && <circle cx="7" cy="5" r="4" fill={color} />}
      {type === "hollow" && <circle cx="7" cy="5" r="3.5" fill="none" stroke={color} strokeWidth="1.5" />}
      {type === "dim" && <circle cx="7" cy="5" r="3" fill="none" stroke={color} strokeWidth="1" />}
      {type === "ring" && <><circle cx="7" cy="5" r="4.5" fill="none" stroke={color} strokeWidth="2" /><circle cx="7" cy="5" r="1.5" fill={color} /></>}
      {type === "line" && <line x1="0" y1="5" x2="14" y2="5" stroke={color} strokeWidth="2" />}
      {type === "dashed" && <line x1="0" y1="5" x2="14" y2="5" stroke={color} strokeWidth="1.5" strokeDasharray="3 2" />}
    </svg>
  );
}

"use client";
import { useEffect, useMemo, useState } from "react";
import {
  Area, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis,
} from "recharts";
import { MATERIALS, MATERIAL_COLUMN, MATERIAL_LABEL, type Composition, type PredictOut } from "@/lib/api";
import { UNITS, f } from "@/lib/format";
import { useDarwin } from "./DarwinProvider";
import { usePredict } from "./usePredict";
import { ErrorBox, FeasBadge, K, Panel, Reasons } from "./ui";

const MAT_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"]; // validated slots 1–7
const AB = { A: "#3987e5", B: "#d95926" };
const AXIS = { stroke: "#383835", tick: { fill: "#898781", fontSize: 11 } };

type Option = { id: string; label: string; group: string; composition: Composition; measured?: { age_days: number; strength_mpa: number }[] };

export function MaterialGenome() {
  const { space, gen, rank, history, objective, selected, facilities } = useDarwin();
  const options = useMemo<Option[]>(() => {
    const out: Option[] = [];
    const order = new Map(rank?.ranked.map((r) => [r.candidate_id, r.rank]) ?? []);
    [...(gen?.accepted ?? [])].sort((a, b) => (order.get(a.candidate_id) ?? 1e9) - (order.get(b.candidate_id) ?? 1e9))
      .forEach((c) => out.push({ id: c.candidate_id, label: `${c.candidate_id}${order.has(c.candidate_id) ? ` (rank ${order.get(c.candidate_id)})` : ""}`, group: "Generated candidates (predicted only)", composition: c.composition }));
    (history?.experiments ?? []).filter((e) => e.session_id).forEach((e) => out.push({
      id: e.id, label: `${e.candidate_id} · ${e.id} · ${e.status}`, group: "Replay experiments (held-out)", composition: e.composition,
      measured: e.measured?.rows.map((r) => ({ age_days: r.age_days, strength_mpa: r.strength_mpa })),
    }));
    (space?.measured_train_mixtures ?? []).forEach((m) => out.push({
      id: m.mix, label: `${m.mix}${m.pareto ? " (measured Pareto)" : ""}`, group: "Training mixtures (measured)", composition: m.composition, measured: m.by_age,
    }));
    return out;
  }, [space, gen, rank, history]);

  const [a, setA] = useState<string>("");
  const [b, setB] = useState<string>("");
  useEffect(() => {
    if (!options.length) return;
    if (!a || !options.some((o) => o.id === a)) setA(options.find((o) => o.id === selected)?.id ?? options[0].id);
    if (!b || !options.some((o) => o.id === b)) setB(options.find((o) => o.group.startsWith("Training") && space?.measured_train_mixtures.find((m) => m.mix === o.id)?.pareto)?.id ?? options[options.length - 1].id);
  }, [options, a, b, selected, space]);

  const oa = options.find((o) => o.id === a);
  const ob = options.find((o) => o.id === b);
  const pa = usePredict(oa?.composition, objective?.facility_id);
  const pb = usePredict(ob?.composition, objective?.facility_id);
  const fac = facilities.find((x) => x.id === objective?.facility_id);

  const Picker = ({ v, set, tag }: { v: string; set: (s: string) => void; tag: "A" | "B" }) => (
    <label className="row"><span className="mono" style={{ color: AB[tag] }}>{tag}</span>
      <select value={v} onChange={(e) => set(e.target.value)} style={{ maxWidth: 360 }}>
        {[...new Set(options.map((o) => o.group))].map((g) => (
          <optgroup key={g} label={g}>{options.filter((o) => o.group === g).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}</optgroup>
        ))}
      </select></label>
  );

  const stack = [oa, ob].filter(Boolean).map((o, i) => {
    const row: Record<string, number | string> = { name: i === 0 ? `A ${o!.id}` : `B ${o!.id}` };
    MATERIALS.forEach((m) => { row[m] = Number(o!.composition[MATERIAL_COLUMN[m]] ?? 0); });
    return row;
  });

  return (
    <div className="stack">
      <div className="row"><Picker v={a} set={setA} tag="A" /><Picker v={b} set={setB} tag="B" />
        <span className="small muted">Facility for factors, prices and constraints: {fac?.name ?? "—"}</span></div>
      <ErrorBox error={pa.error ?? pb.error} />

      <Panel title={`Composition — 7 materials, ${UNITS.kgm3}`}>
        <div style={{ height: 130 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={stack} layout="vertical" margin={{ top: 8, right: 20, left: 10, bottom: 4 }} barCategoryGap={10}>
              <CartesianGrid stroke="#2c2c2a" horizontal={false} />
              <XAxis type="number" {...AXIS} unit=" kg" />
              <YAxis type="category" dataKey="name" width={150} {...AXIS} tick={{ fill: "#c3c2b7", fontSize: 11, fontFamily: "var(--mono)" } as object} />
              <Tooltip cursor={{ fill: "#ffffff08" }} content={<StackTip />} />
              <Legend wrapperStyle={{ fontSize: 11, color: "#c3c2b7" }} />
              {MATERIALS.map((m, i) => (
                <Bar key={m} dataKey={m} name={MATERIAL_LABEL[m]} stackId="s" fill={MAT_COLORS[i]} stroke="#1a1a19" strokeWidth={2} isAnimationActive={false} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      <StrengthDev a={pa.data} b={pb.data} ma={oa?.measured} mb={ob?.measured} target={objective?.target_strength_mpa} />

      <div className="grid-2">
        {([["A", oa, pa.data], ["B", ob, pb.data]] as const).map(([tag, o, p]) => (
          <Panel key={tag} title={<><span className="mono" style={{ color: AB[tag] }}>{tag}</span> <span className="mono" style={{ textTransform: "none" }}>{o?.id ?? "—"}</span></>}
            right={o?.measured ? <K kind="measured" /> : <K kind="predicted" />}>
            {!p ? <div className="pb muted small">predicting…</div> : <MixDetail p={p} fac={fac} />}
          </Panel>
        ))}
      </div>
    </div>
  );
}

function StackTip({ active, payload, label }: { active?: boolean; payload?: { name: string; value: number; color: string }[]; label?: string }) {
  if (!active || !payload?.length) return null;
  const total = payload.reduce((s, p) => s + p.value, 0);
  return (
    <div className="tt"><div className="h">{label}</div>
      {payload.map((p) => <div key={p.name} className="row"><span style={{ width: 8, height: 8, background: p.color, display: "inline-block" }} />{p.name}<span className="spacer" /><span className="num">{f(p.value, 1)} kg/m³</span></div>)}
      <div className="row muted"><span>total</span><span className="spacer" /><span className="num">{f(total, 0)} kg/m³</span></div>
    </div>
  );
}

function StrengthDev({ a, b, ma, mb, target }: { a: PredictOut | null; b: PredictOut | null; ma?: { age_days: number; strength_mpa: number }[]; mb?: { age_days: number; strength_mpa: number }[]; target?: number }) {
  const ages = [1, 3, 5, 14, 28];
  const data = ages.map((age) => {
    const sa = a?.strength.find((s) => s.age_days === age);
    const sb = b?.strength.find((s) => s.age_days === age);
    return {
      age,
      aMean: sa?.mean_mpa, aBand: sa ? sa.pi95_mpa : undefined, aMeas: ma?.find((m) => m.age_days === age)?.strength_mpa,
      bMean: sb?.mean_mpa, bBand: sb ? sb.pi95_mpa : undefined, bMeas: mb?.find((m) => m.age_days === age)?.strength_mpa,
    };
  });
  const solidDot = (color: string) => (p: { cx?: number; cy?: number }) => p.cy == null ? <g /> : <circle cx={p.cx} cy={p.cy} r={4.5} fill={color} stroke="#1a1a19" strokeWidth={1.5} />;
  return (
    <Panel title="Strength development across curing ages (MPa)"
      right={<span className="small muted">dashed + hollow = predicted (band = 95% PI) · solid dots = measured</span>}>
      <div style={{ height: 280 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 12, right: 24, bottom: 22, left: 6 }}>
            <CartesianGrid stroke="#2c2c2a" />
            <XAxis dataKey="age" type="number" scale="log" domain={[1, 28]} ticks={ages} {...AXIS}
              label={{ value: "curing age, days (log)", position: "insideBottom", offset: -12, fill: "#898781", fontSize: 11 }} />
            <YAxis {...AXIS} label={{ value: "MPa", angle: -90, position: "insideLeft", fill: "#898781", fontSize: 11 }} />
            <Tooltip content={<DevTip />} />
            <Area dataKey="aBand" stroke="none" fill={AB.A} fillOpacity={0.12} isAnimationActive={false} />
            <Area dataKey="bBand" stroke="none" fill={AB.B} fillOpacity={0.12} isAnimationActive={false} />
            <Line dataKey="aMean" stroke={AB.A} strokeWidth={2} strokeDasharray="5 3" dot={{ r: 3.5, fill: "#1a1a19", stroke: AB.A, strokeWidth: 1.5 }} isAnimationActive={false} name="A predicted" />
            <Line dataKey="bMean" stroke={AB.B} strokeWidth={2} strokeDasharray="5 3" dot={{ r: 3.5, fill: "#1a1a19", stroke: AB.B, strokeWidth: 1.5 }} isAnimationActive={false} name="B predicted" />
            <Scatter dataKey="aMeas" shape={solidDot(AB.A)} isAnimationActive={false} name="A measured" />
            <Scatter dataKey="bMeas" shape={solidDot(AB.B)} isAnimationActive={false} name="B measured" />
            {target !== undefined && <Line dataKey={() => target} stroke="#898781" strokeDasharray="2 3" dot={false} strokeWidth={1} isAnimationActive={false} name="target" />}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </Panel>
  );
}

type DevRow = { age: number; aMean?: number; aBand?: [number, number]; aMeas?: number; bMean?: number; bBand?: [number, number]; bMeas?: number };
function DevTip({ active, payload }: { active?: boolean; payload?: { payload: DevRow }[] }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div className="tt"><div className="h">{r.age} d</div>
      {(["a", "b"] as const).map((k) => {
        const m = r[`${k}Mean`]; const band = r[`${k}Band`]; const meas = r[`${k}Meas`];
        return m === undefined ? null : (
          <div key={k}><span className="mono" style={{ color: k === "a" ? AB.A : AB.B }}>{k.toUpperCase()}</span>{" "}
            ○ <span className="num">{f(m)} MPa</span> <span className="muted num">[{f(band?.[0])}, {f(band?.[1])}]</span>
            {meas !== undefined && <> · ● <span className="num">{f(meas)} MPa</span></>}</div>
        );
      })}
    </div>
  );
}

function MixDetail({ p, fac }: { p: PredictOut; fac?: ReturnType<typeof useDarwin>["facilities"][number] }) {
  const g = p.gwp.darwin_materials_gwp;
  const maxC = Math.max(1, ...g.lines.map((l) => l.contribution_kgco2e ?? 0));
  const d = p.feasibility.derived;
  return (
    <div className="pb stack" style={{ gap: 12 }}>
      <dl className="kv">
        <dt>w/b ratio</dt><dd className="num">{f(d.w_b, 3)} <K kind="computed" /></dd>
        <dt>Binder</dt><dd className="num">{f(d.binder_kg_m3, 0)} kg/m³</dd>
        <dt>SCM share of binder</dt><dd className="num">{f(d.scm_fraction_of_binder, 3)} (cap {f(d.replacement_cap, 2)})</dd>
        <dt>Materials GWP</dt><dd className="num">{f(g.value)} {UNITS.gwp} <K kind="computed" />{!g.complete && <span className="status warning"> <span className="ic">!</span>incomplete</span>}</dd>
        <dt>Material cost</dt><dd className="num">{f(p.cost.value, 2)} {UNITS.usdm3} <K kind="assumed" title="assumed fixture prices" /></dd>
      </dl>
      <div>
        <div className="fieldset-title">Per-material GWP contribution</div>
        <table className="t"><thead><tr><th>Material</th><th className="r">kg/m³</th><th className="r">kgCO₂e/kg</th><th className="r">kgCO₂e/m³</th><th style={{ width: "30%" }} /></tr></thead>
          <tbody>{g.lines.map((l) => (
            <tr key={l.material}><td>{MATERIAL_LABEL[l.material as keyof typeof MATERIAL_LABEL] ?? l.material}</td>
              <td className="r num">{f(l.qty_kg, 0)}</td><td className="r num">{l.factor?.value != null ? f(l.factor.value, 4) : "—"}</td>
              <td className="r num">{f(l.contribution_kgco2e, 1)}</td>
              <td><div className="bar-track"><div className="bar-fill" style={{ width: `${((l.contribution_kgco2e ?? 0) / maxC) * 100}%` }} /></div></td></tr>
          ))}</tbody></table>
        <div className="small muted">Factors: facility table (assumed, FICTIONAL); materials-only partial boundary.</div>
      </div>
      <div>
        <div className="fieldset-title">Supplier cost assumptions <K kind="assumed" /></div>
        <table className="t"><thead><tr><th>Material</th><th>Supplier</th><th className="r">USD/kg</th><th className="r">USD/m³</th></tr></thead>
          <tbody>{p.cost.lines.map((l) => (
            <tr key={l.material}><td>{MATERIAL_LABEL[l.material as keyof typeof MATERIAL_LABEL] ?? l.material}</td>
              <td className="small sec">{fac?.materials[l.material as keyof typeof fac.materials]?.supplier ?? "—"}</td>
              <td className="r num">{f(l.usd_per_kg, 3)}</td><td className="r num">{f(l.usd, 2)}</td></tr>
          ))}</tbody></table>
        {p.cost.missing_prices.length > 0 && <div className="small" style={{ color: "var(--warning)" }}>missing prices: {p.cost.missing_prices.join(", ")}</div>}
      </div>
      <div>
        <div className="fieldset-title">Constraint checks</div>
        <div style={{ marginBottom: 4 }}><FeasBadge status={p.feasibility.status} /></div>
        <Reasons feas={p.feasibility} />
      </div>
    </div>
  );
}

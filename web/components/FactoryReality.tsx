"use client";
import { useEffect, useState } from "react";
import { api, MATERIALS, MATERIAL_LABEL, type Facility, type Material } from "@/lib/api";
import { UNITS, f, shortTime } from "@/lib/format";
import { useDarwin } from "./DarwinProvider";
import { ErrorBox, Fictional, K, Panel } from "./ui";

type Snap = { at: string; label: string; accepted: number; rejected: number; codes: Record<string, number>; top: string | null; topScore: number | null; topPred: number | null; pareto: number; meanGwp: number | null };
type Change = { at: string; field: string; old: unknown; new: unknown };

export function FactoryReality() {
  const d = useDarwin();
  const { facilities, objective, gen, rank, space, run, busy } = d;
  const fac = facilities.find((x) => x.id === objective?.facility_id);
  const [before, setBefore] = useState<Snap | null>(null);
  const [log, setLog] = useState<Change[]>([]);

  const snap = (label: string): Snap => {
    const acc = space?.candidates.filter((c) => c.accepted) ?? [];
    return {
      at: new Date().toISOString(), label, accepted: gen?.n_accepted ?? 0, rejected: gen?.n_rejected ?? 0, codes: gen?.rejection_code_counts ?? {},
      top: rank?.ranked[0]?.candidate_id ?? null, topScore: rank?.ranked[0]?.score ?? null, topPred: rank?.ranked[0]?.prediction.mean_mpa ?? null,
      pareto: acc.filter((c) => c.pareto).length, meanGwp: acc.length ? acc.reduce((s, c) => s + c.gwp_kgco2e_m3, 0) / acc.length : null,
    };
  };

  /** Every what-if goes to the API, then candidates are regenerated + re-ranked (seeded). */
  const apply = (label: string, fn: () => Promise<Change[]>) => run(label, async () => {
    setBefore(snap(label));
    const changes = await fn();
    setLog((l) => [...changes, ...l]);
    await d.refreshFacilities();
    await d.recompute(objective?.facility_id);
  });

  const switchFacility = (id: string) => run("switching facility", async () => {
    await d.saveObjective({ facility_id: id });
    setBefore(null);
    await d.recompute(id);
  });

  return (
    <div className="stack">
      <Fictional>Every control below is a what-if edit to a fictional fixture, sent to the API, followed by a seeded regenerate and re-rank.</Fictional>
      <ErrorBox error={d.error} />
      <div className="row">
        <span className="sec">Active facility</span>
        <div className="seg">{facilities.map((x) => <button key={x.id} aria-pressed={x.id === fac?.id} disabled={!!busy} onClick={() => switchFacility(x.id)}>{x.name}</button>)}</div>
      </div>
      <div className="grid-2">
        {facilities.map((x) => <FacilityCard key={x.id} f={x} active={x.id === fac?.id} />)}
      </div>
      {fac && (
        <div className="grid-2">
          <Controls fac={fac} apply={apply} reset={() => apply("reset fixture", async () => {
            await api.facilityReset(fac.id);
            return [{ at: new Date().toISOString(), field: `${fac.id}: fixture reset`, old: "overrides", new: "fixture values" }];
          })} />
          <Impact before={before} after={snap("current")} />
        </div>
      )}
      <Panel title="Change log (this browser session)">
        {log.length === 0 ? <div className="pb small muted">No what-if changes yet.</div> : (
          <table className="t"><thead><tr><th>Time</th><th>Field</th><th className="r">Old</th><th className="r">New</th></tr></thead>
            <tbody>{log.map((c, i) => <tr key={i}><td className="mono small">{shortTime(c.at)}</td><td className="mono small">{c.field}</td><td className="r mono small">{JSON.stringify(c.old)}</td><td className="r mono small">{JSON.stringify(c.new)}</td></tr>)}</tbody></table>
        )}
      </Panel>
    </div>
  );
}

function FacilityCard({ f: x, active }: { f: Facility; active: boolean }) {
  return (
    <div className="panel" style={{ borderColor: active ? "var(--accent)" : undefined }}>
      <div className="ph">{x.name}<span className="spacer" /><span className="fictional" style={{ padding: "0 6px" }}><b>FICTIONAL</b></span>{active && <span className="small" style={{ color: "var(--accent)" }}>active</span>}</div>
      <div className="pb">
        <dl className="kv" style={{ marginBottom: 8 }}>
          <dt>w/b bounds</dt><dd className="num">{f(x.mixture_bounds.w_b[0], 2)} – {f(x.mixture_bounds.w_b[1], 2)}</dd>
          <dt>Binder</dt><dd className="num">{f(x.mixture_bounds.binder_kg_m3[0], 0)} – {f(x.mixture_bounds.binder_kg_m3[1], 0)} {UNITS.kgm3}</dd>
          <dt>Max cement replacement</dt><dd className="num">{f(x.mixture_bounds.max_cement_replacement_fraction, 2)}</dd>
          <dt>Lab budget</dt><dd className="num">{x.lab_budget.max_experiments} expts × {f(x.lab_budget.cost_per_experiment_usd, 0)} USD</dd>
          <dt>Planned batch</dt><dd className="num">{f(x.planned_batch_volume_m3, 0)} m³</dd>
          <dt>Model Material Source</dt><dd className="num">{x.model_material_source.value} <K kind={x.model_material_source.provenance.kind} /></dd>
          <dt>Curing temp</dt><dd className="num">{f(x.curing_temp_c.value, 1)} °C</dd>
        </dl>
        <table className="t">
          <thead><tr><th>Material</th><th>Avail.</th><th className="r">Inventory kg</th><th className="r">USD/kg</th><th className="r">GWP factor</th><th>Factor src</th></tr></thead>
          <tbody>{MATERIALS.map((m) => {
            const r = x.materials[m];
            return (
              <tr key={m} style={{ opacity: r.available ? 1 : 0.5 }}>
                <td>{MATERIAL_LABEL[m]}<div className="small muted">{r.supplier}</div></td>
                <td>{r.available ? <span className="status good"><span className="ic">✓</span></span> : <span className="status critical"><span className="ic">✕</span>off</span>}</td>
                <td className="r num">{r.inventory_kg == null ? "∞" : f(r.inventory_kg, 0)}</td>
                <td className="r num">{f(r.cost_per_kg_usd, 3)}</td>
                <td className="r num">{f(r.gwp_factor.value, 4)}<span className="unit">{UNITS.factor}</span></td>
                <td className="small muted">{r.gwp_factor.version} <K kind={r.gwp_factor.published_or_assumed === "published" ? "computed" : "assumed"} /></td>
              </tr>
            );
          })}</tbody>
        </table>
      </div>
    </div>
  );
}

function Controls({ fac, apply, reset }: { fac: Facility; apply: (label: string, fn: () => Promise<Change[]>) => void; reset: () => void }) {
  const { busy } = useDarwin();
  const [mat, setMat] = useState<Material>("fly_ash");
  const [slagInv, setSlagInv] = useState(0);
  const [cementCost, setCementCost] = useState(0);
  const [fMat, setFMat] = useState<Material>("cement");
  const [fVal, setFVal] = useState(0);
  const [maxExp, setMaxExp] = useState(0);
  const [costExp, setCostExp] = useState(0);
  useEffect(() => {
    setSlagInv(fac.materials.slag.inventory_kg ?? 0);
    setCementCost(fac.materials.cement.cost_per_kg_usd ?? 0);
    setMaxExp(fac.lab_budget.max_experiments);
    setCostExp(fac.lab_budget.cost_per_experiment_usd);
  }, [fac]);
  useEffect(() => { setFVal(fac.materials[fMat].gwp_factor.value ?? 0); }, [fac, fMat]);

  const ov = async (body: Parameters<typeof api.overrides>[0]) => {
    const r = await api.overrides(body);
    return r.applied.changes.map((c) => ({ ...c, at: r.applied.at ?? new Date().toISOString(), field: `${fac.id}.${c.field}` }));
  };
  const avail = fac.materials[mat].available;
  return (
    <Panel title={`What-if controls — ${fac.name}`} right={<button className="btn" disabled={!!busy} onClick={reset}>Reset fixture</button>}>
      <div className="pb">
        <div className="fieldset-title">Disable / enable a material</div>
        <div className="row">
          <select value={mat} onChange={(e) => setMat(e.target.value as Material)}>{MATERIALS.map((m) => <option key={m} value={m}>{MATERIAL_LABEL[m]}</option>)}</select>
          <span className="small sec">currently {avail ? "available" : "unavailable"}</span>
          <button className="btn" disabled={!!busy} onClick={() => apply(`${avail ? "disable" : "enable"} ${mat}`, () => ov({ facility_id: fac.id, materials: { [mat]: { available: !avail } }, note: "Factory Reality" }))}>{avail ? "Disable" : "Enable"}</button>
        </div>
        <div className="fieldset-title">Slag inventory</div>
        <div className="row">
          <input type="number" value={slagInv} min={0} step={1000} onChange={(e) => setSlagInv(Number(e.target.value))} style={{ width: 110 }} /><span className="unit">kg</span>
          <button className="btn" disabled={!!busy} onClick={() => apply("set slag inventory", () => ov({ facility_id: fac.id, materials: { slag: { inventory_kg: slagInv } }, note: "Factory Reality" }))}>Apply</button>
          <button className="btn" disabled={!!busy} onClick={() => { const v = Math.round((fac.materials.slag.inventory_kg ?? slagInv) / 2); apply("cut slag inventory 50%", () => ov({ facility_id: fac.id, materials: { slag: { inventory_kg: v } }, note: "Factory Reality: cut 50%" })); }}>Cut 50%</button>
        </div>
        <div className="fieldset-title">Cement price</div>
        <div className="row">
          <input type="number" value={cementCost} min={0} step={0.005} onChange={(e) => setCementCost(Number(e.target.value))} /><span className="unit">{UNITS.usdkg}</span>
          <button className="btn" disabled={!!busy} onClick={() => apply("set cement cost", () => ov({ facility_id: fac.id, materials: { cement: { cost_per_kg_usd: cementCost } }, note: "Factory Reality" }))}>Apply</button>
          <button className="btn" disabled={!!busy} onClick={() => { const v = +((fac.materials.cement.cost_per_kg_usd ?? cementCost) * 1.25).toFixed(4); apply("raise cement cost 25%", () => ov({ facility_id: fac.id, materials: { cement: { cost_per_kg_usd: v } }, note: "Factory Reality: +25%" })); }}>+25%</button>
        </div>
        <div className="small muted">Cost feeds the material-cost figures. The P1 acquisition score does not use cost, so ranking will not move.</div>
        <div className="fieldset-title">GWP factor (new immutable factor version)</div>
        <div className="row">
          <select value={fMat} onChange={(e) => setFMat(e.target.value as Material)}>{MATERIALS.map((m) => <option key={m} value={m}>{MATERIAL_LABEL[m]}</option>)}</select>
          <input type="number" value={fVal} min={0} step={0.01} onChange={(e) => setFVal(Number(e.target.value))} /><span className="unit">{UNITS.factor}</span>
          <button className="btn" disabled={!!busy} onClick={() => apply(`update ${fMat} GWP factor`, async () => {
            const old = fac.materials[fMat].gwp_factor;
            const r = await api.factorUpdate({ facility_id: fac.id, material: fMat, value: fVal, source: "Factory Reality what-if (UI)",
              geography: old.geography, version: `ui-${Date.now()}`, published_or_assumed: "assumed" });
            return [{ at: new Date().toISOString(), field: `${fac.id}.gwp_factor.${fMat}`, old: old.value, new: r.factor.value },
              ...(r.recomputed_impacts.length ? [{ at: new Date().toISOString(), field: `${r.recomputed_impacts.length} impact snapshots recomputed`, old: r.old_factor_id, new: r.new_factor_id }] : [])];
          })}>Apply</button>
        </div>
        <div className="small muted">Factor versions are append-only ledger snapshots. Reset does not roll them back; set the value again to restore it.</div>
        <div className="fieldset-title">Lab budget</div>
        <div className="row">
          <input type="number" value={maxExp} min={1} onChange={(e) => setMaxExp(Number(e.target.value))} style={{ width: 60 }} /><span className="unit">expts</span>
          <input type="number" value={costExp} min={0} step={50} onChange={(e) => setCostExp(Number(e.target.value))} /><span className="unit">USD/expt</span>
          <button className="btn" disabled={!!busy} onClick={() => apply("set lab budget", () => ov({ facility_id: fac.id, max_experiments: maxExp, cost_per_experiment_usd: costExp, note: "Factory Reality" }))}>Apply</button>
        </div>
        <div className="small muted">Lab budget applies to the next replay session started with this facility.</div>
      </div>
    </Panel>
  );
}

function Impact({ before, after }: { before: Snap | null; after: Snap }) {
  const codes = [...new Set([...Object.keys(before?.codes ?? {}), ...Object.keys(after.codes)])].sort();
  const rows: [string, string | number | null, string | number | null][] = [
    ["Accepted candidates", before?.accepted ?? null, after.accepted],
    ["Rejected candidates", before?.rejected ?? null, after.rejected],
    ["Pareto (predicted) size", before?.pareto ?? null, after.pareto],
    ["Mean materials GWP, accepted (kgCO₂e/m³)", before?.meanGwp != null ? f(before.meanGwp) : null, after.meanGwp != null ? f(after.meanGwp) : null],
    ["Top-ranked candidate", before?.top ?? null, after.top],
    ["Top acquisition score", before?.topScore != null ? f(before.topScore, 3) : null, after.topScore != null ? f(after.topScore, 3) : null],
    ["Top predicted 28d (MPa)", before?.topPred != null ? f(before.topPred) : null, after.topPred != null ? f(after.topPred) : null],
  ];
  return (
    <Panel title="Recomputed by the API" right={before ? <span className="small muted">after: {before.label}</span> : null}>
      <div className="pb">
        {!before && <div className="small muted" style={{ marginBottom: 6 }}>Apply a change to see before → after. The values shown are the current state.</div>}
        <table className="t"><thead><tr><th /><th className="r">before</th><th className="r">after</th></tr></thead>
          <tbody>
            {rows.map(([k, b, a]) => <tr key={k}><td>{k}</td><td className="r num muted">{b ?? "—"}</td><td className="r num" style={{ color: b !== null && String(b) !== String(a) ? "var(--text-primary)" : undefined }}>{a ?? "—"}{b !== null && String(b) !== String(a) ? " •" : ""}</td></tr>)}
            {codes.map((c) => <tr key={c}><td className="mono small">rejected: {c}</td><td className="r num muted">{before ? before.codes[c] ?? 0 : "—"}</td><td className="r num">{after.codes[c] ?? 0}</td></tr>)}
          </tbody></table>
        <div className="small muted" style={{ marginTop: 6 }}>Seeded generation (same n and seed) means differences come from the constraint change, not from resampling.</div>
      </div>
    </Panel>
  );
}

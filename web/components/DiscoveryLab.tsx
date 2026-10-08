"use client";
import { useEffect, useMemo, useState } from "react";
import { api, MATERIALS, MATERIAL_LABEL, type Mode } from "@/lib/api";
import { UNITS, f, shortTime } from "@/lib/format";
import { useDarwin } from "./DarwinProvider";
import { DesignSpace } from "./DesignSpace";
import { Inspector } from "./Inspector";
import { ErrorBox, Panel } from "./ui";

type Draft = {
  facility_id: string; target: number; use1d: boolean; target1d: number; budget: number;
  restricted: string[]; useCap: boolean; cap: number; wbLo: number; wbHi: number; labBudget: number; mode: Mode;
  n: number; seed: number;
};

export function DiscoveryLab() {
  const d = useDarwin();
  const { objective, facilities } = d;
  const fac = facilities.find((x) => x.id === objective?.facility_id);
  const [draft, setDraft] = useState<Draft | null>(null);

  // (Re)initialise the draft from backend state whenever it changes.
  useEffect(() => {
    if (!objective || !fac) return;
    setDraft({
      facility_id: objective.facility_id, target: objective.target_strength_mpa,
      use1d: objective.secondary_target_1d_mpa !== null, target1d: objective.secondary_target_1d_mpa ?? 10,
      budget: objective.carbon_budget_kgco2e_m3, restricted: objective.restricted_materials,
      useCap: objective.replacement_cap !== null, cap: objective.replacement_cap ?? fac.mixture_bounds.max_cement_replacement_fraction,
      wbLo: fac.mixture_bounds.w_b[0], wbHi: fac.mixture_bounds.w_b[1], labBudget: fac.lab_budget.max_experiments,
      mode: objective.acquisition_mode, n: d.genParams.n, seed: d.genParams.seed,
    });
  }, [objective, fac, d.genParams]);

  const apply = () => draft && d.run("applying objective → generate → rank", async () => {
    await d.saveObjective({
      facility_id: draft.facility_id, target_strength_mpa: draft.target, target_age_days: 28,
      secondary_target_1d_mpa: draft.use1d ? draft.target1d : null, carbon_budget_kgco2e_m3: draft.budget,
      acquisition_mode: draft.mode, restricted_materials: draft.restricted, replacement_cap: draft.useCap ? draft.cap : null,
    });
    const target = facilities.find((x) => x.id === draft.facility_id);
    if (target && (target.mixture_bounds.w_b[0] !== draft.wbLo || target.mixture_bounds.w_b[1] !== draft.wbHi
      || target.lab_budget.max_experiments !== draft.labBudget)) {
      await api.overrides({ facility_id: draft.facility_id, w_b: [draft.wbLo, draft.wbHi], max_experiments: draft.labBudget,
        note: "Discovery Lab config panel" });
      await d.refreshFacilities();
    }
    const p = { n: draft.n, seed: draft.seed };
    d.setGenParams(p);
    await d.recompute(draft.facility_id, p);
  });

  const onFacility = (id: string) => {
    const nf = facilities.find((x) => x.id === id);
    if (!draft || !nf) return;
    setDraft({ ...draft, facility_id: id, wbLo: nf.mixture_bounds.w_b[0], wbHi: nf.mixture_bounds.w_b[1],
      labBudget: nf.lab_budget.max_experiments, cap: nf.mixture_bounds.max_cement_replacement_fraction });
  };

  return (
    <div className="stack">
      <ErrorBox error={d.error} />
      <div className="grid-lab">
        <Panel title="Objective & constraints">
          {draft ? <Config draft={draft} set={setDraft} onFacility={onFacility} onApply={apply} /> : <div className="pb muted">loading…</div>}
        </Panel>
        <DesignSpace />
        <Inspector />
      </div>
      <Timeline />
    </div>
  );
}

function Num({ v, on, step = 1, min, max, w }: { v: number; on: (n: number) => void; step?: number; min?: number; max?: number; w?: number }) {
  return <input type="number" value={v} step={step} min={min} max={max} style={w ? { width: w } : undefined}
    onChange={(e) => on(e.target.value === "" ? 0 : Number(e.target.value))} />;
}

function Config({ draft, set, onFacility, onApply }: { draft: Draft; set: (d: Draft) => void; onFacility: (id: string) => void; onApply: () => void }) {
  const { facilities, busy, gen, objective } = useDarwin();
  const fac = facilities.find((x) => x.id === draft.facility_id);
  const u = (p: Partial<Draft>) => set({ ...draft, ...p });
  const dirty = objective && (objective.facility_id !== draft.facility_id || objective.target_strength_mpa !== draft.target
    || objective.carbon_budget_kgco2e_m3 !== draft.budget || objective.acquisition_mode !== draft.mode);
  return (
    <div className="pb">
      <div className="fieldset-title">Facility</div>
      <div className="field"><label>Site</label>
        <select value={draft.facility_id} onChange={(e) => onFacility(e.target.value)}>
          {facilities.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </select></div>
      <div className="small muted" style={{ marginTop: -3 }}>FICTIONAL fixture; all values assumed.</div>

      <div className="fieldset-title">Targets</div>
      <div className="field"><label>28-day strength ≥</label><span className="ctl"><Num v={draft.target} on={(v) => u({ target: v })} step={1} min={1} w={64} /><span className="unit">MPa</span></span></div>
      <div className="field"><label><input type="checkbox" checked={draft.use1d} onChange={(e) => u({ use1d: e.target.checked })} /> 1-day strength ≥</label>
        <span className="ctl"><Num v={draft.target1d} on={(v) => u({ target1d: v })} w={64} /><span className="unit">MPa</span></span></div>
      <div className="small muted" style={{ marginTop: -3 }}>The 1-day target is stored in the objective. P1 acquisition does not score it yet.</div>
      <div className="field" style={{ marginTop: 6 }}><label>Carbon budget ≤</label><span className="ctl"><Num v={draft.budget} on={(v) => u({ budget: v })} step={5} min={1} w={64} /><span className="unit">{UNITS.gwp}</span></span></div>

      <div className="fieldset-title">Material availability</div>
      {MATERIALS.map((m) => {
        const facOff = fac && !fac.materials[m].available;
        return (
          <div key={m} className="field" style={{ marginBottom: 3 }}>
            <label style={{ opacity: facOff ? 0.5 : 1 }}>
              <input type="checkbox" disabled={facOff} checked={!draft.restricted.includes(m) && !facOff}
                onChange={(e) => u({ restricted: e.target.checked ? draft.restricted.filter((x) => x !== m) : [...draft.restricted, m] })} />{" "}
              {MATERIAL_LABEL[m]}
            </label>
            <span className="small muted">{facOff ? "unavailable at site" : fac?.materials[m].inventory_kg != null ? `inv ${f(fac.materials[m].inventory_kg, 0)} kg` : ""}</span>
          </div>
        );
      })}

      <div className="fieldset-title">Mixture bounds</div>
      <div className="field"><label><input type="checkbox" checked={draft.useCap} onChange={(e) => u({ useCap: e.target.checked })} /> Cement replacement cap</label>
        <span className="ctl"><Num v={draft.cap} on={(v) => u({ cap: v })} step={0.05} min={0} max={1} w={56} /><span className="unit">frac</span></span></div>
      <div className="small muted" style={{ marginTop: -3 }}>Unchecked: facility cap {fac ? f(fac.mixture_bounds.max_cement_replacement_fraction, 2) : "—"} applies.</div>
      <div className="field" style={{ marginTop: 6 }}><label>w/b ratio</label>
        <span className="ctl"><Num v={draft.wbLo} on={(v) => u({ wbLo: v })} step={0.01} w={56} />–<Num v={draft.wbHi} on={(v) => u({ wbHi: v })} step={0.01} w={56} /></span></div>
      <div className="field"><label>Lab budget</label><span className="ctl"><Num v={draft.labBudget} on={(v) => u({ labBudget: v })} min={1} w={56} /><span className="unit">expts</span></span></div>

      <div className="fieldset-title">Acquisition</div>
      <div className="seg" role="group" aria-label="acquisition mode">
        {(["exploit", "balanced", "explore"] as Mode[]).map((m) => (
          <button key={m} aria-pressed={draft.mode === m} onClick={() => u({ mode: m })}>{m}</button>
        ))}
      </div>
      <div className="field" style={{ marginTop: 8 }}><label>Candidates n / seed</label>
        <span className="ctl"><Num v={draft.n} on={(v) => u({ n: v })} min={1} max={2000} w={56} /><Num v={draft.seed} on={(v) => u({ seed: v })} w={44} /></span></div>

      <button className="btn primary" style={{ width: "100%", marginTop: 8 }} disabled={!!busy} onClick={onApply}>
        Apply &amp; recompute{dirty ? " •" : ""}
      </button>
      {gen && (
        <div className="small muted" style={{ marginTop: 8 }}>
          Last run: {gen.n_requested} generated (seed {gen.seed}) → <span className="sec">{gen.n_accepted} accepted</span>, {gen.n_rejected} rejected.
          {Object.entries(gen.rejection_code_counts).map(([k, v]) => <div key={k} className="mono">{k} ×{v}</div>)}
        </div>
      )}
    </div>
  );
}

function Timeline() {
  const { history, setSelected, selected, run, refreshHistory, compositionOf } = useDarwin();
  const exps = useMemo(() => [...(history?.experiments ?? [])].sort((a, b) => a.proposed_at.localeCompare(b.proposed_at)), [history]);
  const act = (id: string, to: string) => run(`transition ${id} → ${to}`, async () => {
    await api.transition(id, to, "Discovery Lab timeline");
    await refreshHistory();
  });
  return (
    <Panel title={`Experiment timeline (ledger) — ${exps.length} experiments`}
      right={<span className="small muted">PROPOSED → APPROVED → AWAITING_RESULT → MEASURED | REJECTED</span>}>
      {exps.length === 0 ? <div className="pb muted small">No experiments in the ledger yet. Propose one from the inspector, or run Experiment Replay.</div> : (
        <div className="timeline">
          {exps.map((e) => {
            const p = e.predicted.target_age ?? (e.predicted.mean_mpa !== undefined ? { mean_mpa: e.predicted.mean_mpa, std_mpa: e.predicted.std_mpa ?? 0 } : null);
            const m28 = e.measured?.strength_28d_mpa;
            return (
              <div key={e.id} className={`tl-item ${selected === e.candidate_id ? "sel" : ""}`}
                onClick={() => compositionOf(e.candidate_id) && setSelected(e.candidate_id)}>
                <div className="row"><span className="mono">{e.id}</span><span className="spacer" /><span className="small muted">{shortTime(e.proposed_at)}</span></div>
                <div className="row small"><span className="mono sec">{e.candidate_id}</span>
                  <span className="spacer" /><span className="mono" style={{ color: e.status === "MEASURED" ? "var(--s-aqua)" : e.status === "REJECTED" ? "var(--critical)" : "var(--text-secondary)" }}>{e.status}</span></div>
                <div className="small">
                  {p && <span title="predicted">○ {f(p.mean_mpa)} ± {f(p.std_mpa)} MPa</span>}
                  {m28 != null && <span title="measured" style={{ marginLeft: 8 }}>● {f(m28)} MPa</span>}
                  {e.session_id ? <span className="muted"> · replay</span> : <span className="muted"> · generated (not measurable)</span>}
                </div>
                {e.status === "PROPOSED" && (
                  <div className="row" style={{ marginTop: 4 }} onClick={(ev) => ev.stopPropagation()}>
                    <button className="btn" onClick={() => act(e.id, "APPROVED")}>Approve</button>
                    <button className="btn danger" onClick={() => act(e.id, "REJECTED")}>Reject</button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

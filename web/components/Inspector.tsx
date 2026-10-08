"use client";
import { api, MATERIALS, MATERIAL_COLUMN, MATERIAL_LABEL, type Composition, type PredictOut } from "@/lib/api";
import { UNITS, f } from "@/lib/format";
import { useDarwin } from "./DarwinProvider";
import { usePredict } from "./usePredict";
import { ErrorBox, FeasBadge, K, N, Panel, Reasons } from "./ui";

export function Inspector() {
  const d = useDarwin();
  const { selected, objective, rank, space, gen } = d;
  const src = selected ? d.compositionOf(selected) : null;
  const pred = usePredict(src?.composition, objective?.facility_id);
  const ranked = rank?.ranked.find((r) => r.candidate_id === selected);
  const rejected = gen?.rejected.find((c) => c.candidate_id === selected);
  const measured = space?.measured_train_mixtures.find((m) => m.mix === selected);
  const age = objective?.target_age_days ?? 28;

  if (!selected || !src) {
    return (
      <Panel title="Inspector">
        <div className="pb muted small">Click a point in the design space to see its composition, prediction, carbon, feasibility and acquisition evidence.</div>
      </Panel>
    );
  }
  const p = pred.data;
  const atAge = p?.strength.find((s) => s.age_days === age);
  const propose = () => d.run(`propose ${selected}`, async () => {
    await api.rank({ candidate_ids: [selected], top_k: 1, propose_top: 1 });
    await d.refreshHistory();
  });

  return (
    <Panel title={<>Inspector · <span className="mono" style={{ textTransform: "none" }}>{selected}</span></>}
      right={src.source === "train" ? <K kind="measured" /> : rejected ? <span className="small muted">rejected</span> : <K kind="predicted" />}>
      <div className="pb stack" style={{ gap: 12 }}>
        <ErrorBox error={pred.error} />
        {src.source === "generated" && <div className="small muted">Generated candidate. Prediction only: it can never be measured in replay.</div>}
        {src.source === "train" && <div className="small muted">Training-split mixture. The measured values are real CSV rows; the model prediction below is in-sample.</div>}

        <section>
          <div className="fieldset-title">Composition <span className="unit">{UNITS.kgm3}</span></div>
          <CompositionTable c={src.composition} />
        </section>

        <section>
          <div className="fieldset-title">Strength at {age} d</div>
          {measured && (
            <div className="row"><K kind="measured" /><N v={measured.strength_at_target_age_mpa} unit="MPa" /><span className="small muted">CSV mean of 3 cylinders</span></div>
          )}
          {pred.loading && <div className="small muted">predicting…</div>}
          {atAge && (
            <>
              <div className="row"><K kind="predicted" /><span className="big">{f(atAge.mean_mpa)}</span><span className="sec num">± {f(atAge.std_mpa)}</span><span className="unit">MPa (1σ latent)</span></div>
              <div className="small sec">95% PI <span className="num">[{f(atAge.pi95_mpa[0])}, {f(atAge.pi95_mpa[1])}] MPa</span> · target {f(objective?.target_strength_mpa, 0)} MPa</div>
              <div className="small muted" title={JSON.stringify(p?.strength_provenance)}>{String(p?.strength_provenance?.source ?? "")} · {p?.model_role}</div>
            </>
          )}
        </section>

        {p && <Carbon p={p} />}

        {p && (
          <section>
            <div className="fieldset-title">Feasibility</div>
            <div className="row" style={{ marginBottom: 4 }}><FeasBadge status={p.feasibility.status} /><K kind="computed" /></div>
            <div className="small sec">w/b <span className="num">{f(p.feasibility.derived.w_b, 3)}</span> · binder <span className="num">{f(p.feasibility.derived.binder_kg_m3, 0)} kg/m³</span> · SCM fraction <span className="num">{f(p.feasibility.derived.scm_fraction_of_binder, 3)}</span> (cap {f(p.feasibility.derived.replacement_cap, 2)})</div>
            <Reasons feas={p.feasibility} />
          </section>
        )}

        <section>
          <div className="fieldset-title">Acquisition</div>
          {ranked ? (
            <>
              <div className="row"><K kind="computed" /><span className="big">{f(ranked.score, 3)}</span><span className="sec">rank {ranked.rank} of {rank?.n_scored} · mode {rank?.mode}</span></div>
              <table className="t" style={{ marginTop: 4 }}>
                <thead><tr><th>Component</th><th className="r">Value</th><th className="r">Weight</th></tr></thead>
                <tbody>
                  {([
                    ["p_meet_target", "P(strength ≥ target)", 3], ["ei_norm", "Expected improvement (norm.)", 3],
                    ["uncertainty_norm", "Uncertainty (norm.)", 3], ["carbon_score", "Carbon score", 3],
                    ["over_budget_penalty", "Over-budget penalty", 3], ["ei_raw_mpa", "EI raw (MPa)", 2],
                  ] as const).map(([k, label, dd]) => (
                    <tr key={k}><td>{label}</td><td className="r num">{f(ranked.components[k], dd)}</td><td className="r num muted">{ranked.weights[k] !== undefined ? f(ranked.weights[k], 2) : ""}</td></tr>
                  ))}
                </tbody>
              </table>
              <div className="small muted" style={{ marginTop: 4 }}>GWP basis: {ranked.gwp_used.basis}</div>
            </>
          ) : <div className="small muted">{rejected ? "Not scored: rejected candidates never enter acquisition." : src.source === "train" ? "Not scored: training mixtures are already measured." : "Not in the current ranking."}</div>}
        </section>

        {ranked?.review_route && (
          <section>
            <div className="fieldset-title">Evidence / why proposed</div>
            <ul className="tight small">
              <li>Ranked #{ranked.rank} by the deterministic {rank?.mode} acquisition (numbers only; no LLM).</li>
              <li>P(meets {f(objective?.target_strength_mpa, 0)} MPa) = {f(ranked.components.p_meet_target, 3)}, predicted {f(ranked.prediction.mean_mpa)} ± {f(ranked.prediction.std_mpa)} MPa.</li>
              <li>Review route <span className="mono">{ranked.review_route.route}</span> — {ranked.review_route.route_description}</li>
              {ranked.review_route.triggered.map((t, i) => <li key={i} className="mono">{t.code}</li>)}
              <li><K kind="simulated" /> {ranked.review_route.label} (reviewer: {ranked.review_route.reviewer}); routing cannot change numbers.</li>
            </ul>
            <button className="btn primary" style={{ marginTop: 6 }} disabled={!!d.busy} onClick={propose}>Propose as experiment (ledger)</button>
          </section>
        )}
        {p && <div className="small muted">{p.disclaimer}</div>}
      </div>
    </Panel>
  );
}

export function CompositionTable({ c }: { c: Composition }) {
  return (
    <table className="t">
      <tbody>
        {MATERIALS.map((m) => (
          <tr key={m}><td>{MATERIAL_LABEL[m]}</td><td className="r num">{f(c[MATERIAL_COLUMN[m]] ?? c[m], 1)}</td></tr>
        ))}
        {"Material Source" in c && <tr><td className="muted">Material Source (model input)</td><td className="r num">{c["Material Source"]}</td></tr>}
        {"Temp (C)" in c && <tr><td className="muted">Curing temp</td><td className="r num">{f(c["Temp (C)"], 1)} °C</td></tr>}
      </tbody>
    </table>
  );
}

function Carbon({ p }: { p: PredictOut }) {
  const g = p.gwp.darwin_materials_gwp;
  const bx = p.gwp.boxcrete_gwp_model_output;
  return (
    <section>
      <div className="fieldset-title">Carbon estimate</div>
      <div className="row"><K kind="computed" /><span className="big">{f(g.value)}</span><span className="unit">{UNITS.gwp}</span>
        {!g.complete && <span className="status warning"><span className="ic">!</span>incomplete: missing {g.missing_factors.join(", ")}</span>}</div>
      <table className="t" style={{ marginTop: 4 }}>
        <thead><tr><th>Material</th><th className="r">kg</th><th className="r">Factor</th><th className="r">kgCO₂e</th><th>Factor provenance</th></tr></thead>
        <tbody>
          {g.lines.map((l) => (
            <tr key={l.material}>
              <td>{MATERIAL_LABEL[l.material as keyof typeof MATERIAL_LABEL] ?? l.material}</td>
              <td className="r num">{f(l.qty_kg, 0)}</td>
              <td className="r num">{l.factor?.value != null ? f(l.factor.value, 4) : "—"}</td>
              <td className="r num">{f(l.contribution_kgco2e, 1)}</td>
              <td className="small">{l.factor ? <><K kind={l.factor.published_or_assumed === "published" ? "computed" : "assumed"} /> <span className="muted">{l.factor.source} · {l.factor.version}{l.factor.fictional ? " · FICTIONAL" : ""}</span></> : <span className="muted">{l.status}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="small muted" style={{ marginTop: 4 }}>{g.boundary.statement}</div>
      {bx && <div className="small sec" style={{ marginTop: 4 }}>BOxCrete GWP model output (separate field, never summed): <span className="num">{f(bx.value)} {UNITS.gwp}</span></div>}
      <div className="small sec">Material cost <K kind="computed" /> <span className="num">{f(p.cost.value, 2)} {UNITS.usdm3}</span> <span className="muted">(assumed fixture prices)</span></div>
    </section>
  );
}

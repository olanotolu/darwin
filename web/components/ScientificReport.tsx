"use client";
import { useEffect, useState } from "react";
import { api, MATERIALS, MATERIAL_LABEL, type Experiment, type Facility, type Objective, type ReplayUpdate, type RouteDecision } from "@/lib/api";
import { UNITS, downloadJson, f, pct } from "@/lib/format";
import { useDarwin } from "./DarwinProvider";
import { ErrorBox, K, fmtAny } from "./ui";

type Metrics = { mae_mpa: number; rmse_mpa: number; n: number; pi95_coverage?: number; label?: string; provenance?: { source?: string } };
type Report = {
  generated_at: string; darwin_version: string; disclaimers: string[];
  dataset: { data_card: Record<string, unknown>; split: Record<string, unknown>; model: Record<string, unknown>; router: Record<string, unknown> };
  objective: Objective; facilities: Facility[];
  environmental_boundary: { name: string; statement: string; [k: string]: unknown }; gwp_fields_note: string;
  review_taxonomy: Record<string, string>;
  ledger: { experiments: Experiment[]; transitions: unknown[]; model_snapshots: { id: string; version: string; label: string; created_at: string }[];
    router_decisions: { route: string; reviewer: string; output: RouteDecision }[]; calc_snapshots: unknown[] };
  baselines: { model: Metrics; train_mean_per_age: Metrics; ridge: Metrics } | null;
  replay: { id: string; steps: { experiment_id: string; mix: string }[]; updates: ReplayUpdate[]; excluded: unknown[] } | null;
  rejected_generated_candidates: unknown[];
  negative_results: { experiment_id: string; kind: string; measured_mpa?: number; predicted_mpa?: number }[];
  citation: string;
};

export function ScientificReport() {
  const { run, busy, error } = useDarwin();
  const [r, setR] = useState<Report | null>(null);
  const load = () => run("loading /report/export", async () => setR(await api.report() as unknown as Report));
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);
  const baselines = () => run("computing baselines on held-out split", async () => { await api.baselines(); await load(); });

  if (!r) return <div className="stack"><ErrorBox error={error} /><div className="notice">{busy ?? "loading report…"}</div></div>;
  const exps = r.ledger.experiments;
  const measured = exps.filter((e) => e.status === "MEASURED");
  const proposedOnly = exps.filter((e) => e.status !== "MEASURED");
  const fac = r.facilities.find((x) => x.id === r.objective.facility_id);
  const routes = r.ledger.router_decisions.reduce<Record<string, number>>((m, d) => ({ ...m, [d.route]: (m[d.route] ?? 0) + 1 }), {});
  const outsidePi = measured.filter((e) => { const p = e.predicted.target_age; const m = e.measured?.rows.find((x) => x.age_days === p?.age_days); return p && m && !(p.pi95_mpa[0] <= m.strength_mpa && m.strength_mpa <= p.pi95_mpa[1]); });
  const strategies = [...new Set(exps.map((e) => e.acquisition_components?.mode).filter(Boolean))];
  const dc = r.dataset.data_card;

  return (
    <div className="report stack">
      <ErrorBox error={error} />
      <div className="row">
        <span className="sec">Evidence packet generated <span className="mono">{r.generated_at}</span> · DARWIN {r.darwin_version}</span>
        <span className="spacer" />
        <button className="btn" disabled={!!busy} onClick={load}>Reload</button>
        <button className="btn" disabled={!!busy} onClick={baselines}>{r.baselines ? "Recompute baselines" : "Compute baselines"}</button>
        <button className="btn primary" onClick={() => downloadJson(`darwin-report-${r.generated_at.replace(/[:]/g, "")}.json`, r)}>Download JSON</button>
      </div>
      <div className="legend" style={{ padding: 0 }}><span>Every number carries a tag:</span><K kind="measured" /><K kind="predicted" /><K kind="assumed" /><K kind="computed" /><K kind="simulated" /></div>

      <Sec title="1 · Objective">
        <dl className="kv">
          <dt>Target</dt><dd><span className="num">≥ {f(r.objective.target_strength_mpa, 1)} MPa at {r.objective.target_age_days} d</span> <K kind="assumed" /></dd>
          <dt>Secondary 1-day target</dt><dd>{r.objective.secondary_target_1d_mpa != null ? <span className="num">≥ {f(r.objective.secondary_target_1d_mpa)} MPa</span> : "—"} <span className="muted small">(stored; not scored in P1)</span></dd>
          <dt>Carbon budget</dt><dd><span className="num">≤ {f(r.objective.carbon_budget_kgco2e_m3, 0)} {UNITS.gwp}</span> <K kind="assumed" /></dd>
          <dt>Facility</dt><dd>{fac?.name ?? r.objective.facility_id} <b className="small">FICTIONAL</b></dd>
          <dt>Acquisition mode</dt><dd>{r.objective.acquisition_mode}</dd>
          <dt>Restricted materials</dt><dd>{r.objective.restricted_materials.join(", ") || "none"}</dd>
          <dt>Objective source</dt><dd className="small muted">{String(r.objective.provenance?.source)}</dd>
        </dl>
      </Sec>

      <Sec title="2 · Data & model provenance">
        <dl className="kv">
          <dt>Dataset</dt><dd className="small">{fmtAny(dc.path)} · sha256 <span className="mono">{String(dc.sha256).slice(0, 16)}</span> · BOxCrete commit <span className="mono">{String(dc.boxcrete_commit).slice(0, 10)}</span></dd>
          <dt>Rows / mixtures</dt><dd className="num">{fmtAny(dc.rows)} rows / {fmtAny(dc.mixtures)} mixtures <K kind="measured" /></dd>
          <dt>Split</dt><dd className="small"><span className="mono">{fmtAny(r.dataset.split.split_id)}</span> · {fmtAny(r.dataset.split.n_train_mixes)} train / {fmtAny(r.dataset.split.n_heldout_mixes)} held-out mixtures (whole-mixture split, seed {fmtAny(r.dataset.split.seed)})</dd>
          <dt>Model</dt><dd className="small">{fmtAny(r.dataset.model.label)} · <span className="mono">{fmtAny(r.dataset.model.version)}</span> · fit {f(r.dataset.model.fit_seconds as number, 1)} s on {fmtAny(r.dataset.model.n_train_rows)} rows</dd>
          <dt>Model snapshots</dt><dd className="num">{r.ledger.model_snapshots.length}</dd>
          <dt>Reviewer</dt><dd className="small">{fmtAny(r.dataset.router.reviewer)} ({fmtAny(r.dataset.router.model_version)}) <K kind="simulated" /></dd>
          <dt>Citation</dt><dd className="small">{r.citation}</dd>
        </dl>
      </Sec>

      <Sec title="3 · Strategy">
        <ul className="tight small">
          <li>Deterministic acquisition (exploit / balanced / explore weights over P(meet target), EI, uncertainty and carbon score, with an over-budget penalty). Modes used in the ledger: {strategies.join(", ") || "none yet"}.</li>
          <li>Replay: {r.replay ? <>session <span className="mono">{r.replay.id}</span>, {r.replay.steps.length} steps, {r.replay.updates.length} model updates, {r.replay.excluded.length} pool mixtures excluded by constraints.</> : "no replay session run."}</li>
          <li>Review routing runs after ranking and cannot change numbers. Routes issued: {Object.entries(routes).map(([k, v]) => `${k} ×${v}`).join(", ") || "none"}.</li>
        </ul>
      </Sec>

      <Sec title={`4 · Proposed vs measured (${measured.length} measured, ${proposedOnly.length} not measured)`}>
        <table className="t">
          <thead><tr><th>Experiment</th><th>Mix</th><th>Status</th><th className="r">Predicted <K kind="predicted" /></th><th className="r">Measured <K kind="measured" /></th><th className="r">Error MPa <K kind="computed" /></th><th>In 95% PI</th><th className="r">Carbon score <K kind="computed" /></th></tr></thead>
          <tbody>{exps.map((e) => {
            const p = e.predicted.target_age ?? (e.predicted.mean_mpa !== undefined ? { mean_mpa: e.predicted.mean_mpa, std_mpa: e.predicted.std_mpa ?? 0, age_days: e.predicted.age_days ?? 28, pi95_mpa: null } : null);
            const m = e.measured?.rows.find((x) => x.age_days === p?.age_days);
            const pi = p && "pi95_mpa" in p && p.pi95_mpa ? p.pi95_mpa as [number, number] : null;
            return (
              <tr key={e.id}><td className="mono">{e.id}</td><td className="mono">{e.candidate_id}</td><td className="mono small">{e.status}</td>
                <td className="r num">{p ? `○ ${f(p.mean_mpa)} ± ${f(p.std_mpa)}` : "—"}</td>
                <td className="r num">{m ? `● ${f(m.strength_mpa)}` : <span className="muted small">{e.session_id ? "not revealed" : "not measurable (generated)"}</span>}</td>
                <td className="r num">{m && p ? f(m.strength_mpa - p.mean_mpa, 2) : "—"}</td>
                <td>{m && pi ? (pi[0] <= m.strength_mpa && m.strength_mpa <= pi[1] ? "✓" : "✕") : ""}</td>
                <td className="r small muted">{e.acquisition_components ? f(e.acquisition_components.carbon_score, 2) : "—"}</td></tr>
            );
          })}</tbody>
        </table>
        {exps.length === 0 && <div className="small muted">Ledger is empty.</div>}
        {r.replay && r.replay.updates.length > 0 && (
          <table className="t" style={{ marginTop: 8 }}><thead><tr><th>Model update</th><th className="r">rows added</th><th className="r">MAE before → after (MPa) <K kind="computed" /></th><th className="r">PI95 coverage</th></tr></thead>
            <tbody>{r.replay.updates.map((u) => <tr key={u.new_version}><td className="mono small">{u.old_version} → {u.new_version}</td><td className="r num">{u.added_rows}</td><td className="r num">{f(u.test_mae_before_mpa, 2)} → {f(u.test_mae_after_mpa, 2)}</td><td className="r num">{pct(u.test_pi95_coverage_before, 1)} → {pct(u.test_pi95_coverage_after, 1)}</td></tr>)}</tbody></table>
        )}
        <div className="small muted" style={{ marginTop: 4 }}>Carbon score is the acquisition component (0–1), not GWP.</div>
      </Sec>

      <Sec title="5 · Constraints">
        {r.facilities.map((x) => (
          <div key={x.id} className="small" style={{ marginBottom: 4 }}>
            <b>{x.name}</b> <K kind="assumed" />: w/b {f(x.mixture_bounds.w_b[0], 2)}–{f(x.mixture_bounds.w_b[1], 2)}; binder {f(x.mixture_bounds.binder_kg_m3[0], 0)}–{f(x.mixture_bounds.binder_kg_m3[1], 0)} {UNITS.kgm3};
            max cement replacement {f(x.mixture_bounds.max_cement_replacement_fraction, 2)}; unavailable: {MATERIALS.filter((m) => !x.materials[m].available).map((m) => MATERIAL_LABEL[m]).join(", ") || "none"};
            lab budget {x.lab_budget.max_experiments} experiments.
          </div>
        ))}
        <div className="small muted">Rejected generated candidates kept in the packet: {r.rejected_generated_candidates.length}.</div>
      </Sec>

      <Sec title="6 · Costs & emission factors">
        {r.facilities.map((x) => (
          <table key={x.id} className="t" style={{ marginBottom: 8 }}>
            <thead><tr><th>{x.name}</th><th className="r">USD/kg <K kind="assumed" /></th><th className="r">GWP factor ({UNITS.factor})</th><th>Factor source · version</th><th>Type</th></tr></thead>
            <tbody>{MATERIALS.map((m) => { const r2 = x.materials[m]; return (
              <tr key={m}><td>{MATERIAL_LABEL[m]}</td><td className="r num">{f(r2.cost_per_kg_usd, 3)}</td><td className="r num">{f(r2.gwp_factor.value, 4)}</td>
                <td className="small muted">{r2.gwp_factor.source} · {r2.gwp_factor.version}</td><td><K kind={r2.gwp_factor.published_or_assumed === "published" ? "computed" : "assumed"} title={r2.gwp_factor.published_or_assumed} /></td></tr>); })}</tbody>
          </table>
        ))}
        <div className="small">{r.environmental_boundary.statement}</div>
        <div className="small muted">{r.gwp_fields_note}</div>
      </Sec>

      <Sec title="7 · Baselines (held-out, whole unseen mixtures)">
        {r.baselines ? (
          <table className="t"><thead><tr><th>Predictor</th><th className="r">MAE MPa</th><th className="r">RMSE MPa</th><th className="r">n rows</th><th className="r">PI95 coverage</th></tr></thead>
            <tbody>{([["model", "DARWIN model (BOxCrete)"], ["train_mean_per_age", "Train mean per age"], ["ridge", "Ridge on composition"]] as const).map(([k, label]) => {
              const b = r.baselines![k];
              return <tr key={k}><td>{b?.label ?? label} <K kind="computed" /></td><td className="r num">{f(b?.mae_mpa, 2)}</td><td className="r num">{f(b?.rmse_mpa, 2)}</td><td className="r num">{b?.n}</td><td className="r num">{b?.pi95_coverage !== undefined ? pct(b.pi95_coverage, 1) : "—"}</td></tr>;
            })}</tbody></table>
        ) : <div className="small muted">Not computed in this backend session. Use “Compute baselines” (POST /baselines/compare).</div>}
      </Sec>

      <Sec title="8 · Limitations & disclaimers">
        <ul className="tight small">{r.disclaimers.map((x, i) => <li key={i}>{x}</li>)}</ul>
        <div className="fieldset-title">Negative results (kept)</div>
        {r.negative_results.length ? <ul className="tight small">{r.negative_results.map((n, i) => <li key={i}><span className="mono">{n.experiment_id}</span>: {n.kind}{n.measured_mpa !== undefined && <> — measured ● {f(n.measured_mpa)} MPa vs predicted ○ {f(n.predicted_mpa)} MPa</>}</li>)}</ul> : <div className="small muted">None recorded.</div>}
      </Sec>

      <Sec title="9 · Open questions (derived from this packet)">
        <ul className="tight small">
          {outsidePi.length > 0 && <li>{outsidePi.length} revealed measurement(s) fell outside the pre-reveal 95% PI ({outsidePi.map((e) => e.candidate_id).join(", ")}). Is the uncertainty calibrated for this facility&apos;s region of the space?</li>}
          {(routes.DOMAIN_SHIFT ?? 0) > 0 && <li>{routes.DOMAIN_SHIFT} review decision(s) flagged DOMAIN_SHIFT. Facility materials may differ from the dataset&apos;s Material Sources.</li>}
          {(routes.EXPERT_ESCALATION ?? 0) > 0 && <li>{routes.EXPERT_ESCALATION} decision(s) escalated to an expert.</li>}
          {r.baselines && r.baselines.ridge.mae_mpa < r.baselines.model.mae_mpa && <li>Ridge beats the GP on held-out MAE ({f(r.baselines.ridge.mae_mpa, 2)} vs {f(r.baselines.model.mae_mpa, 2)} MPa). Where does the GP add value beyond its uncertainty?</li>}
          {proposedOnly.filter((e) => !e.session_id).length > 0 && <li>{proposedOnly.filter((e) => !e.session_id).length} generated-candidate proposal(s) can only be validated by real lab work. No measurement exists for them.</li>}
          <li>The GWP boundary is materials-only and uses assumed factors. A1–A3 results need supplier EPDs.</li>
          <li>The 1-day (precast) target is not yet part of the acquisition objective.</li>
        </ul>
      </Sec>
    </div>
  );
}

function Sec({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="panel"><h2>{title}</h2><div className="pb">{children}</div></section>;
}

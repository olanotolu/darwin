"use client";
import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, ErrorBar, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import {
  api, type Experiment, type Mode, type PredictOut, type ReplayNextOut, type ReplayStartOut, type ReplayUpdate, type RevealOut, type SpaceOut,
} from "@/lib/api";
import { UNITS, f, pct } from "@/lib/format";
import { useDarwin } from "./DarwinProvider";
import { CompositionTable } from "./Inspector";
import { ErrorBox, FeasBadge, Glyph, K, N, Panel } from "./ui";

type SpaceSummary = { version: string; role: string; nAboveTarget: number; meanStd: number; n: number; best: { id: string; mean: number } | null };
function summarise(s: SpaceOut, target: number): SpaceSummary {
  const acc = s.candidates.filter((c) => c.accepted);
  const best = acc.reduce<{ id: string; mean: number } | null>((b, c) => (!b || c.pred_mean_mpa > b.mean ? { id: c.candidate_id, mean: c.pred_mean_mpa } : b), null);
  return {
    version: s.model.version, role: s.model.role, n: acc.length, best,
    nAboveTarget: acc.filter((c) => c.pred_mean_mpa >= target).length,
    meanStd: acc.length ? acc.reduce((t, c) => t + c.pred_std_mpa, 0) / acc.length : NaN,
  };
}

type AfterView = { update: ReplayUpdate; mix: string; before: Experiment; after: PredictOut; spaceBefore: SpaceSummary | null; spaceAfter: SpaceSummary | null };

export function ExperimentReplay() {
  const d = useDarwin();
  const { objective, facilities, history, run, busy } = d;
  const fac = facilities.find((x) => x.id === objective?.facility_id);
  const session = history?.replay_session ?? null;
  const [start, setStart] = useState<ReplayStartOut | null>(null);
  const [applyFac, setApplyFac] = useState(true);
  const [budget, setBudget] = useState<number>(10);
  const [mode, setMode] = useState<Mode>("balanced");
  const [autoApprove, setAutoApprove] = useState(true);
  const [nextOut, setNextOut] = useState<Record<string, ReplayNextOut>>({});
  const [reveals, setReveals] = useState<Record<string, RevealOut>>({});
  const [after, setAfter] = useState<AfterView | null>(null);

  useEffect(() => { if (fac) setBudget(fac.lab_budget.max_experiments); }, [fac]);
  useEffect(() => { if (objective) setMode(objective.acquisition_mode); }, [objective]);

  const exps = useMemo(() => (history?.experiments ?? []).filter((e) => session && e.session_id === session.id)
    .sort((a, b) => a.proposed_at.localeCompare(b.proposed_at)), [history, session]);
  const updates = session?.updates ?? [];
  const trained = new Set(updates.flatMap((u) => u.added_experiments));
  const current = exps[exps.length - 1];
  const open = current && ["PROPOSED", "APPROVED", "AWAITING_RESULT"].includes(current.status) ? current : null;
  const pendingUpdate = exps.filter((e) => e.status === "MEASURED" && !trained.has(e.id));
  const budgetLeft = session ? session.budget - session.steps.length : 0;
  const target = objective?.target_strength_mpa ?? 40;

  const doStart = () => run("starting replay session", async () => {
    const r = await api.replayStart({ facility_id: objective?.facility_id, apply_facility_constraints: applyFac, budget });
    setStart(r); setNextOut({}); setReveals({}); setAfter(null);
    await d.refreshHistory();
  });
  const doNext = () => run("proposing next experiment (prediction recorded before reveal)", async () => {
    const r = await api.replayNext({ mode, auto_approve: autoApprove });
    setNextOut((m) => ({ ...m, [r.experiment.id]: r }));
    setAfter(null);
    await d.refreshHistory();
  });
  const doTransition = (to: string) => open && run(`→ ${to}`, async () => {
    await api.transition(open.id, to, "Experiment Replay operator");
    if (to === "APPROVED") await api.transition(open.id, "AWAITING_RESULT", "sent to (replayed) lab");
    await d.refreshHistory();
  });
  const doReveal = () => open && run("revealing held-out measurement", async () => {
    const r = await api.replayReveal(open.id);
    setReveals((m) => ({ ...m, [open.id]: r }));
    await d.refreshHistory();
  });
  const doUpdate = () => run("refitting replay model on approved measurements", async () => {
    const spaceBefore = await api.space(objective?.facility_id, true).catch(() => null);
    const u = await api.replayUpdate();
    const lastId = u.added_experiments[u.added_experiments.length - 1];
    const before = exps.find((e) => e.id === lastId)!;
    const [afterPred, spaceAfter] = await Promise.all([
      api.predict(before.composition, before.facility_id, true),
      api.space(objective?.facility_id, true).catch(() => null),
    ]);
    setAfter({ update: u, mix: before.candidate_id, before, after: afterPred,
      spaceBefore: spaceBefore && summarise(spaceBefore, target), spaceAfter: spaceAfter && summarise(spaceAfter, target) });
    await d.refreshHistory();
  });

  const shown = open ?? current;
  return (
    <div className="stack">
      <ErrorBox error={d.error} />
      <Panel title="Experiment replay — sequential discovery on real held-out mixtures"
        right={session ? <span className="small muted">session <span className="mono">{session.id}</span> · {session.steps.length}/{session.budget} experiments · budget left {budgetLeft}</span> : null}>
        <div className="pb">
          <div className="row">
            <label><input type="checkbox" checked={applyFac} onChange={(e) => setApplyFac(e.target.checked)} /> apply {fac?.name ?? "facility"} constraints to pool</label>
            <label className="row">lab budget <input type="number" value={budget} min={1} onChange={(e) => setBudget(Number(e.target.value))} style={{ width: 56 }} /> expts</label>
            <button className="btn" disabled={!!busy} onClick={doStart}>{session ? "Start new session" : "Start replay session"}</button>
            <span className="spacer" />
            <span className="small muted">mode</span>
            <div className="seg">{(["exploit", "balanced", "explore"] as Mode[]).map((m) => <button key={m} aria-pressed={mode === m} onClick={() => setMode(m)}>{m}</button>)}</div>
            <label className="small"><input type="checkbox" checked={autoApprove} onChange={(e) => setAutoApprove(e.target.checked)} /> auto-approve (logged as automated operator action)</label>
          </div>
          <div className="small muted" style={{ marginTop: 6 }}>
            The pool is the real held-out concrete mixtures from split <span className="mono">{d.meta?.split.split_id}</span>. Their measured strengths stay inside the
            backend oracle until you press Reveal. Each prediction is written to the ledger before the reveal.
            {start && <> Pool {start.pool_size}: {start.n_eligible} eligible, {start.n_excluded} excluded by constraints.</>}
          </div>
        </div>
      </Panel>

      {session && (
        <div className="grid-3">
          <StepCard n={1} title="Propose next experiment" active={!open && budgetLeft > 0}>
            <button className="btn primary" disabled={!!busy || !!open || budgetLeft <= 0} onClick={doNext}>Propose next experiment</button>
            {pendingUpdate.length > 0 && !open && <div className="small" style={{ color: "var(--warning)", marginTop: 6 }}>{pendingUpdate.length} measured result(s) not yet used by the model. Update first to learn from them.</div>}
            {budgetLeft <= 0 && <div className="small muted" style={{ marginTop: 6 }}>Lab budget exhausted.</div>}
            {shown && <Proposal e={shown} next={nextOut[shown.id]} />}
          </StepCard>
          <StepCard n={2} title="Reveal held-out measurement" active={!!open && open.status === "AWAITING_RESULT"}>
            {open?.status === "PROPOSED" && (
              <div className="row" style={{ marginBottom: 6 }}>
                <span className="small sec">Awaiting operator review:</span>
                <button className="btn" onClick={() => doTransition("APPROVED")}>Approve → send to lab</button>
                <button className="btn danger" onClick={() => doTransition("REJECTED")}>Reject</button>
              </div>
            )}
            <button className="btn primary" disabled={!!busy || open?.status !== "AWAITING_RESULT"} onClick={doReveal}>Reveal held-out measurement</button>
            {shown && <RevealView e={shown} r={reveals[shown.id]} target={target} />}
          </StepCard>
          <StepCard n={3} title="Update model" active={pendingUpdate.length > 0 && !open}>
            <button className="btn primary" disabled={!!busy || pendingUpdate.length === 0 || !!open} onClick={doUpdate}>Update model ({pendingUpdate.length} new)</button>
            <div className="small muted" style={{ marginTop: 6 }}>Warm-start BOxCrete refit on train rows plus APPROVED measurements only (about 3 s). The startup model is never modified.</div>
            {after ? <AfterPanel a={after} /> : updates.length > 0 && <UpdateMetrics u={updates[updates.length - 1]} />}
          </StepCard>
        </div>
      )}

      {session && exps.length > 0 && <ReplayChart exps={exps} target={target} />}
      {session && <Ledger exps={exps} updates={updates} target={target} />}
      {!session && <div className="notice">No replay session yet. Start one to run the propose → reveal → update loop.</div>}
    </div>
  );
}

function StepCard({ n, title, active, children }: { n: number; title: string; active: boolean; children: React.ReactNode }) {
  return (
    <div className="panel" style={{ borderColor: active ? "var(--accent)" : undefined }}>
      <div className="ph"><span className="mono">{n}</span>{title}</div>
      <div className="pb">{children}</div>
    </div>
  );
}

function Proposal({ e, next }: { e: Experiment; next?: ReplayNextOut }) {
  const p = e.predicted.target_age;
  const comps = e.acquisition_components;
  return (
    <div className="stack" style={{ gap: 8, marginTop: 10 }}>
      <div className="row"><span className="mono">{e.id}</span><span className="mono sec">{e.candidate_id}</span><span className="spacer" /><span className="mono small">{e.status}</span></div>
      {p && (
        <div>
          <div className="row"><K kind="predicted" /><span className="big">{f(p.mean_mpa)}</span><span className="sec num">± {f(p.std_mpa)}</span><span className="unit">MPa @ {p.age_days} d</span></div>
          <div className="small sec">95% PI <span className="num">[{f(p.pi95_mpa[0])}, {f(p.pi95_mpa[1])}] MPa</span> · model <span className="mono">{e.predicted.model_version}</span>
            {e.predicted.made_before_reveal && <> · recorded before reveal</>}</div>
        </div>
      )}
      {comps && <div className="small sec">acquisition {f(e.acquisition_score, 3)} · rank {comps.rank}/{comps.n_ranked} · {comps.mode} · P(≥target) {f(comps.p_meet_target, 3)} · EI {f(comps.ei_raw_mpa, 2)} MPa · carbon score {f(comps.carbon_score, 3)}</div>}
      {e.constraint_eval && <FeasBadge status={e.constraint_eval.status} />}
      {next && (
        <>
          <div className="small">GWP <K kind="computed" /> <span className="num">{f(next.gwp.darwin_materials_gwp.value)} {UNITS.gwp}</span></div>
          <div className="small">review route <span className="mono">{next.review_route.route}</span> <K kind="simulated" /> <span className="muted">{next.review_route.label}</span></div>
          <table className="t"><thead><tr><th>Top 5 (this step)</th><th className="r">score</th><th className="r">pred MPa</th></tr></thead>
            <tbody>{next.top5.map((r) => <tr key={r.candidate_id} className={r.candidate_id === e.candidate_id ? "sel" : ""}><td className="mono">{r.candidate_id}</td><td className="r num">{f(r.score, 3)}</td><td className="r num">○ {f(r.prediction.mean_mpa)} ± {f(r.prediction.std_mpa)}</td></tr>)}</tbody></table>
        </>
      )}
      <details><summary className="small sec">composition (kg/m³)</summary><CompositionTable c={e.composition} /></details>
    </div>
  );
}

function RevealView({ e, r, target }: { e: Experiment; r?: RevealOut; target: number }) {
  if (!e.measured) return <div className="sealed" style={{ marginTop: 10 }}>Sealed. The recorded measurement for <span className="mono">{e.candidate_id}</span> stays inside the oracle until you reveal it.</div>;
  const p = e.predicted.target_age!;
  const m = e.measured.rows.find((x) => x.age_days === p.age_days);
  const err = m ? m.strength_mpa - p.mean_mpa : null;
  const inPi = m ? p.pi95_mpa[0] <= m.strength_mpa && m.strength_mpa <= p.pi95_mpa[1] : null;
  return (
    <div className="stack" style={{ gap: 8, marginTop: 10 }}>
      <div className="row"><K kind="measured" /><span className="big" style={{ color: "var(--s-aqua)" }}>{f(m?.strength_mpa)}</span><span className="sec num">± {f(m?.strength_std_mpa)}</span><span className="unit">MPa @ {p.age_days} d (n={m?.n_cylinders})</span></div>
      <dl className="kv">
        <dt>predicted (before)</dt><dd className="num">○ {f(p.mean_mpa)} ± {f(p.std_mpa)} MPa</dd>
        <dt>prediction error</dt><dd className="num">{err !== null ? `${err >= 0 ? "+" : ""}${f(err, 2)} MPa` : "—"} {r?.comparison && <span className="muted">(z = {f(r.comparison.z, 2)})</span>}</dd>
        <dt>within 95% PI</dt><dd>{inPi === null ? "—" : inPi ? <span className="status good"><span className="ic">✓</span>yes</span> : <span className="status critical"><span className="ic">✕</span>no</span>}</dd>
        <dt>met {f(target, 0)} MPa target</dt><dd>{m ? (m.strength_mpa >= target ? <span className="status good"><span className="ic">✓</span>yes</span> : <span className="status serious"><span className="ic">✕</span>no (negative result, kept)</span>) : "—"}</dd>
      </dl>
      <table className="t"><thead><tr><th>age</th><th className="r">predicted</th><th className="r">measured</th></tr></thead>
        <tbody>{e.measured.rows.map((row) => {
          const pr = e.predicted.by_age?.find((x) => x.age_days === row.age_days);
          return <tr key={row.age_days}><td>{row.age_days} d</td><td className="r num">○ {f(pr?.mean_mpa)}</td><td className="r num">● {f(row.strength_mpa)}</td></tr>;
        })}</tbody></table>
      {r && <div className="small">post-measurement review <span className="mono">{r.post_measurement_review.route}</span> <K kind="simulated" /></div>}
      <div className="small muted">{String(e.measured.provenance.source)}</div>
    </div>
  );
}

function UpdateMetrics({ u }: { u: ReplayUpdate }) {
  return (
    <dl className="kv" style={{ marginTop: 10 }}>
      <dt>model version</dt><dd className="mono small">{u.old_version} → <b>{u.new_version}</b></dd>
      <dt>rows added</dt><dd className="num">{u.added_rows} ({u.added_experiments.join(", ")})</dd>
      <dt>fit</dt><dd className="num">{u.fit_mode ?? "—"} · {f(u.fit_seconds, 1)} s</dd>
      <dt>MAE on unrevealed held-out</dt><dd className="num">{f(u.test_mae_before_mpa, 2)} → {f(u.test_mae_after_mpa, 2)} MPa <span className="muted">(n={u.n_test_rows} rows)</span></dd>
      <dt>95% PI coverage</dt><dd className="num">{pct(u.test_pi95_coverage_before, 1)} → {pct(u.test_pi95_coverage_after, 1)}</dd>
    </dl>
  );
}

function AfterPanel({ a }: { a: AfterView }) {
  const age = a.before.predicted.target_age?.age_days ?? 28;
  const pb = a.before.predicted.target_age;
  const pa = a.after.strength.find((s) => s.age_days === age);
  const m = a.before.measured?.rows.find((r) => r.age_days === age);
  return (
    <div className="stack" style={{ gap: 8 }}>
      <UpdateMetrics u={a.update} />
      <div className="fieldset-title">What the model knew: {a.mix} @ {age} d</div>
      <table className="t"><tbody>
        <tr><td>before (v {a.update.old_version.slice(-12)})</td><td className="r num">○ {f(pb?.mean_mpa)} ± {f(pb?.std_mpa)} MPa</td></tr>
        <tr><td>after (v {a.update.new_version.slice(-12)})</td><td className="r num">○ {f(pa?.mean_mpa)} ± {f(pa?.std_mpa)} MPa</td></tr>
        <tr><td>measured</td><td className="r num">● {f(m?.strength_mpa)} MPa</td></tr>
      </tbody></table>
      {a.spaceBefore && a.spaceAfter && (
        <>
          <div className="fieldset-title">Recomputed candidate space (generated, predicted)</div>
          <table className="t"><thead><tr><th /><th className="r">before</th><th className="r">after</th></tr></thead><tbody>
            <tr><td>candidates predicted ≥ target</td><td className="r num">{a.spaceBefore.nAboveTarget}/{a.spaceBefore.n}</td><td className="r num">{a.spaceAfter.nAboveTarget}/{a.spaceAfter.n}</td></tr>
            <tr><td>mean σ (MPa)</td><td className="r num">{f(a.spaceBefore.meanStd, 2)}</td><td className="r num">{f(a.spaceAfter.meanStd, 2)}</td></tr>
            <tr><td>highest predicted</td><td className="r num">{a.spaceBefore.best?.id} {f(a.spaceBefore.best?.mean)}</td><td className="r num">{a.spaceAfter.best?.id} {f(a.spaceAfter.best?.mean)}</td></tr>
          </tbody></table>
        </>
      )}
    </div>
  );
}

type ChartPt = { step: number; y: number; err?: [number, number]; id: string; kind: "predicted" | "measured" };
function ReplayChart({ exps, target }: { exps: Experiment[]; target: number }) {
  const pred: ChartPt[] = [];
  const meas: ChartPt[] = [];
  exps.forEach((e, i) => {
    const p = e.predicted.target_age;
    if (p) pred.push({ step: i + 1 - 0.08, y: p.mean_mpa, err: [p.mean_mpa - p.pi95_mpa[0], p.pi95_mpa[1] - p.mean_mpa], id: `${e.candidate_id} (${e.id})`, kind: "predicted" });
    const m = e.measured?.rows.find((r) => r.age_days === (p?.age_days ?? 28));
    if (m) meas.push({ step: i + 1 + 0.08, y: m.strength_mpa, id: `${e.candidate_id} (${e.id})`, kind: "measured" });
  });
  return (
    <Panel title="Pre-reveal prediction vs revealed measurement, per step (MPa)"
      right={<div className="legend" style={{ padding: 0 }}><span><Glyph type="hollow" color="var(--s-blue)" />predicted ± 95% PI</span><span><Glyph type="solid" color="var(--s-aqua)" />measured (held-out)</span></div>}>
      <div style={{ height: 240 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 12, right: 24, bottom: 20, left: 6 }}>
            <CartesianGrid stroke="#2c2c2a" />
            <XAxis type="number" dataKey="step" domain={[0.5, exps.length + 0.5]} ticks={exps.map((_, i) => i + 1)} stroke="#383835" tick={{ fill: "#898781", fontSize: 11 }}
              label={{ value: "replay step", position: "insideBottom", offset: -10, fill: "#898781", fontSize: 11 }} />
            <YAxis type="number" dataKey="y" domain={["auto", "auto"]} stroke="#383835" tick={{ fill: "#898781", fontSize: 11 }} />
            <ReferenceLine y={target} stroke="#898781" strokeDasharray="4 3" label={{ value: `target ${target} MPa`, position: "insideTopRight", fill: "#898781", fontSize: 10.5 }} />
            <Scatter data={pred} isAnimationActive={false} shape={(p: { cx?: number; cy?: number }) => <circle cx={p.cx} cy={p.cy} r={5} fill="#1a1a19" stroke="#3987e5" strokeWidth={1.5} />}>
              <ErrorBar dataKey="err" direction="y" stroke="#3987e5" width={5} strokeWidth={1} />
            </Scatter>
            <Scatter data={meas} isAnimationActive={false} shape={(p: { cx?: number; cy?: number }) => <circle cx={p.cx} cy={p.cy} r={5} fill="#199e70" stroke="#1a1a19" strokeWidth={1.5} />} />
            <Tooltip cursor={false} content={({ active, payload }) => {
              const pt = active && payload?.[0] ? (payload[0].payload as ChartPt) : null;
              return pt ? <div className="tt"><div className="h">{pt.id}</div>{pt.kind === "measured" ? "● measured" : "○ predicted before reveal"} <span className="num">{f(pt.y)} MPa</span></div> : null;
            }} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </Panel>
  );
}

function Ledger({ exps, updates, target }: { exps: Experiment[]; updates: ReplayUpdate[]; target: number }) {
  return (
    <Panel title="Replay ledger">
      <div style={{ overflowX: "auto" }}>
        <table className="t">
          <thead><tr><th>#</th><th>Experiment</th><th>Mix</th><th>Status</th><th>Model at proposal</th><th className="r">Pred. 28d</th><th className="r">95% PI</th>
            <th className="r">Measured 28d</th><th className="r">Error</th><th>In PI</th><th>Target</th><th>Used in update</th></tr></thead>
          <tbody>
            {exps.map((e, i) => {
              const p = e.predicted.target_age;
              const m = e.measured?.rows.find((r) => r.age_days === (p?.age_days ?? 28));
              const u = updates.find((x) => x.added_experiments.includes(e.id));
              return (
                <tr key={e.id}>
                  <td className="num">{i + 1}</td><td className="mono">{e.id}</td><td className="mono">{e.candidate_id}</td><td className="mono small">{e.status}</td>
                  <td className="mono small muted">{e.predicted.model_version}</td>
                  <td className="r"><N v={p?.mean_mpa} pm={p?.std_mpa} /></td>
                  <td className="r num small">{p ? `${f(p.pi95_mpa[0])}–${f(p.pi95_mpa[1])}` : "—"}</td>
                  <td className="r">{m ? <span className="num" style={{ color: "var(--s-aqua)" }}>● {f(m.strength_mpa)}</span> : <span className="muted small">sealed</span>}</td>
                  <td className="r num">{m && p ? f(m.strength_mpa - p.mean_mpa, 2) : "—"}</td>
                  <td>{m && p ? (p.pi95_mpa[0] <= m.strength_mpa && m.strength_mpa <= p.pi95_mpa[1] ? "✓" : "✕") : ""}</td>
                  <td>{m ? (m.strength_mpa >= target ? <span style={{ color: "var(--good)" }}>met</span> : <span style={{ color: "var(--serious)" }}>missed</span>) : ""}</td>
                  <td className="mono small">{u ? `${u.new_version.slice(-14)} · MAE ${f(u.test_mae_before_mpa, 2)}→${f(u.test_mae_after_mpa, 2)}` : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="small muted" style={{ padding: "6px 10px" }}>All strengths are in MPa. Predicted values are the latent mean ± 1σ recorded before the reveal. Measured values are BOxCrete CSV held-out rows (mean of 3 cylinders). Misses are kept as negative results.</div>
    </Panel>
  );
}

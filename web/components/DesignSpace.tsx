"use client";
import { useMemo, useState } from "react";
import {
  CartesianGrid, ErrorBar, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from "recharts";
import { UNITS, f } from "@/lib/format";
import { useDarwin } from "./DarwinProvider";
import { Glyph, Panel } from "./ui";

type Pt = {
  id: string; x: number; y: number; sd?: number; err?: number; kind: "measured" | "predicted" | "rejected";
  status?: string; codes?: string[]; score?: number; rank?: number; pareto?: boolean; complete: boolean;
};
type ShapeProps = { cx?: number; cy?: number; payload?: Pt };

const C = {
  pred: "var(--s-blue)", meas: "var(--s-aqua)", next: "var(--s-orange)", dim: "var(--dim)",
  grid: "#2c2c2a", axis: "#383835", muted: "#898781", surface: "#1a1a19",
};

export function DesignSpace() {
  const { space, rank, selected, setSelected, objective, gen } = useDarwin();
  const [show, setShow] = useState({ rejected: true, measured: true, pareto: true });

  const { accepted, rejected, measured, paretoPred, paretoMeas, next, sel } = useMemo(() => {
    const score = new Map(rank?.ranked.map((r) => [r.candidate_id, r]) ?? []);
    const cand = space?.candidates ?? [];
    const toPt = (c: (typeof cand)[number]): Pt => ({
      id: c.candidate_id, x: c.gwp_kgco2e_m3, y: c.pred_mean_mpa, sd: c.pred_std_mpa, err: c.pred_std_mpa,
      kind: c.accepted ? "predicted" : "rejected", status: c.status, codes: c.rejection_codes,
      score: score.get(c.candidate_id)?.score, rank: score.get(c.candidate_id)?.rank, pareto: c.pareto, complete: c.gwp_complete,
    });
    const accepted = cand.filter((c) => c.accepted).map(toPt);
    const rejected = cand.filter((c) => !c.accepted).map(toPt);
    const measured: Pt[] = (space?.measured_train_mixtures ?? []).filter((m) => m.strength_at_target_age_mpa !== null).map((m) => ({
      id: m.mix, x: m.gwp_kgco2e_m3, y: m.strength_at_target_age_mpa as number, kind: "measured", pareto: m.pareto, complete: m.gwp_complete,
    }));
    const paretoPred = accepted.filter((p) => p.pareto).sort((a, b) => a.x - b.x);
    const paretoMeas = measured.filter((p) => p.pareto).sort((a, b) => a.x - b.x);
    const nextId = rank?.ranked[0]?.candidate_id;
    const next = accepted.filter((p) => p.id === nextId);
    const sel = [...accepted, ...rejected, ...measured].filter((p) => p.id === selected);
    return { accepted, rejected, measured, paretoPred, paretoMeas, next, sel };
  }, [space, rank, selected]);

  const click = (p?: Pt) => p && setSelected(p.id);
  const hollow = (color: string, r: number, w = 1.5) => (props: ShapeProps) => (
    <circle cx={props.cx} cy={props.cy} r={r} fill={C.surface} fillOpacity={0.01} stroke={color} strokeWidth={w}
      style={{ cursor: "pointer" }} onClick={() => click(props.payload)} />
  );
  const solid = (props: ShapeProps) => (
    <circle cx={props.cx} cy={props.cy} r={4} fill={C.meas} stroke={C.surface} strokeWidth={1.5}
      style={{ cursor: "pointer" }} onClick={() => click(props.payload)} />
  );
  const nextShape = (props: ShapeProps) => (
    <g style={{ cursor: "pointer" }} onClick={() => click(props.payload)}>
      <circle cx={props.cx} cy={props.cy} r={9} fill="none" stroke={C.next} strokeWidth={2} />
      <circle cx={props.cx} cy={props.cy} r={4.5} fill="none" stroke={C.pred} strokeWidth={1.5} />
      <text x={(props.cx ?? 0) + 12} y={(props.cy ?? 0) - 8} fill="#f2f1ec" fontSize={10.5} fontFamily="var(--mono)">NEXT {props.payload?.id}</text>
    </g>
  );
  const selShape = (props: ShapeProps) => (
    <circle cx={props.cx} cy={props.cy} r={12} fill="none" stroke="#f2f1ec" strokeWidth={1} strokeDasharray="2 2" pointerEvents="none" />
  );
  const none = () => <g />;

  const target = objective?.target_strength_mpa;
  const budget = objective?.carbon_budget_kgco2e_m3;
  const counts = `${accepted.length} predicted · ${rejected.length} rejected · ${measured.length} measured (train)`;

  return (
    <Panel title={`Design space — predicted ${space?.target_age_days ?? 28}-day strength vs materials GWP`}
      right={<span className="small muted">{counts}</span>}>
      <div className="legend">
        <span><Glyph type="hollow" color={C.pred} />Predicted candidate (unmeasured)</span>
        <span><Glyph type="solid" color={C.meas} />Measured mixture (train split)</span>
        <span><Glyph type="line" color={C.pred} />Pareto: predicted</span>
        <span><Glyph type="dashed" color={C.meas} />Pareto: measured</span>
        <span><Glyph type="dim" color="#6b6a64" />Rejected / infeasible</span>
        <span><Glyph type="ring" color={C.next} />Next proposed experiment</span>
      </div>
      <div className="row small" style={{ padding: "0 10px" }}>
        {(["rejected", "measured", "pareto"] as const).map((k) => (
          <label key={k}><input type="checkbox" checked={show[k]} onChange={(e) => setShow({ ...show, [k]: e.target.checked })} /> {k}</label>
        ))}
        <span className="spacer" />
        {space && <span className="muted">model: <span className="mono">{space.model.version}</span> ({space.model.role})</span>}
      </div>
      <div style={{ height: 470, padding: "4px 6px 0 0" }}>
        {!space ? <div className="pb muted">loading design space…</div> : (
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 14, right: 18, bottom: 28, left: 8 }}>
              <CartesianGrid stroke={C.grid} strokeWidth={1} />
              <XAxis type="number" dataKey="x" name="GWP" domain={["auto", "auto"]} stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }}
                label={{ value: `Materials GWP (partial boundary), ${UNITS.gwp}`, position: "insideBottom", offset: -16, fill: C.muted, fontSize: 11 }} />
              <YAxis type="number" dataKey="y" name="Strength" domain={["auto", "auto"]} stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }}
                label={{ value: "28-day strength, MPa", angle: -90, position: "insideLeft", offset: 14, fill: C.muted, fontSize: 11 }} />
              {target !== undefined && <ReferenceLine y={target} stroke={C.muted} strokeDasharray="4 3"
                label={{ value: `target ${f(target, 0)} MPa`, position: "insideTopRight", fill: C.muted, fontSize: 10.5 }} />}
              {budget !== undefined && <ReferenceLine x={budget} stroke={C.muted} strokeDasharray="4 3"
                label={{ value: `budget ${f(budget, 0)}`, position: "insideTopLeft", fill: C.muted, fontSize: 10.5 }} />}
              {show.rejected && <Scatter name="rejected" data={rejected} shape={hollow("#55544f", 2.5, 1)} isAnimationActive={false} />}
              {show.measured && <Scatter name="measured" data={measured} shape={solid} isAnimationActive={false} />}
              {show.pareto && show.measured && paretoMeas.length > 1 && <Scatter name="pareto-meas" data={paretoMeas} shape={none}
                line={{ stroke: C.meas, strokeWidth: 1.5, strokeDasharray: "4 3" }} isAnimationActive={false} legendType="none" />}
              <Scatter name="predicted" data={accepted} shape={hollow(C.pred, 4)} isAnimationActive={false} />
              {show.pareto && paretoPred.length > 1 && <Scatter name="pareto-pred" data={paretoPred} shape={none}
                line={{ stroke: C.pred, strokeWidth: 2 }} isAnimationActive={false} />}
              <Scatter name="next" data={next} shape={nextShape} isAnimationActive={false} />
              <Scatter name="selected" data={sel} shape={selShape} isAnimationActive={false}>
                {sel[0]?.err !== undefined && <ErrorBar dataKey="err" direction="y" stroke="#f2f1ec" width={6} strokeWidth={1} />}
              </Scatter>
              <Tooltip cursor={false} isAnimationActive={false} content={<Tip />} />
            </ScatterChart>
          </ResponsiveContainer>
        )}
      </div>
      <div className="small muted" style={{ padding: "0 10px 8px" }}>
        Predicted points show the model&apos;s latent posterior mean; the error bar on the selected point is ±1σ. GWP is DARWIN&apos;s materials-only
        figure computed with the facility&apos;s assumed factors (FICTIONAL); it is not an A1–A3 EPD. Measured points come from the training split only.
        Held-out labels are never shown here.
        {gen && gen.n_accepted === 0 && <div className="err" style={{ marginTop: 6 }}>No candidate passed the constraints. Relax the bounds and recompute.</div>}
      </div>
    </Panel>
  );
}

function Tip({ active, payload }: { active?: boolean; payload?: { payload: Pt }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  if (!p?.id) return null;
  return (
    <div className="tt">
      <div className="h">{p.id}</div>
      <div className="sec">{p.kind === "measured" ? "● measured (train split)" : p.kind === "predicted" ? "○ predicted, unmeasured" : "rejected / infeasible"}</div>
      <div>strength <span className="num">{f(p.y)}{p.sd !== undefined ? ` ± ${f(p.sd)}` : ""} MPa</span></div>
      <div>GWP <span className="num">{f(p.x)} {UNITS.gwp}</span>{!p.complete && <span className="muted"> (incomplete)</span>}</div>
      {p.score !== undefined && <div>acquisition <span className="num">{f(p.score, 3)}</span> · rank {p.rank}</div>}
      {p.pareto && <div className="muted">on Pareto front</div>}
      {p.codes && p.codes.length > 0 && <div className="mono small" style={{ color: "#f0b4b4" }}>{p.codes.join(", ")}</div>}
    </div>
  );
}

"use client";
// Shared instrument state. Everything here is a mirror of backend state, loaded
// from the real API; nothing is synthesised on the client.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import {
  api, ApiError, errText,
  type Composition, type Facility, type GenerateOut, type HistoryOut, type Metadata, type Objective, type RankOut, type SpaceOut,
} from "@/lib/api";

export type Backend = { state: "connecting" | "up" | "down"; since: number; detail?: string; upstream?: string; attempts: number };

type Ctx = {
  backend: Backend;
  meta: Metadata | null;
  facilities: Facility[];
  fictionalLabel: string;
  objective: Objective | null;
  gen: GenerateOut | null;
  rank: RankOut | null;
  space: SpaceOut | null;
  history: HistoryOut | null;
  genParams: { n: number; seed: number };
  setGenParams: (p: { n: number; seed: number }) => void;
  busy: string | null;
  error: string | null;
  setError: (e: string | null) => void;
  selected: string | null;
  setSelected: (id: string | null) => void;
  /** generate (seeded) -> rank all accepted -> fetch the predicted space */
  recompute: (facilityId?: string, params?: { n: number; seed: number }) => Promise<void>;
  refreshFacilities: () => Promise<Facility[]>;
  refreshHistory: () => Promise<void>;
  saveObjective: (o: Partial<Objective>) => Promise<Objective>;
  compositionOf: (id: string) => { composition: Composition; source: "generated" | "train" | "experiment" } | null;
  run: <T>(label: string, fn: () => Promise<T>) => Promise<T | undefined>;
};

const C = createContext<Ctx | null>(null);
export const useDarwin = () => {
  const c = useContext(C);
  if (!c) throw new Error("useDarwin outside provider");
  return c;
};

const OBJECTIVE_KEYS = ["target_strength_mpa", "target_age_days", "secondary_target_1d_mpa", "carbon_budget_kgco2e_m3",
  "facility_id", "acquisition_mode", "restricted_materials", "replacement_cap", "planned_volume_m3"] as const;

export function DarwinProvider({ children }: { children: ReactNode }) {
  const [backend, setBackend] = useState<Backend>({ state: "connecting", since: Date.now(), attempts: 0 });
  const [meta, setMeta] = useState<Metadata | null>(null);
  const [facilities, setFacilities] = useState<Facility[]>([]);
  const [fictionalLabel, setFictionalLabel] = useState("");
  const [objective, setObjective] = useState<Objective | null>(null);
  const [gen, setGen] = useState<GenerateOut | null>(null);
  const [rank, setRank] = useState<RankOut | null>(null);
  const [space, setSpace] = useState<SpaceOut | null>(null);
  const [history, setHistory] = useState<HistoryOut | null>(null);
  const [genParams, setGenParams] = useState({ n: 200, seed: 0 });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const booted = useRef(false);

  const run = useCallback(async <T,>(label: string, fn: () => Promise<T>) => {
    setBusy(label);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(`${label}: ${errText(e)}`);
      return undefined;
    } finally {
      setBusy(null);
    }
  }, []);

  const refreshFacilities = useCallback(async () => {
    const r = await api.facilities();
    setFacilities(r.facilities);
    setFictionalLabel(r.label);
    return r.facilities;
  }, []);
  const refreshHistory = useCallback(async () => setHistory(await api.history()), []);

  const recompute = useCallback(async (facilityId?: string, params?: { n: number; seed: number }) => {
    const p = params ?? genParams;
    const g = await api.generate(p.n, p.seed, facilityId);
    setGen(g);
    let r: RankOut | null = null;
    if (g.n_accepted > 0) r = await api.rank({ top_k: Math.min(500, g.n_accepted) });
    setRank(r);
    setSpace(await api.space(g.facility_id));
    await refreshHistory();
  }, [genParams, refreshHistory]);

  const saveObjective = useCallback(async (o: Partial<Objective>) => {
    const body: Partial<Objective> = {};
    const src = { ...(objective ?? {}), ...o } as Record<string, unknown>;
    for (const k of OBJECTIVE_KEYS) if (src[k] !== undefined) (body as Record<string, unknown>)[k] = src[k];
    const saved = await api.setObjective(body);
    setObjective(saved);
    return saved;
  }, [objective]);

  // Cold start: uvicorn only binds after the ~75-85 s BOxCrete startup fit, so the
  // proxy answers 503 backend_unreachable until then. Poll honestly; no fake progress.
  useEffect(() => {
    let stop = false;
    let attempts = 0;
    const started = Date.now();
    const tick = async () => {
      attempts += 1;
      try {
        const m = await api.metadata();
        if (stop) return;
        setMeta(m);
        setBackend({ state: "up", since: started, attempts });
      } catch (e) {
        if (stop) return;
        const d = e instanceof ApiError ? (e.detail as { upstream?: string; detail?: string } | string) : String(e);
        setBackend({
          state: attempts === 1 ? "connecting" : "down", since: started, attempts,
          detail: typeof d === "string" ? d : (d?.detail ?? JSON.stringify(d)),
          upstream: typeof d === "object" && d ? d.upstream : undefined,
        });
        setTimeout(tick, 4000);
      }
    };
    tick();
    return () => { stop = true; };
  }, []);

  useEffect(() => {
    if (backend.state !== "up" || booted.current) return;
    booted.current = true;
    run("loading instrument state", async () => {
      const [, o] = await Promise.all([refreshFacilities(), api.getObjective()]);
      setObjective(o);
      // Seeded generation is deterministic, so this reproduces the candidate set
      // for the current objective and gives the UI the compositions it needs.
      await recompute(o.facility_id);
    });
  }, [backend.state, run, refreshFacilities, recompute]);

  const compositionOf = useCallback((id: string) => {
    const g = gen?.accepted.find((c) => c.candidate_id === id) ?? gen?.rejected.find((c) => c.candidate_id === id);
    if (g) return { composition: g.composition, source: "generated" as const };
    const m = space?.measured_train_mixtures.find((x) => x.mix === id);
    if (m) return { composition: m.composition, source: "train" as const };
    const e = history?.experiments.find((x) => x.id === id || x.candidate_id === id);
    if (e) return { composition: e.composition, source: "experiment" as const };
    return null;
  }, [gen, space, history]);

  return (
    <C.Provider value={{
      backend, meta, facilities, fictionalLabel, objective, gen, rank, space, history, genParams, setGenParams,
      busy, error, setError, selected, setSelected, recompute, refreshFacilities, refreshHistory, saveObjective, compositionOf, run,
    }}>
      {children}
    </C.Provider>
  );
}

// Typed client for the DARWIN FastAPI backend (darwin_core/api.py).
// All calls go through the same-origin proxy at /api/darwin/* (see app/api/darwin).

export type Kind = "measured" | "predicted" | "assumed" | "computed" | "simulated";
export type Provenance = { kind: Kind; source: string; [k: string]: unknown };

export const MATERIALS = [
  "cement", "fly_ash", "slag", "water", "admixture_hrwr", "fine_aggregate", "coarse_aggregate",
] as const;
export type Material = (typeof MATERIALS)[number];
export const MATERIAL_COLUMN: Record<Material, string> = {
  cement: "Cement (kg/m3)",
  fly_ash: "Fly Ash (kg/m3)",
  slag: "Slag (kg/m3)",
  water: "Water (kg/m3)",
  admixture_hrwr: "HRWR (kg/m3)",
  fine_aggregate: "Fine Aggregate (kg/m3)",
  coarse_aggregate: "Coarse Aggregates (kg/m3)",
};
export const MATERIAL_LABEL: Record<Material, string> = {
  cement: "Cement", fly_ash: "Fly ash", slag: "Slag", water: "Water",
  admixture_hrwr: "HRWR", fine_aggregate: "Fine agg.", coarse_aggregate: "Coarse agg.",
};
export type Composition = Record<string, number>; // CSV column -> kg/m3 (+ Material Source, Temp (C))

export type GwpFactor = {
  material: string; value: number | null; unit: string; source: string; geography: string;
  version: string; published_or_assumed: "published" | "assumed"; fictional?: boolean;
};
export type FacilityMaterial = {
  material: Material; column: string; available: boolean; inventory_kg: number | null;
  cost_per_kg_usd: number | null; supplier: string; gwp_factor: GwpFactor; bounds_kg_m3: [number, number];
};
export type Facility = {
  id: string; name: string; fictional: boolean; label: string; material_class: string;
  model_material_source: { value: number; provenance: Provenance };
  curing_temp_c: { value: number; provenance: Provenance };
  materials: Record<Material, FacilityMaterial>;
  mixture_bounds: { w_b: [number, number]; binder_kg_m3: [number, number]; max_cement_replacement_fraction: number };
  planned_batch_volume_m3: number;
  lab_budget: { max_experiments: number; cost_per_experiment_usd: number };
};

export type Objective = {
  id: string; target_strength_mpa: number; target_age_days: number; secondary_target_1d_mpa: number | null;
  carbon_budget_kgco2e_m3: number; facility_id: string; acquisition_mode: Mode;
  restricted_materials: string[]; replacement_cap: number | null; planned_volume_m3: number | null;
  provenance: Provenance; set_at?: string;
};
export type Mode = "exploit" | "explore" | "balanced";

export type Reason = { code: string; field: string; limit: unknown; value: unknown; [k: string]: unknown };
export type Feasibility = {
  status: "COMPUTATIONALLY_FEASIBLE" | "REQUIRES_LAB_VALIDATION" | "OUTSIDE_SUPPORTED_DOMAIN";
  rejection_reasons: Reason[]; validation_flags: Reason[];
  derived: { binder_kg_m3: number; w_b: number | null; scm_fraction_of_binder: number | null; replacement_cap: number; planned_volume_m3: number };
  facility_id: string; facility_fictional: boolean; disclaimer: string; provenance: Provenance;
};
export type GeneratedCandidate = {
  candidate_id: string; composition: Composition; feasibility: Feasibility; measurable: false; provenance: Provenance;
};
export type GenerateOut = {
  facility_id: string; n_requested: number; seed: number; n_accepted: number; n_rejected: number;
  status_counts: Record<string, number>; rejection_code_counts: Record<string, number>;
  accepted: GeneratedCandidate[]; rejected: GeneratedCandidate[]; note: string;
};
export type Components = {
  p_meet_target: number; ei_norm: number; uncertainty_norm: number; carbon_score: number;
  over_budget_penalty: number; ei_raw_mpa: number;
};
export type RouteDecision = {
  candidate_id: string; route: string; route_description: string; triggered: { route: string; code: string; detail: unknown }[];
  reviewer: string; label: string; live_call: boolean; numeric_fields_modified: boolean;
};
export type Ranked = {
  candidate_id: string; score: number; rank: number; components: Components; weights: Record<string, number>;
  prediction: { age_days: number; mean_mpa: number; std_mpa: number; kind: string; provenance: Provenance };
  gwp_used: { value: number; basis: string; unit: string }; provenance: Provenance;
  feasibility_status?: string; review_route?: RouteDecision;
};
export type RankOut = {
  mode: Mode; weights: Record<string, number>; objective: Objective; incumbent_mpa: number; n_scored: number;
  ranked: Ranked[]; proposed_experiment_ids: string[]; router: { reviewer: string; model_version: string; note: string };
};

export type SpaceCandidate = {
  candidate_id: string; accepted: boolean; status: string; rejection_codes: string[];
  pred_mean_mpa: number; pred_std_mpa: number; gwp_kgco2e_m3: number; gwp_complete: boolean; pareto?: boolean;
};
export type MeasuredMix = {
  mix: string; composition: Composition; by_age: { age_days: number; strength_mpa: number; strength_std_mpa: number }[];
  strength_at_target_age_mpa: number | null; gwp_kgco2e_m3: number; gwp_complete: boolean; pareto?: boolean;
};
export type SpaceOut = {
  facility_id: string; target_age_days: number; objective: Objective;
  model: { label: string; version: string; role: string };
  candidates: SpaceCandidate[]; measured_train_mixtures: MeasuredMix[]; provenance: Record<string, Provenance>;
};

export type AgePred = { age_days: number; mean_mpa: number; std_mpa: number; predictive_std_mpa: number; pi95_mpa: [number, number]; mean_psi: number };
export type GwpLine = { material: string; qty_kg: number; factor: GwpFactor | null; contribution_kgco2e: number | null; status: string };
export type MaterialsGwp = {
  field: string; value: number; unit: string; complete: boolean; status: string; missing_factors: string[];
  lines: GwpLine[]; boundary: { name: string; statement: string; [k: string]: unknown }; provenance: Provenance; impact_snapshot_id?: string;
};
export type PredictOut = {
  composition: Composition; facility_id: string; strength: AgePred[]; strength_provenance: Provenance; model_role: string;
  gwp: { darwin_materials_gwp: MaterialsGwp; boxcrete_gwp_model_output: { value: number; unit?: string; [k: string]: unknown } | null; note: string };
  cost: { value: number; unit: string; complete: boolean; missing_prices: string[]; lines: { material: string; qty_kg: number; usd_per_kg: number; usd: number }[]; provenance: Provenance };
  feasibility: Feasibility; disclaimer: string;
};

export type Measured = {
  rows: { age_days: number; strength_mpa: number; strength_std_mpa: number; strength_psi: number; strength_std_psi: number; n_cylinders: number }[];
  strength_28d_mpa: number | null; provenance: Provenance;
};
export type Experiment = {
  id: string; candidate_id: string; session_id: string | null; composition: Composition; facility_id: string;
  proposed_at: string; model_snapshot_id: string; status: string; updated_at: string;
  predicted: {
    by_age?: AgePred[]; target_age?: AgePred; model_version?: string; made_before_reveal?: boolean;
    // non-replay proposals store the ranker prediction
    age_days?: number; mean_mpa?: number; std_mpa?: number; provenance?: Provenance;
  };
  acquisition_score: number | null; acquisition_components: (Components & { mode: Mode; weights: Record<string, number>; rank?: number; n_ranked?: number }) | null;
  constraint_eval: Feasibility | null; measured: Measured | null; provenance: Provenance;
};
export type Transition = { id: number; experiment_id: string; from_status: string | null; to_status: string; actor: string; note: string; at: string };
export type ModelSnapshot = { id: string; created_at: string; label: string; version: string; info: Record<string, unknown> };
export type ReplayUpdate = {
  model_snapshot_id: string; old_version: string; new_version: string; fit_mode: string | null; fit_seconds: number;
  added_experiments: string[]; added_rows: number; test_mae_before_mpa: number | null; test_mae_after_mpa: number | null;
  test_pi95_coverage_before: number | null; test_pi95_coverage_after: number | null; n_test_rows: number; metrics_provenance: Provenance;
};
export type HistoryOut = {
  experiments: Experiment[]; transitions: Transition[]; model_snapshots: ModelSnapshot[];
  router_decisions: { id: number; experiment_id: string | null; candidate_id: string; route: string; reviewer: string; at: string; output: RouteDecision }[];
  replay_session: { id: string; steps: { experiment_id: string; mix: string }[]; updates: ReplayUpdate[]; budget: number; facility_id: string } | null;
};
export type Metadata = {
  data_card: Record<string, unknown>;
  split: { split_id: string; material_class: string; seed: number; n_train_mixes: number; n_heldout_mixes: number };
  model: { label: string; backend: string; version: string; n_train_rows: number; fit_seconds: number; snapshot_id: string; fit_mode: string | null };
  training_domain: Record<string, unknown>; router: { reviewer: string; model_version: string };
};
export type ReplayStartOut = {
  session_id: string; split_id: string; facility_id: string; pool_size: number; n_eligible: number; n_excluded: number;
  excluded: { mix: string; p0_reasons: unknown[]; facility_reasons: Reason[] }[]; budget_experiments: number;
  lab_cost_per_experiment_usd: number; model_version: string; note: string;
};
export type ReplayNextOut = {
  experiment: Experiment; acquisition: Ranked; top5: Ranked[]; review_route: RouteDecision;
  gwp: { darwin_materials_gwp: MaterialsGwp; impact_snapshot_id: string }; budget_remaining: number;
};
export type RevealOut = {
  experiment: Experiment;
  comparison: { age_days: number; predicted_mpa: number; measured_mpa: number; error_mpa: number; z: number; within_pi95: boolean; met_target: boolean; provenance: Provenance } | null;
  post_measurement_review: RouteDecision; note: string;
};

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}

export const API_BASE_LABEL = process.env.NEXT_PUBLIC_DARWIN_API || "http://127.0.0.1:8000";

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/darwin${path}`, {
    method,
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const text = await r.text();
  let data: unknown = text;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON error body */ }
  if (!r.ok) {
    const d = (data as { detail?: unknown })?.detail ?? data;
    throw new ApiError(r.status, d);
  }
  return data as T;
}

export const api = {
  metadata: () => call<Metadata>("GET", "/dataset/metadata"),
  facilities: () => call<{ label: string; facilities: Facility[] }>("GET", "/facilities"),
  getObjective: () => call<Objective>("GET", "/objectives"),
  setObjective: (o: Partial<Objective>) => call<Objective>("POST", "/objectives", o),
  predict: (composition: Composition, facility_id?: string, use_replay_model = false) =>
    call<PredictOut>("POST", "/predict", { composition, facility_id, use_replay_model }),
  generate: (n: number, seed: number, facility_id?: string) => call<GenerateOut>("POST", "/candidates/generate", { n, seed, facility_id }),
  rank: (body: { mode?: Mode; top_k?: number; propose_top?: number; candidate_ids?: string[] }) => call<RankOut>("POST", "/experiments/rank", body),
  transition: (id: string, to_status: string, note = "") =>
    call<unknown>("POST", `/experiments/${encodeURIComponent(id)}/transition`, { to_status, actor: "ui-operator", note }),
  factorUpdate: (b: { facility_id: string; material: string; value: number; source: string; geography: string; version: string; published_or_assumed: "published" | "assumed" }) =>
    call<{ old_factor_id: string; new_factor_id: string; factor: GwpFactor; recomputed_impacts: { old_impact_id: string; new_impact_id: string; old_value: number; new_value: number; experiments_relinked: string[] }[] }>("POST", "/factors/update", b),
  replayStart: (b: { facility_id?: string; apply_facility_constraints?: boolean; budget?: number }) => call<ReplayStartOut>("POST", "/replay/start", b),
  replayNext: (b: { mode?: Mode; mix_name?: string; auto_approve?: boolean }) => call<ReplayNextOut>("POST", "/replay/next", b),
  replayReveal: (experiment_id: string) => call<RevealOut>("POST", "/replay/reveal", { experiment_id }),
  replayUpdate: () => call<ReplayUpdate>("POST", "/replay/update", {}),
  history: (session_id?: string) => call<HistoryOut>("GET", `/history${session_id ? `?session_id=${encodeURIComponent(session_id)}` : ""}`),
  baselines: () => call<Record<string, unknown>>("POST", "/baselines/compare", { include_replay_vs_random: true }),
  report: () => call<Record<string, unknown>>("GET", "/report/export"),
  space: (facility_id?: string, use_replay_model = false) => {
    const q = new URLSearchParams();
    if (facility_id) q.set("facility_id", facility_id);
    if (use_replay_model) q.set("use_replay_model", "true");
    const qs = q.toString();
    return call<SpaceOut>("GET", `/ui/space${qs ? `?${qs}` : ""}`);
  },
  overrides: (b: {
    facility_id: string; materials?: Record<string, { available?: boolean; inventory_kg?: number; clear_inventory_limit?: boolean; cost_per_kg_usd?: number }>;
    w_b?: [number, number]; max_cement_replacement_fraction?: number; max_experiments?: number; cost_per_experiment_usd?: number; note?: string;
  }) => call<{ applied: { at: string; changes: { field: string; old: unknown; new: unknown }[] }; overrides: { at: string; changes: { field: string; old: unknown; new: unknown }[] }[]; facility: Facility }>("POST", "/ui/facility_overrides", b),
  facilityReset: (facility_id: string) => call<{ facility: Facility }>("POST", "/ui/facility_reset", { facility_id }),
};

export function errText(e: unknown): string {
  if (e instanceof ApiError) return `HTTP ${e.status}: ${e.message}`;
  return String((e as Error)?.message ?? e);
}

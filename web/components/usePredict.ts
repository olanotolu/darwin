"use client";
import { useEffect, useState } from "react";
import { api, errText, type Composition, type PredictOut } from "@/lib/api";

/** POST /predict for one composition; refetches when inputs change. */
export function usePredict(composition: Composition | null | undefined, facilityId: string | undefined, useReplayModel = false, nonce = 0) {
  const [data, setData] = useState<PredictOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const key = composition ? JSON.stringify(composition) : "";
  useEffect(() => {
    if (!composition) { setData(null); return; }
    let live = true;
    setLoading(true);
    setError(null);
    api.predict(composition, facilityId, useReplayModel)
      .then((r) => { if (live) setData(r); })
      .catch((e) => { if (live) { setData(null); setError(errText(e)); } })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, facilityId, useReplayModel, nonce]);
  return { data, error, loading };
}

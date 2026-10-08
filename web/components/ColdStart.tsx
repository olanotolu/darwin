"use client";
import { useEffect, useState } from "react";
import { API_BASE_LABEL } from "@/lib/api";
import { useDarwin } from "./DarwinProvider";

/** Honest waiting state: elapsed time and the last connection error. No progress bar,
 *  because the backend reports no progress while it fits the startup model. */
export function ColdStart() {
  const { backend } = useDarwin();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const secs = Math.round((now - backend.since) / 1000);
  return (
    <div className="coldstart panel">
      <div className="ph">Waiting for DARWIN API</div>
      <div className="pb stack">
        <div>
          Upstream <span className="mono">{backend.upstream ?? API_BASE_LABEL}</span> is not answering yet.
          Waited <span className="num">{secs}</span> s; connection attempts <span className="num">{backend.attempts}</span> (retrying every 4 s).
        </div>
        <div className="sec">
          If you have just started the backend, this is expected. At startup it fits the BOxCrete strength GP once on the
          concrete training split, which takes about 75–85 s on this VM, and it binds the port only after that fit finishes.
          The backend reports no progress during the fit, so none is shown here.
        </div>
        <div>
          <div className="small muted">Start the backend from the repo root:</div>
          <pre>.venv/bin/uvicorn darwin_core.api:app</pre>
        </div>
        {backend.detail && <div className="small muted mono">last error: {backend.detail}</div>}
      </div>
    </div>
  );
}

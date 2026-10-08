"use client";
import { useState } from "react";
import { DarwinProvider, useDarwin } from "@/components/DarwinProvider";
import { ColdStart } from "@/components/ColdStart";
import { DiscoveryLab } from "@/components/DiscoveryLab";
import { MaterialGenome } from "@/components/MaterialGenome";
import { ExperimentReplay } from "@/components/ExperimentReplay";
import { FactoryReality } from "@/components/FactoryReality";
import { ScientificReport } from "@/components/ScientificReport";
import { Busy } from "@/components/ui";

const TABS = [
  ["lab", "Discovery Lab"],
  ["genome", "Material Genome"],
  ["replay", "Experiment Replay"],
  ["factory", "Factory Reality"],
  ["report", "Scientific Report"],
] as const;
type Tab = (typeof TABS)[number][0];

function Instrument() {
  const { backend, meta, busy, objective } = useDarwin();
  const [tab, setTab] = useState<Tab>("lab");
  return (
    <>
      <header className="topbar">
        <span className="brand">DARWIN</span>
        <nav className="tabs" role="tablist">
          {TABS.map(([id, label]) => (
            <button key={id} role="tab" className="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>
          ))}
        </nav>
        <div className="statusline">
          <Busy label={busy} />
          {meta && <span title={meta.model.label}>model <span className="mono sec">{meta.model.version}</span></span>}
          {meta && <span>split <span className="mono sec">{meta.split.split_id}</span></span>}
          {objective && <span>facility <span className="sec">{objective.facility_id}</span> <span className="small">(FICTIONAL)</span></span>}
          <span>
            API{" "}
            <span className="mono" style={{ color: backend.state === "up" ? "var(--good)" : "var(--warning)" }}>
              {backend.state === "up" ? "● connected" : "○ waiting"}
            </span>
          </span>
        </div>
      </header>
      {backend.state !== "up" ? <ColdStart /> : (
        <main className="page">
          {tab === "lab" && <DiscoveryLab />}
          {tab === "genome" && <MaterialGenome />}
          {tab === "replay" && <ExperimentReplay />}
          {tab === "factory" && <FactoryReality />}
          {tab === "report" && <ScientificReport />}
        </main>
      )}
    </>
  );
}

export default function Page() {
  return <DarwinProvider><Instrument /></DarwinProvider>;
}

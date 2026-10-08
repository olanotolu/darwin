"""P1: SQLite experiment ledger (stdlib sqlite3).

State machine (enforced in Python AND by a database trigger):

    PROPOSED -> APPROVED -> AWAITING_RESULT -> MEASURED
    PROPOSED -> REJECTED,  APPROVED -> REJECTED

MEASURED and REJECTED are terminal. Every transition writes an audit row.
Measurements can only be attached by the AWAITING_RESULT -> MEASURED
transition, and ``approved_measurements_for_training`` only releases
measurements whose experiment has an audited APPROVED transition.

``calc_snapshots`` is append-only (UPDATE/DELETE are blocked by triggers):
it stores immutable environmental-accounting calculations by content id.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

PROPOSED, APPROVED, AWAITING_RESULT, MEASURED, REJECTED = (
    "PROPOSED", "APPROVED", "AWAITING_RESULT", "MEASURED", "REJECTED")
STATUSES = (PROPOSED, APPROVED, AWAITING_RESULT, MEASURED, REJECTED)
TRANSITIONS = {
    PROPOSED: {APPROVED, REJECTED},
    APPROVED: {AWAITING_RESULT, REJECTED},
    AWAITING_RESULT: {MEASURED},
    MEASURED: set(),
    REJECTED: set(),
}


class InvalidTransition(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _dump(x) -> str:
    return json.dumps(x, sort_keys=True, default=_json_default)


def _json_default(o):
    import numpy as np
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serialisable: {type(o)}")


def content_id(prefix: str, payload) -> str:
    return f"{prefix}-{hashlib.sha256(_dump(payload).encode()).hexdigest()[:16]}"


_VALID_PAIRS = " OR ".join(f"(OLD.status='{a}' AND NEW.status='{b}')"
                           for a, bs in TRANSITIONS.items() for b in sorted(bs))

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    session_id TEXT,
    composition_json TEXT NOT NULL,
    facility_id TEXT,
    proposed_at TEXT NOT NULL,
    model_snapshot_id TEXT,
    predicted_json TEXT NOT NULL,
    acquisition_score REAL,
    acquisition_components_json TEXT,
    constraint_eval_json TEXT,
    status TEXT NOT NULL CHECK (status IN ({",".join(f"'{s}'" for s in STATUSES)})),
    measured_json TEXT,
    provenance_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL REFERENCES experiments(id),
    from_status TEXT, to_status TEXT NOT NULL, actor TEXT NOT NULL, note TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_snapshots (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, label TEXT, version TEXT, info_json TEXT
);
CREATE TABLE IF NOT EXISTS calc_snapshots (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL,
    inputs_json TEXT NOT NULL, outputs_json TEXT NOT NULL, parents_json TEXT NOT NULL,
    supersedes TEXT
);
CREATE TABLE IF NOT EXISTS experiment_impacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL, calc_snapshot_id TEXT NOT NULL,
    reason TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS router_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT, candidate_id TEXT,
    route TEXT NOT NULL, reviewer TEXT NOT NULL, adapter_model_version TEXT,
    output_json TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS experiments_status_guard
BEFORE UPDATE OF status ON experiments
WHEN OLD.status <> NEW.status AND NOT ({_VALID_PAIRS})
BEGIN SELECT RAISE(ABORT, 'invalid experiment status transition'); END;
CREATE TRIGGER IF NOT EXISTS experiments_measured_guard
BEFORE UPDATE OF measured_json ON experiments
WHEN NOT (OLD.status = 'AWAITING_RESULT' AND NEW.status = 'MEASURED')
BEGIN SELECT RAISE(ABORT, 'measured_json may only be set on AWAITING_RESULT -> MEASURED'); END;
CREATE TRIGGER IF NOT EXISTS calc_snapshots_no_update BEFORE UPDATE ON calc_snapshots
BEGIN SELECT RAISE(ABORT, 'calc_snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS calc_snapshots_no_delete BEFORE DELETE ON calc_snapshots
BEGIN SELECT RAISE(ABORT, 'calc_snapshots are immutable'); END;
"""

_JSON_COLS = {"composition_json", "predicted_json", "acquisition_components_json", "constraint_eval_json",
              "measured_json", "provenance_json", "info_json", "inputs_json", "outputs_json",
              "parents_json", "output_json"}


def _row(r: sqlite3.Row) -> dict:
    d = {}
    for k in r.keys():
        v = r[k]
        if k in _JSON_COLS:
            d[k[:-5]] = None if v is None else json.loads(v)
        else:
            d[k] = v
    return d


class Ledger:
    def __init__(self, path: str = "darwin.db"):
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    # --- experiments ------------------------------------------------------
    def propose(self, *, candidate_id: str, composition: dict, facility_id: str | None,
                predicted: dict, acquisition_score: float | None, acquisition_components: dict | None,
                constraint_eval: dict | None, provenance: dict, model_snapshot_id: str | None = None,
                session_id: str | None = None, actor: str = "darwin") -> str:
        if not predicted:
            raise ValueError("an experiment must carry its prediction at proposal time")
        eid = f"EXP-{uuid.uuid4().hex[:12]}"
        now = _now()
        with self._lock:
            self.conn.execute("BEGIN")
            self.conn.execute(
                "INSERT INTO experiments (id,candidate_id,session_id,composition_json,facility_id,proposed_at,"
                "model_snapshot_id,predicted_json,acquisition_score,acquisition_components_json,"
                "constraint_eval_json,status,measured_json,provenance_json,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?)",
                (eid, candidate_id, session_id, _dump(composition), facility_id, now, model_snapshot_id,
                 _dump(predicted), acquisition_score, _dump(acquisition_components), _dump(constraint_eval),
                 PROPOSED, _dump(provenance), now))
            self.conn.execute("INSERT INTO transitions (experiment_id,from_status,to_status,actor,note,at) "
                              "VALUES (?,?,?,?,?,?)", (eid, None, PROPOSED, actor, "proposed", now))
            self.conn.execute("COMMIT")
        return eid

    def get(self, eid: str) -> dict:
        r = self.conn.execute("SELECT * FROM experiments WHERE id=?", (eid,)).fetchone()
        if r is None:
            raise KeyError(eid)
        return _row(r)

    def transition(self, eid: str, to_status: str, *, actor: str, note: str = "",
                   measured: dict | None = None) -> dict:
        if to_status not in STATUSES:
            raise InvalidTransition(f"unknown status {to_status!r}")
        with self._lock:
            cur = self.get(eid)["status"]
            if to_status not in TRANSITIONS[cur]:
                raise InvalidTransition(f"{eid}: {cur} -> {to_status} is not allowed "
                                        f"(allowed: {sorted(TRANSITIONS[cur]) or 'none, terminal'})")
            if (to_status == MEASURED) != (measured is not None):
                raise InvalidTransition("a measurement is required for, and only for, -> MEASURED")
            now = _now()
            self.conn.execute("BEGIN")
            if measured is not None:
                self.conn.execute("UPDATE experiments SET status=?, measured_json=?, updated_at=? WHERE id=?",
                                  (to_status, _dump(measured), now, eid))
            else:
                self.conn.execute("UPDATE experiments SET status=?, updated_at=? WHERE id=?", (to_status, now, eid))
            self.conn.execute("INSERT INTO transitions (experiment_id,from_status,to_status,actor,note,at) "
                              "VALUES (?,?,?,?,?,?)", (eid, cur, to_status, actor, note, now))
            self.conn.execute("COMMIT")
        return self.get(eid)

    def record_measurement(self, eid: str, measured: dict, *, actor: str) -> dict:
        if (measured.get("provenance") or {}).get("kind") != "measured":
            raise ValueError("measurement must carry provenance kind 'measured'")
        return self.transition(eid, MEASURED, actor=actor, note="measurement recorded", measured=measured)

    def experiments(self, session_id: str | None = None) -> list[dict]:
        q, a = "SELECT * FROM experiments", ()
        if session_id:
            q, a = q + " WHERE session_id=?", (session_id,)
        return [_row(r) for r in self.conn.execute(q + " ORDER BY proposed_at, id", a)]

    def transitions_for(self, eid: str | None = None) -> list[dict]:
        q, a = "SELECT * FROM transitions", ()
        if eid:
            q, a = q + " WHERE experiment_id=?", (eid,)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY id", a)]

    def approved_measurements_for_training(self, session_id: str | None = None) -> list[dict]:
        """The ONLY route from the ledger into training data: MEASURED
        experiments that passed an audited APPROVED transition."""
        q = ("SELECT e.* FROM experiments e WHERE e.status='MEASURED' AND e.measured_json IS NOT NULL "
             "AND EXISTS (SELECT 1 FROM transitions t WHERE t.experiment_id=e.id AND t.to_status='APPROVED')")
        a: tuple = ()
        if session_id:
            q, a = q + " AND e.session_id=?", (session_id,)
        return [_row(r) for r in self.conn.execute(q + " ORDER BY e.updated_at, e.id", a)]

    # --- snapshots / decisions -------------------------------------------
    def add_model_snapshot(self, label: str, version: str, info: dict) -> str:
        sid = content_id("MODEL", {"version": version, "label": label, "info": info})
        self.conn.execute("INSERT OR IGNORE INTO model_snapshots VALUES (?,?,?,?,?)",
                          (sid, _now(), label, version, _dump(info)))
        return sid

    def model_snapshots(self) -> list[dict]:
        return [_row(r) for r in self.conn.execute("SELECT * FROM model_snapshots ORDER BY created_at")]

    def add_calc_snapshot(self, kind: str, inputs: dict, outputs: dict, parents: list[str],
                          supersedes: str | None = None) -> str:
        sid = content_id("CALC", {"kind": kind, "inputs": inputs, "outputs": outputs, "parents": parents,
                                  "supersedes": supersedes})
        self.conn.execute("INSERT OR IGNORE INTO calc_snapshots VALUES (?,?,?,?,?,?,?)",
                          (sid, kind, _now(), _dump(inputs), _dump(outputs), _dump(parents), supersedes))
        return sid

    def calc_snapshot(self, sid: str) -> dict:
        r = self.conn.execute("SELECT * FROM calc_snapshots WHERE id=?", (sid,)).fetchone()
        if r is None:
            raise KeyError(sid)
        return _row(r)

    def calc_snapshots(self) -> list[dict]:
        return [_row(r) for r in self.conn.execute("SELECT * FROM calc_snapshots ORDER BY created_at, id")]

    def link_impact(self, eid: str, calc_id: str, reason: str) -> None:
        self.conn.execute("INSERT INTO experiment_impacts (experiment_id,calc_snapshot_id,reason,at) "
                          "VALUES (?,?,?,?)", (eid, calc_id, reason, _now()))

    def impacts_for(self, eid: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM experiment_impacts WHERE experiment_id=? ORDER BY id", (eid,))]

    def add_router_decision(self, decision: dict, experiment_id: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO router_decisions (experiment_id,candidate_id,route,reviewer,adapter_model_version,"
            "output_json,at) VALUES (?,?,?,?,?,?,?)",
            (experiment_id, decision.get("candidate_id"), decision["route"], decision["reviewer"],
             decision.get("adapter_model_version"), _dump(decision), _now()))
        return int(cur.lastrowid)

    def router_decisions(self) -> list[dict]:
        return [_row(r) for r in self.conn.execute("SELECT * FROM router_decisions ORDER BY id")]

"""Persistent, costed information selection in an isolated synthetic experiment.

Only feedback for the selected source updates its estimate. Quotes and the free
forecast are committed before feedback. This module has no payment or RPC tools.
"""

import hashlib
import json
import math
import sqlite3


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def probability(value):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError("Invalid forecast probability")
    return value


class Acquisition:
    def __init__(self, path, scope, *, contextual=True, adaptive=True):
        if (
            not isinstance(scope, str)
            or not scope.startswith("synthetic:")
            or type(contextual) is not bool
            or type(adaptive) is not bool
        ):
            raise ValueError(
                "Acquisition research cannot receive live financial credit"
            )
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY, terms TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trials(id TEXT PRIMARY KEY, terms TEXT NOT NULL, delivery TEXT, outcome TEXT);
            CREATE TABLE IF NOT EXISTS estimates(context TEXT, provider TEXT, n INTEGER, gain REAL,
              PRIMARY KEY(context,provider));
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, payload TEXT NOT NULL,
              previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
            """
        )
        self.terms = {
            "scope": scope,
            "contextual": contextual,
            "adaptive": adaptive,
            "minimum_update_rate": 0.08,
            "exploration_floor": 0.02,
            "units": "virtual normalized prediction utility; no USD or token expense",
        }
        row = self.db.execute("SELECT terms FROM config WHERE id=1").fetchone()
        if row and row[0] != canonical(self.terms):
            self.db.close()
            raise ValueError("Research scope or learning terms differ from checkpoint")
        if not row:
            self.db.execute("INSERT INTO config VALUES(1,?)", (canonical(self.terms),))
        try:
            self.verify()
        except Exception:
            self.db.close()
            raise

    def event(self, value):
        row = self.db.execute(
            "SELECT hash FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        parent = row[0] if row else "0" * 64
        raw = canonical(value)
        digest = hashlib.sha256((parent + raw).encode()).hexdigest()
        self.db.execute(
            "INSERT INTO events(payload,previous_hash,hash) VALUES(?,?,?)",
            (raw, parent, digest),
        )
        return digest

    def verify(self):
        parent, n = "0" * 64, 0
        decisions, deliveries, outcomes, expected = {}, {}, {}, {}
        for seq, raw, previous, digest in self.db.execute(
            "SELECT * FROM events ORDER BY seq"
        ):
            if (
                seq != n + 1
                or previous != parent
                or hashlib.sha256((parent + raw).encode()).hexdigest() != digest
            ):
                raise ValueError("Acquisition journal integrity failed")
            value = json.loads(raw)
            if value["state"] == "decision":
                if value["id"] in decisions:
                    raise ValueError("Duplicate acquisition decision")
                decisions[value["id"]] = value["terms"]
            elif value["state"] == "delivery":
                if value["id"] not in decisions or value["id"] in deliveries:
                    raise ValueError("Delivery lacks a unique prior decision")
                terms, delivery = decisions[value["id"]], value["delivery"]
                self.validate_delivery(terms, delivery)
                deliveries[value["id"]] = delivery
            elif value["state"] == "outcome":
                if value["id"] not in deliveries or value["id"] in outcomes:
                    raise ValueError(
                        "Acquisition feedback lacks a unique prior decision"
                    )
                outcomes[value["id"]] = value["outcome"]
                terms, outcome = decisions[value["id"]], value["outcome"]
                if outcome != self.outcome(
                    terms,
                    deliveries[value["id"]],
                    outcome["label"],
                    outcome["at"],
                    outcome["learn"],
                ):
                    raise ValueError("Recorded acquisition score is inconsistent")
                if outcome["learn"] and terms["selection"] != "free":
                    key = (terms["learning_context"], terms["selection"])
                    count, mean = expected.get(key, (0, 0.0))
                    rate = (
                        max(0.08, 1 / (count + 1))
                        if self.terms["adaptive"]
                        else 1 / (count + 1)
                    )
                    expected[key] = (
                        count + 1,
                        mean + rate * (outcome["gross_gain"] - mean),
                    )
            else:
                raise ValueError("Unknown acquisition journal record")
            parent, n = digest, n + 1
        records = {
            ident: (
                json.loads(raw),
                json.loads(delivery) if delivery else None,
                json.loads(outcome) if outcome else None,
            )
            for ident, raw, delivery, outcome in self.db.execute("SELECT * FROM trials")
        }
        if records != {
            ident: (terms, deliveries.get(ident), outcomes.get(ident))
            for ident, terms in decisions.items()
        }:
            raise ValueError("Acquisition trial rows differ from their journal")
        estimates = {
            (context, provider): (count, gain)
            for context, provider, count, gain in self.db.execute(
                "SELECT * FROM estimates"
            )
        }
        if estimates != expected:
            raise ValueError(
                "Acquisition estimates differ from recorded selected feedback"
            )
        return {
            "events": n,
            "head": parent,
            "scope": "Local consistency, not independent attestation",
        }

    def choose(self, ident, context, free_probability, quotes, at, *, maximum_cost=0.1):
        probability(free_probability)
        if (
            not ident
            or not isinstance(context, str)
            or not context
            or type(at) is not int
            or at < 0
        ):
            raise ValueError("Invalid decision identity or experiment clock")
        if (
            type(maximum_cost) not in (int, float)
            or not math.isfinite(maximum_cost)
            or not 0 <= maximum_cost <= 1
        ):
            raise ValueError("Invalid virtual job ceiling")
        if any(
            not isinstance(k, str)
            or not k
            or k == "free"
            or type(v) not in (int, float)
            or not math.isfinite(v)
            or not 0 <= v <= 1
            for k, v in quotes.items()
        ):
            raise ValueError("Invalid virtual provider quotes")
        inputs = {
            "context": context,
            "free_probability": free_probability,
            "quotes": quotes,
            "at": at,
            "maximum_cost": maximum_cost,
        }
        self.db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.db.execute(
                "SELECT terms FROM trials WHERE id=?", (ident,)
            ).fetchone()
            if prior:
                stored = json.loads(prior[0])
                if stored["inputs"] != inputs:
                    raise ValueError("Decision identity cannot change terms")
                self.db.execute("COMMIT")
                return stored
            key = context if self.terms["contextual"] else "all"
            estimates = {
                p: {"observations": n, "gross_gain": gain}
                for p, n, gain in self.db.execute(
                    "SELECT provider,n,gain FROM estimates WHERE context=?", (key,)
                )
            }
            candidates = [p for p in sorted(quotes) if quotes[p] <= maximum_cost]
            utilities = {
                "free": 0.0,
                **{
                    p: estimates.get(p, {}).get("gross_gain", 0) - quotes[p]
                    for p in candidates
                },
            }
            selected = max(utilities, key=lambda p: (utilities[p], p == "free", p))
            observations = sum(v["observations"] for v in estimates.values())
            epsilon = max(0.02, 0.2 / math.sqrt(1 + observations / 10))
            digest = hashlib.sha256(
                (self.terms["scope"] + ":" + ident).encode()
            ).digest()
            explore = int.from_bytes(digest[:8], "big") / 2**64 < epsilon
            if explore:
                options = ["free", *candidates]
                selected = options[int.from_bytes(digest[8:16], "big") % len(options)]
            terms = {
                "inputs": inputs,
                "learning_context": key,
                "selection": selected,
                "cost": 0 if selected == "free" else quotes[selected],
                "estimated_net_gain": utilities[selected],
                "estimates_before_feedback": estimates,
                "exploration": explore,
                "exploration_probability": epsilon,
                "reason": "Bounded virtual exploration"
                if explore
                else "Positive estimated gain after quoted cost"
                if selected != "free"
                else "No available source has positive estimated gain after cost",
            }
            self.db.execute(
                "INSERT INTO trials VALUES(?,?,NULL,NULL)", (ident, canonical(terms))
            )
            self.event({"state": "decision", "id": ident, "terms": terms})
            self.db.execute("COMMIT")
            return terms
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    @staticmethod
    def validate_delivery(terms, delivery):
        value, at = delivery["probability"], delivery["at"]
        if type(at) is not int or at <= terms["inputs"]["at"]:
            raise ValueError("Delivery must follow the committed decision")
        if value is not None:
            probability(value)
        if (
            terms["selection"] == "free"
            and value != terms["inputs"]["free_probability"]
        ):
            raise ValueError("Free branch must use its committed forecast")

    def deliver(self, ident, value, at):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT terms,delivery FROM trials WHERE id=?", (ident,)
            ).fetchone()
            if not row:
                raise ValueError("Delivery has no prior acquisition decision")
            delivery = {"probability": value, "at": at}
            self.validate_delivery(json.loads(row[0]), delivery)
            if row[1]:
                if json.loads(row[1]) != delivery:
                    raise ValueError("Committed delivery cannot change")
                self.db.execute("COMMIT")
                return delivery
            self.db.execute(
                "UPDATE trials SET delivery=? WHERE id=?", (canonical(delivery), ident)
            )
            self.event({"state": "delivery", "id": ident, "delivery": delivery})
            self.db.execute("COMMIT")
            return delivery
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    @staticmethod
    def outcome(terms, delivery, label, at, learn):
        if (
            type(label) is not int
            or label not in (0, 1)
            or type(at) is not int
            or type(learn) is not bool
        ):
            raise ValueError("Invalid delayed outcome")
        if at <= delivery["at"]:
            raise ValueError("Outcome must follow the committed delivery")
        baseline, value = terms["inputs"]["free_probability"], delivery["probability"]
        prediction = baseline if value is None else value
        baseline_loss, paid_loss = (baseline - label) ** 2, (prediction - label) ** 2
        gain = baseline_loss - paid_loss
        return {
            "label": label,
            "at": at,
            "learn": learn,
            "baseline_brier": baseline_loss,
            "selected_brier": paid_loss,
            "gross_gain": gain,
            "net_gain": gain - terms["cost"],
            "cost": terms["cost"],
            "delivered": value is not None,
        }

    def settle(self, ident, label, at, *, learn=True):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT terms,delivery,outcome FROM trials WHERE id=?", (ident,)
            ).fetchone()
            if not row or not row[1]:
                raise ValueError("Outcome has no prior committed delivery")
            terms = json.loads(row[0])
            outcome = self.outcome(terms, json.loads(row[1]), label, at, learn)
            if row[2]:
                if json.loads(row[2]) != outcome:
                    raise ValueError("Settled acquisition outcome cannot change")
                self.db.execute("COMMIT")
                return outcome
            if learn and terms["selection"] != "free":
                context, provider = terms["learning_context"], terms["selection"]
                prior = self.db.execute(
                    "SELECT n,gain FROM estimates WHERE context=? AND provider=?",
                    (context, provider),
                ).fetchone()
                count, mean = prior if prior else (0, 0.0)
                rate = (
                    max(0.08, 1 / (count + 1))
                    if self.terms["adaptive"]
                    else 1 / (count + 1)
                )
                mean += rate * (outcome["gross_gain"] - mean)
                self.db.execute(
                    "INSERT INTO estimates VALUES(?,?,?,?) ON CONFLICT(context,provider) DO UPDATE SET n=excluded.n,gain=excluded.gain",
                    (context, provider, count + 1, mean),
                )
            self.db.execute(
                "UPDATE trials SET outcome=? WHERE id=?", (canonical(outcome), ident)
            )
            self.event({"state": "outcome", "id": ident, "outcome": outcome})
            self.db.execute("COMMIT")
            return outcome
        except Exception:
            self.db.execute("ROLLBACK")
            raise

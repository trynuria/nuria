"""Isolated exact-payment worker; configuration is disabled by default.

There is no HTTP listener or general transaction-signing endpoint. A fixed job
catalog turns fresh recorded cognitive decisions into bounded purchase requests.
"""

import base64
import fcntl
import hashlib
import json
import math
import os
import signal
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from solders.keypair import Keypair
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from cognition.fee_observer import rpc
from commerce.config import SOURCE, Policy
from commerce.evidence import export_pages
from commerce.fees import observe as fee_observation
from commerce.funding import Collector
from commerce.information import InformationChoice
from commerce.ledger import Ledger, canonical
from commerce.managed import ManagedSigner, invoke
from commerce.recovery import history_anchor, inspect_history
from commerce.solana import settlement, usdc_balance
from commerce.swaps import Converter
from commerce.sweep import Sweeper
from commerce.transport import request
from commerce.x402 import authorize, decode_header, delivery, quote
from publish import publish


def select_job(policy, cognition, decision, now, ledger):
    payload = {
        k: v for k, v in decision.items() if k not in ("seq", "hash", "previous_hash")
    }
    parent = decision.get("previous_hash", "")
    if hashlib.sha256(
        (parent + canonical(payload)).encode()
    ).hexdigest() != decision.get("hash"):
        raise ValueError("Cognitive decision integrity failed")
    stamp = datetime.fromisoformat(decision["utc"]).timestamp()
    if cognition.get("phase") != "running" or not -2 <= now - stamp <= 30:
        return None
    chain = cognition.get("learning", {}).get("sources", {}).get(SOURCE)
    if not chain or not chain.get("prediction"):
        return None
    action = decision["decision"]["action"]
    if ledger.db.execute(
        "SELECT 1 FROM jobs WHERE decision_hash=?", (decision["hash"],)
    ).fetchone():
        return None
    last = ledger.db.execute("SELECT max(created) FROM jobs").fetchone()[0]
    if last is not None and now - last < policy.cooldown_seconds:
        return None
    uncertainty = cognition.get("workspace", {}).get("uncertainty", 0)
    surprise = cognition.get("workspace", {}).get("last_surprise", 0)
    scores = []
    for provider in policy.providers:
        if provider.action != action:
            continue
        learned = (
            ledger.db.execute(
                "SELECT avg(reward) FROM jobs WHERE provider=? AND status='evaluated'",
                (provider.id,),
            ).fetchone()[0]
            or 0
        )
        value = (
            0.5 * uncertainty
            + 0.25 * surprise
            + 0.25 * learned
            - provider.maximum_micro_usdc / max(1, policy.per_job_micro_usdc) * 0.1
        )
        if math.isfinite(value) and value > 0.1:
            scores.append((value, provider.id, provider))
    if not scores:
        return None
    value, _, provider = max(scores, key=lambda row: (row[0], row[1]))
    return {
        "provider": provider,
        "expected_value": value,
        "decision_hash": decision["hash"],
        "action": action,
        "cursor": cognition["source_cursor"],
        "baseline_p_buy": chain["prediction"]["probability"],
    }


def load_key(path, expected):
    metadata = path.stat()
    if (
        stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.getuid()
        or path.is_symlink()
    ):
        raise ValueError(
            "Signing file must be private and owned by the financial service"
        )
    values = json.loads(path.read_text())
    if (
        not isinstance(values, list)
        or len(values) != 64
        or any(type(v) is not int or not 0 <= v <= 255 for v in values)
    ):
        raise ValueError("Invalid dedicated key material")
    key = Keypair.from_bytes(bytes(values))
    if str(key.pubkey()) != expected:
        raise ValueError("Signing public address differs from policy")
    return key


class Executor:
    def __init__(self, ledger, fetch=rpc, http=request, signer=authorize):
        self.ledger, self.fetch, self.http, self.signer = ledger, fetch, http, signer

    def purchase(self, policy, selected, keypair, rpc_url, now):
        provider = selected["provider"]
        status, headers, _ = self.http(provider.endpoint)
        if status != 402:
            raise ValueError("Provider did not return a payable invoice")
        accepted = quote(headers.get("payment-required"), provider)
        job = {
            "id": hashlib.sha256(
                (selected["decision_hash"] + provider.id).encode()
            ).hexdigest(),
            "provider": provider.id,
            "decision_hash": selected["decision_hash"],
            "action": selected["action"],
            "schema": provider.schema,
            "amount": int(accepted["amount"]),
            "accepted": accepted,
            "cursor": selected["cursor"],
            "baseline_p_buy": selected["baseline_p_buy"],
            "expected_value": selected["expected_value"],
        }
        balance = usdc_balance(
            policy.spending_wallet, self.fetch, self.ledger.minimum_balance_slot()
        )
        anchor = history_anchor(policy.spending_wallet, self.fetch, balance["slot"])
        self.ledger.reserve(job, policy, balance, now)
        try:
            authorization = self.signer(keypair, accepted, rpc_url)
            authorization["history_anchor"] = anchor
            encoded = decode_header(authorization["header"])["payload"]["transaction"]
            payment = VersionedTransaction.from_bytes(
                base64.b64decode(encoded, validate=True)
            )
            unsigned = VersionedTransaction.populate(
                payment.message,
                [Signature.default()] * payment.message.header.num_required_signatures,
            )
            simulated = self.fetch(
                "simulateTransaction",
                [
                    base64.b64encode(bytes(unsigned)).decode(),
                    {
                        "encoding": "base64",
                        "sigVerify": False,
                        "commitment": "finalized",
                    },
                ],
            )
            if simulated["value"].get("err") is not None:
                raise ValueError("Exact payment simulation failed")
            if time.time() - now > min(60, accepted["maxTimeoutSeconds"]):
                raise ValueError("Invoice expired before authorization disclosure")
        except Exception:
            self.ledger.transition(
                job["id"],
                "failed",
                {"reason": "Authorization or simulation failed before disclosure"},
                time.time(),
            )
            raise
        # Persist the authorization before it is sent. Crash recovery never repays.
        self.ledger.db.execute(
            "INSERT INTO authorizations(job_id,payload) VALUES(?,?)",
            (job["id"], canonical(authorization)),
        )
        self.ledger.transition(
            job["id"],
            "authorized",
            {
                "client_signature": authorization["client_signature"],
                "transaction_sha256": authorization["transaction_sha256"],
            },
            time.time(),
        )
        try:
            status, headers, raw = self.http(
                provider.endpoint, {"PAYMENT-SIGNATURE": authorization["header"]}
            )
            received = time.time()
            receipt = decode_header(headers.get("payment-response"))
            if (
                status != 200
                or receipt.get("success") is not True
                or receipt.get("network") != accepted["network"]
            ):
                raise ValueError("Provider response does not establish settlement")
            # Retain settlement evidence even when the merchant delivers bad data.
            try:
                delivered = delivery(raw, policy.mint, received, provider.schema)
            except (ValueError, TypeError):
                delivered = None
            response = {"receipt": receipt, "delivery": delivered}
            self.ledger.db.execute(
                "UPDATE authorizations SET response=? WHERE job_id=?",
                (canonical(response), job["id"]),
            )
            self.reconcile_one(job["id"], policy)
        except Exception:
            state = self.ledger.db.execute(
                "SELECT status FROM jobs WHERE id=?", (job["id"],)
            ).fetchone()[0]
            if state == "authorized":
                self.ledger.transition(
                    job["id"],
                    "uncertain",
                    {
                        "reason": "Payment disclosed; settlement or delivery remains unverified. No automatic repayment."
                    },
                    time.time(),
                )
            raise
        return job["id"]

    def reconcile_one(self, ident, policy):
        row = self.ledger.db.execute(
            "SELECT j.status,j.terms,a.payload,a.response FROM jobs j JOIN authorizations a ON a.job_id=j.id WHERE j.id=?",
            (ident,),
        ).fetchone()
        state, terms, authorization, response = row
        if state not in ("authorized", "uncertain", "settled"):
            return
        terms, authorization = json.loads(terms), json.loads(authorization)
        if state != "settled" and not response and authorization.get("history_anchor"):
            recovery = inspect_history(
                authorization,
                terms["accepted"],
                policy.spending_wallet,
                authorization["history_anchor"],
                self.fetch,
            )
            if recovery and recovery["state"] == "expired_unsettled":
                self.ledger.close_expired(ident, recovery, time.time())
                return
            if recovery and recovery["state"] == "included" and not recovery["failed"]:
                if not response:
                    response = canonical(
                        {
                            "receipt": {"transaction": recovery["transaction"]},
                            "delivery": None,
                        }
                    )
                    self.ledger.db.execute(
                        "UPDATE authorizations SET response=? WHERE job_id=?",
                        (response, ident),
                    )
        if not response:
            return
        response = json.loads(response)
        if state != "settled":
            evidence = settlement(
                response["receipt"]["transaction"],
                authorization["client_signature"],
                policy.spending_wallet,
                terms["accepted"]["payTo"],
                terms["amount"],
                self.fetch,
            )
            if not evidence:
                return
            self.ledger.db.execute(
                "UPDATE jobs SET settlement=? WHERE id=?", (canonical(evidence), ident)
            )
            self.ledger.transition(ident, "settled", evidence, time.time())
        if response["delivery"]:
            self.ledger.db.execute(
                "UPDATE jobs SET delivery=? WHERE id=?",
                (canonical(response["delivery"]), ident),
            )
            self.ledger.transition(
                ident,
                "delivered",
                {
                    "result_sha256": response["delivery"]["sha256"],
                    "schema": response["delivery"]["schema"],
                },
                time.time(),
            )

    def reconcile(self, policy):
        for (ident,) in self.ledger.db.execute(
            "SELECT id FROM jobs WHERE status IN ('authorized','uncertain','settled') LIMIT 20"
        ).fetchall():
            self.reconcile_one(ident, policy)

    def evaluate(self, effects, now):
        for ident, terms, raw in self.ledger.db.execute(
            "SELECT id,terms,delivery FROM jobs WHERE status='delivered' LIMIT 20"
        ).fetchall():
            job, delivered = json.loads(terms), json.loads(raw)
            if delivered["schema"] != "nuria.forecast.v1":
                continue  # Market context has no implemented neural reward attribution.
            # A missing recent-cache window cannot establish the next outcome.
            if effects and min(e["event_order"] for e in effects) > job["cursor"] + 1:
                continue
            candidates = [
                effect
                for effect in effects
                if effect.get("source") == SOURCE
                and effect.get("event_order", 0) > job["cursor"]
                and effect.get("source_payload", {}).get("mint") == delivered["mint"]
            ]
            if not candidates:
                continue
            effect, observed = None, None
            for candidate in sorted(candidates, key=lambda e: e["event_order"]):
                tx = self.fetch(
                    "getTransaction",
                    [
                        candidate["signature"],
                        {
                            "encoding": "json",
                            "commitment": "finalized",
                            "maxSupportedTransactionVersion": 0,
                        },
                    ],
                )
                if not tx or type(tx.get("blockTime")) is not int:
                    break
                if tx["meta"].get("err") is not None:
                    raise ValueError("Outcome transaction evidence is inconsistent")
                if tx["blockTime"] > delivered["delivered_at"]:
                    effect, observed = candidate, tx["blockTime"]
                    break
            if effect is None:
                continue
            if observed > delivered["expires_at"]:
                reward, result = -min(1, job["amount"] / 1_000_000), {"expired": True}
            else:
                outcome = float(effect["side"] == "buy")
                paid_error = (delivered["p_buy"] - outcome) ** 2
                baseline_error = (job["baseline_p_buy"] - outcome) ** 2
                reward = max(
                    -1,
                    min(
                        1,
                        baseline_error - paid_error - job["amount"] / 1_000_000 * 0.01,
                    ),
                )
                result = {
                    "input_id": effect["input_id"],
                    "paid_brier": paid_error,
                    "local_forecast_brier": baseline_error,
                    "source": SOURCE,
                    "scope": "First finalized input with block time after delivery; operational comparison, not a controlled benchmark",
                }
            self.ledger.db.execute(
                "UPDATE jobs SET reward=? WHERE id=?", (reward, ident)
            )
            self.ledger.transition(
                ident, "evaluated", {"reward": reward, **result}, now
            )

    def feedback(self):
        values = []
        for seq, raw in self.ledger.db.execute(
            "SELECT seq,payload FROM events ORDER BY seq DESC LIMIT 1000"
        ):
            event = json.loads(raw)
            if event.get("state") != "evaluated":
                continue
            action, amount, delivered = self.ledger.db.execute(
                "SELECT action,amount,delivery FROM jobs WHERE id=?", (event["job_id"],)
            ).fetchone()
            values.append(
                {
                    "seq": seq,
                    "job_id": event["job_id"],
                    "action": action,
                    "reward": event["reward"],
                    "amount_micro_usdc": amount,
                    "result_sha256": json.loads(delivered)["sha256"],
                }
            )
            if len(values) == 100:
                break
        return sorted(values, key=lambda item: item["seq"])


def main():
    private, public = (
        Path(os.environ["NURIA_COMMERCE_DATA"]),
        Path(os.environ["NURIA_COMMERCE_PUBLIC"]),
    )
    private.mkdir(parents=True, exist_ok=True)
    lock = (private / "writer.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    ledger = Ledger(private / "commerce.sqlite3")
    executor = Executor(ledger)
    collector = Collector(ledger, rpc)
    information = InformationChoice(ledger)
    converter = Converter(ledger, rpc)
    sweeper = Sweeper(ledger, rpc)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        result = {
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            "phase": "guarded",
            "financial_execution": False,
            "policy": None,
            "fee_path": None,
            "usdc_balance": None,
            "missing": [],
            "rails": {
                "pump_claim": "guarded_standard_collection_disabled",
                "x402": "exact_solana_usdc",
                "creator_to_operating": "awaiting_verified_direct_claim_recipient",
                "sol_to_usdc": "constrained_jupiter_v2_adapter_disabled",
                "custody": "privy_parsed_transactions",
                "batch_settlement": "requires_verified_merchant_and_channel",
            },
        }
        try:
            policy = Policy.load(
                json.loads(Path(os.environ["NURIA_COMMERCE_CONFIG"]).read_text())
            )
            result["policy"], result["missing"] = policy.public(), policy.missing()
            result["funding"] = {
                "mode": policy.funding_mode,
                "creator_wallet_control": policy.funding_mode
                in ("direct_creator", "managed_creator"),
                "destination": policy.spending_wallet,
                "scope": (
                    "The launch wallet remains owner-controlled. Funding requires an owner-signed transfer; a protocol claim does not grant access to that wallet."
                    if policy.funding_mode == "owner_transfer"
                    else "Standard protocol claims pay the verified creator beneficiary directly into the operating wallet. No intermediate reserve or forwarding transaction is required."
                    if policy.funding_mode == "direct_creator"
                    else "A separate restricted creator-wallet delegate may fund only the configured operating destination within native SOL ceilings."
                ),
            }
            key_path = Path(os.environ.get("NURIA_COMMERCE_KEY", "/nonexistent"))
            custody_path = Path(
                os.environ.get("NURIA_PRIVY_CREDENTIALS", "/nonexistent")
            )
            if not (
                custody_path.exists() if policy.signer == "privy" else key_path.exists()
            ):
                result["missing"].append(
                    "managed_custody_configuration"
                    if policy.signer == "privy"
                    else "isolated_signing_key"
                )
            if policy.mint and policy.creator_wallet:
                result["fee_path"] = fee_observation(
                    policy.mint, policy.creator_wallet, rpc, policy.pool
                )
            if policy.spending_wallet:
                result["usdc_balance"] = usdc_balance(
                    policy.spending_wallet, rpc, ledger.minimum_balance_slot()
                )
                if not result["usdc_balance"]["micro_usdc"]:
                    result["missing"].append("USDC_funding")
            executor.reconcile(policy)
            collector.reconcile()
            converter.reconcile()
            sweeper.reconcile()
            if policy.enabled:
                if policy.signer == "privy":
                    keypair = ManagedSigner(policy.spending_wallet, ledger, policy)
                    result["custody"] = keypair.check()
                else:
                    keypair = load_key(key_path, policy.spending_wallet)
                controls_path = Path(
                    os.environ.get("NURIA_COLLECTION_CONFIG", "/nonexistent")
                )
                if controls_path.exists():
                    controls = json.loads(controls_path.read_text())
                    if controls.get("enabled"):
                        collector.collect(
                            result["fee_path"], policy, keypair, controls, time.time()
                        )
                        result["rails"]["pump_claim"] = (
                            "guarded_standard_collection_configured"
                        )
                sweep_path = Path(os.environ.get("NURIA_SWEEP_CONFIG", "/nonexistent"))
                if sweep_path.exists():
                    controls = json.loads(sweep_path.read_text())
                    if controls.get("enabled"):
                        creator_file = Path(
                            os.environ["NURIA_CREATOR_PRIVY_CREDENTIALS"]
                        )
                        creator_signer = ManagedSigner(
                            policy.creator_wallet,
                            ledger,
                            policy,
                            lambda value: invoke(value, creator_file),
                        )
                        creator_signer.check()
                        sweeper.sweep(policy, creator_signer, controls, time.time())
                        result["rails"]["creator_to_operating"] = (
                            "guarded_native_forwarding_configured"
                        )
                conversion_path = Path(
                    os.environ.get("NURIA_CONVERSION_CONFIG", "/nonexistent")
                )
                if conversion_path.exists():
                    controls = json.loads(conversion_path.read_text())
                    if controls.get("enabled"):
                        converter.convert(
                            policy,
                            keypair,
                            controls,
                            result["usdc_balance"],
                            time.time(),
                        )
                        result["rails"]["sol_to_usdc"] = (
                            "constrained_jupiter_v2_conversion_configured"
                        )
                cognition_path = Path(os.environ["NURIA_COMMERCE_COGNITION"])
                if (
                    not 0
                    <= time.time() - (cognition_path / "status.json").stat().st_mtime
                    <= 15
                ):
                    raise ValueError("Cognitive evidence is stale")
                cognition = json.loads((cognition_path / "status.json").read_text())
                executor.evaluate(
                    json.loads((cognition_path / "effects.json").read_text()),
                    time.time(),
                )
                decisions = json.loads((cognition_path / "decisions.json").read_text())
                selected = (
                    select_job(policy, cognition, decisions[0], time.time(), ledger)
                    if decisions
                    else None
                )
                if selected:
                    choice = information.consider(selected, time.time())
                    if choice is not None:
                        result["information_choice"] = choice
                        selected = None
                if selected:
                    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    daily = ledger.db.execute(
                        "SELECT coalesce(sum(amount),0) FROM jobs WHERE day=?", (day,)
                    ).fetchone()[0]
                    pending = ledger.summary()["reserved_micro_usdc"]
                    ceiling = selected["provider"].maximum_micro_usdc
                    if (
                        daily + ceiling > policy.per_day_micro_usdc
                        or result["usdc_balance"]["micro_usdc"] - pending - ceiling
                        < policy.reserve_micro_usdc
                    ):
                        selected = None
                if selected:
                    url = (
                        os.environ.get("HELIUS_RPC_URL")
                        or "https://mainnet.helius-rpc.com/?api-key="
                        + os.environ["HELIUS_API_KEY"]
                    )
                    executor.purchase(policy, selected, keypair, url, time.time())
                result["phase"], result["financial_execution"] = "running", True
        except Exception:
            result["phase"] = "unknown"
            result["error"] = (
                "Financial configuration, evidence or execution check failed; details are not inferred and payment retries remain blocked."
            )
        result.update(ledger.summary())
        result["ledger_index"] = export_pages(ledger, public)
        result["headroom"] = {
            "trade_volume_independent": True,
            "maximum_paid_jobs_per_day": 86400 // policy.cooldown_seconds
            if result["policy"]
            else None,
            "monthly_signature_limit": result["policy"].get("monthly_signature_limit")
            if result["policy"]
            else None,
            "scope": "Payment scheduling and signing are bounded independently of trade ingestion. Reaching a ceiling pauses purchases, not the neural stream.",
        }
        result["feedback"] = executor.feedback()
        result["learning_rule"] = (
            "Fresh free SOL/USD context displaces the basic paid-price adapter; unavailable free data defers that purchase. Structured forecasts still choose matching neural action when 0.5 uncertainty + 0.25 surprise + 0.25 learned provider reward - 0.1 relative cost exceeds 0.1. Reward: local Brier error minus paid Brier error minus 0.01 × USDC price."
        )
        result["scope"] = (
            "Privy-managed exact USDC purchases and gated standard-Pump claims into the verified creator beneficiary. Direct creator funding uses the same fee and operating wallet; no reserve multisig is used. SOL conversion has separate native ceilings. No consciousness result is established."
        )
        publish(public, "status.json", result)
        stop.wait(10)


if __name__ == "__main__":
    main()

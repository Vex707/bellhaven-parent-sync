"""Match website locations to CRM accounts and turn every discrepancy into a reviewable proposal.

Nothing here writes to the CRM. Each proposal carries the evidence a reviewer
needs plus a list of `ops` that apply.py executes only after approval.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date

from . import config
from .normalize import (care_types, is_po_box, name_similarity, phone_digits, street_key,
                        street_number)

FIELD_LABELS = {
    "name": "Name", "parent_id": "Parent account", "billing_street": "Street", "billing_city": "City",
    "billing_state": "State", "billing_zip": "Zip", "phone": "Phone", "care_type": "Care type",
    "status": "Status", "duplicate_of_account": "Duplicate of", "chow_current_account": "CHOW current account",
    "note": "Note",
}


@dataclass
class Candidate:
    account: dict
    score: int
    signals: list[str] = field(default_factory=list)
    phone_match: bool = False
    admin_match: bool = False

    def summary(self) -> dict:
        a = self.account
        return {"account_id": a["account_id"], "name": a["name"], "parent_name": a["parent_name"],
                "address": f"{a['billing_street']}, {a['billing_city']}, {a['billing_state']} {a['billing_zip']}",
                "score": self.score, "signals": self.signals}


def has_billing_lock(account: dict) -> bool:
    """SOP: revenue history AND outstanding AR > 0 means billing needs the old account preserved."""
    return (account.get("lifetime_revenue") or 0) > 0 and (account.get("outstanding_ar") or 0) > 0


def _money(v) -> str:
    return f"${(v or 0):,.0f}"


def _is_parent_account(a: dict) -> bool:
    return a["name"].endswith("(Parent Account)")


def _is_resolved(a: dict) -> bool:
    """Already marked as a duplicate or superseded by a CHOW successor; never a live match."""
    return bool(a.get("duplicate_of_account") or a.get("chow_current_account"))


def _append_note(existing: str, text: str, today: str) -> str:
    line = f"[{today} {config.SYNC_TAG}] {text}"
    return f"{existing}\n{line}" if existing else line


def _proposal_key(kind: str, subject: str, ops: list[dict]) -> str:
    """Stable identity for a proposal: same kind, subject and target values -> same key.

    Notes (which embed the run date) and 'before' snapshots are excluded so a
    re-run of unchanged data reproduces the key and is skipped as already-decided.
    """
    material = [{"op": o["op"], "account_id": o.get("account_id"),
                 "fields": {k: v for k, v in o.get("fields", {}).items() if k != "note"}} for o in ops]
    digest = hashlib.sha1(json.dumps(material, sort_keys=True).encode()).hexdigest()[:10]
    return f"{kind}:{subject}:{digest}"


class Matcher:
    def __init__(self, locations: list[dict], accounts: list[dict], contacts: list[dict], today: str | None = None):
        self.locations = locations
        self.accounts = accounts
        self.today = today or date.today().isoformat()
        self.by_id = {a["account_id"]: a for a in accounts}
        parent = next((a for a in accounts if a["name"] == config.PARENT_NAME), None)
        if not parent:
            raise RuntimeError(f"Parent account {config.PARENT_NAME!r} not found in CRM")
        self.parent = parent
        self.contacts_by_account: dict[str, list[dict]] = {}
        for c in contacts:
            if c.get("is_active", True):
                self.contacts_by_account.setdefault(c["account_id"], []).append(c)

    # ---------- scoring ----------
    def score(self, loc: dict, acct: dict) -> Candidate | None:
        if acct["billing_state"] != loc["state"]:
            return None
        signals, score = [], 0
        if street_key(acct["billing_street"]) == street_key(loc["street"]):
            score += 45
            signals.append(f"Street matches ('{acct['billing_street']}')")
        elif street_number(acct["billing_street"]) and street_number(acct["billing_street"]) == street_number(loc["street"]) \
                and street_key(acct["billing_street"]).split()[1:2] == street_key(loc["street"]).split()[1:2]:
            score += 30
            signals.append(f"Street number and name match ('{acct['billing_street']}')")
        elif is_po_box(acct["billing_street"]):
            signals.append(f"CRM street is a PO box ('{acct['billing_street']}'), not comparable")
        if acct["billing_city"].lower() == loc["city"].lower():
            score += 10
            signals.append("City matches")
        if acct["billing_zip"] == loc["zip"]:
            score += 10
            signals.append("Zip matches")
        sim = name_similarity(loc["name"], acct["name"])
        if sim > 0:
            score += round(25 * sim)
            signals.append(f"Name similarity {sim:.0%} ('{acct['name']}')")
        phone_match = bool(loc.get("phone")) and phone_digits(acct["phone"]) == phone_digits(loc["phone"])
        if phone_match:
            score += 10
            signals.append(f"Phone matches {loc['phone']}")
        admin = (loc.get("administrator") or "").lower()
        admin_match = bool(admin) and any(c["name"].lower() == admin for c in self.contacts_by_account.get(acct["account_id"], []))
        if admin_match:
            score += 20
            signals.append(f"CRM contact '{loc['administrator']}' is the website's listed administrator")
        if score == 0:
            return None
        return Candidate(acct, score, signals, phone_match, admin_match)

    def _survivor_rank(self, c: Candidate):
        a = c.account
        return (
            (a.get("lifetime_revenue") or 0) > 0 or (a.get("outstanding_ar") or 0) > 0,  # keep billing history
            a["parent_id"] == self.parent["account_id"],
            a["status"] == "Active",
            c.phone_match,
            c.admin_match,
            c.score,
            a["account_id"],  # deterministic tie-break
        )

    # ---------- field diffs ----------
    def website_fields(self, loc: dict) -> dict:
        types = care_types(loc["care_offerings"])
        return {
            "name": loc["name"], "billing_street": loc["street"], "billing_city": loc["city"],
            "billing_state": loc["state"], "billing_zip": loc["zip"], "phone": loc["phone"],
            "care_type": types[0] if types else "",
        }

    def field_diffs(self, loc: dict, acct: dict) -> tuple[dict, list[str]]:
        want, diffs, why = self.website_fields(loc), {}, []
        if acct["name"] != want["name"]:
            diffs["name"] = want["name"]
            why.append(f"Name on website is '{want['name']}' (CRM: '{acct['name']}')")
        if street_key(acct["billing_street"]) != street_key(want["billing_street"]):
            diffs["billing_street"] = want["billing_street"]
            why.append(f"Street on website is '{want['billing_street']}' (CRM: '{acct['billing_street']}')")
        for f in ("billing_city", "billing_state", "billing_zip"):
            if acct[f].strip().lower() != want[f].strip().lower():
                diffs[f] = want[f]
                why.append(f"{FIELD_LABELS[f]} on website is '{want[f]}' (CRM: '{acct[f]}')")
        if want["phone"] and phone_digits(acct["phone"]) != phone_digits(want["phone"]):
            diffs["phone"] = want["phone"]
            why.append(f"Phone on website is {want['phone']} (CRM: {acct['phone'] or 'blank'})")
        offered = care_types(loc["care_offerings"])
        if offered and acct["care_type"] not in offered:
            diffs["care_type"] = offered[0]
            why.append(f"Website care offerings {loc['care_offerings']} do not include CRM care type '{acct['care_type'] or 'blank'}'")
        if acct["status"] != "Active":
            diffs["status"] = "Active"
            why.append(f"Community is listed on the website but CRM status is '{acct['status']}'")
        return diffs, why

    # ---------- proposal builders ----------
    def _patch(self, acct: dict, fields: dict) -> dict:
        return {"op": "patch", "account_id": acct["account_id"], "fields": fields,
                "before": {k: acct.get(k, "") for k in fields}}

    def _proposal(self, kind, category, subject, title, summary, ops, evidence, confidence="high",
                  location=None, account=None, candidates=None):
        return {
            "key": _proposal_key(kind, subject, ops), "kind": kind, "category": category, "title": title,
            "summary": summary, "ops": ops, "evidence": evidence, "confidence": confidence,
            "location": location, "account": account, "candidates": candidates or [],
        }

    def run(self) -> tuple[list[dict], list[dict]]:
        """Returns (proposals, report). The report classifies every location and orphaned account."""
        proposals, report = [], []
        claimed: set[str] = set()
        live = [a for a in self.accounts if not _is_parent_account(a) and not _is_resolved(a)]
        pid = self.parent["account_id"]

        for loc in self.locations:
            scored = [c for a in live if (c := self.score(loc, a))]
            scored.sort(key=lambda c: c.score, reverse=True)
            matches = [c for c in scored if c.score >= config.MATCH_THRESHOLD]
            near = [c.summary() for c in scored[:3] if c.score >= 20]
            loc_evidence = [f"Website: {loc['name']}, {loc['street']}, {loc['city']}, {loc['state']} {loc['zip']} "
                            f"· {', '.join(loc['care_offerings'])} · admin {loc['administrator'] or 'n/a'} · {loc['phone']}",
                            f"Found on: {', '.join(loc['found_on'])} ({loc['url']})"]

            if not matches:
                fields = {**self.website_fields(loc), "parent_id": pid, "status": "Active",
                          "note": _append_note("", f"Created from Bellhaven website listing {loc['url']}", self.today)}
                ev = loc_evidence + ["No CRM account scored above the match threshold "
                                     f"({config.MATCH_THRESHOLD})."]
                for c in scored[:3]:
                    ev.append(f"Closest non-match: {c.account['name']} ({c.account['parent_name'] or 'no parent'}, "
                              f"{c.account['billing_street']}) scored {c.score}: {'; '.join(c.signals)}")
                for a in live:
                    if a["name"].lower() == loc["name"].lower() and a["billing_state"] != loc["state"]:
                        ev.append(f"Same-name account ignored: {a['name']} ({a['account_id']}) is in {a['billing_city']}, "
                                  f"{a['billing_state']} under {a['parent_name'] or 'no parent'}, a different facility.")
                proposals.append(self._proposal(
                    "create", "No CRM account yet", loc["slug"], f"Create account: {loc['name']}",
                    f"{loc['name']} is on the Bellhaven website but has no CRM account. Create it under Bellhaven.",
                    [{"op": "create", "ref": "new", "fields": fields}], ev, location=loc, candidates=near))
                report.append({"location": loc["name"], "classification": "No CRM account yet", "account_id": None})
                continue

            matches.sort(key=self._survivor_rank, reverse=True)
            best, losers = matches[0], matches[1:]
            acct = best.account
            claimed.update(c.account["account_id"] for c in matches)
            ev = loc_evidence + [f"Matched CRM account {acct['name']} ({acct['account_id']}), score {best.score}: "
                                 + "; ".join(best.signals),
                                 f"Current parent: {acct['parent_name'] or 'none'} · billing: lifetime revenue "
                                 f"{_money(acct['lifetime_revenue'])}, outstanding AR {_money(acct['outstanding_ar'])}"]
            if losers:
                ev.append(f"{len(losers)} other account(s) also match this address; this one was kept because it ranks "
                          "highest on billing history > already under Bellhaven > active > phone match > admin contact > score.")
            confidence = "high" if best.score >= 75 else "medium"
            if losers and not (best.phone_match or best.admin_match or acct["parent_id"] == pid):
                confidence = "medium"
            diffs, why = self.field_diffs(loc, acct)

            if acct["parent_id"] != pid:
                if has_billing_lock(acct):
                    new_fields = {**self.website_fields(loc), "parent_id": pid, "status": "Active",
                                  "note": _append_note("", f"CHOW successor of {acct['account_id']} ({acct['name']}, "
                                                           f"formerly under {acct['parent_name'] or 'no parent'}). "
                                                           f"Source: {loc['url']}", self.today)}
                    ops = [{"op": "create", "ref": "new", "fields": new_fields},
                           self._patch(acct, {"chow_current_account": "$ref:new"})]
                    ev.append(f"SOP: account has revenue history ({_money(acct['lifetime_revenue'])}) AND outstanding AR "
                              f"({_money(acct['outstanding_ar'])}) -> do not re-parent. Create a new account under "
                              "Bellhaven and point the old account's chow_current_account at it. Old account otherwise untouched.")
                    proposals.append(self._proposal(
                        "chow", "Needs fix: change of ownership (CHOW)", acct["account_id"],
                        f"CHOW: {acct['name']} -> new Bellhaven account",
                        f"{loc['name']} is now Bellhaven but the CRM account sits under {acct['parent_name'] or 'no parent'} "
                        "and has open billing. Preserve it and create a successor account.",
                        ops, ev + why, confidence, loc, acct, near))
                    classification = "Needs fix (CHOW)"
                else:
                    fields = {"parent_id": pid, **diffs}
                    fields["note"] = _append_note(acct["note"], f"Re-parented from {acct['parent_name'] or 'no parent'} to "
                                                  f"Bellhaven; synced to {loc['url']}", self.today)
                    ev.append("SOP: " + ("no revenue history" if not (acct["lifetime_revenue"] or 0) > 0 else "no outstanding AR")
                              + " -> re-parent the existing account directly.")
                    proposals.append(self._proposal(
                        "reparent", "Needs fix: wrong parent", acct["account_id"],
                        f"Re-parent {acct['name']} to Bellhaven" + (" and update fields" if diffs else ""),
                        f"{loc['name']} is a Bellhaven community but the CRM account is under "
                        f"{acct['parent_name'] or 'no parent'}.",
                        [self._patch(acct, fields)], ev + why, confidence, loc, acct, near))
                    classification = "Needs fix (wrong parent)"
            elif diffs:
                fields = {**diffs, "note": _append_note(acct["note"], f"Synced fields to {loc['url']}: "
                                                        + ", ".join(FIELD_LABELS[f] for f in diffs), self.today)}
                proposals.append(self._proposal(
                    "update", "Needs fix: outdated data", acct["account_id"], f"Update {acct['name']}",
                    f"Matched correctly under Bellhaven, but {', '.join(FIELD_LABELS[f].lower() for f in diffs)} "
                    "differ from the website.", [self._patch(acct, fields)], ev + why, confidence, loc, acct, near))
                classification = "Needs fix (outdated data)"
            else:
                classification = "Confident match"
            report.append({"location": loc["name"], "classification": classification, "account_id": acct["account_id"],
                           "account_name": acct["name"], "score": best.score,
                           "duplicates": [c.account["account_id"] for c in losers]})

            for loser in losers:
                la = loser.account
                lev = loc_evidence + [f"Same facility as surviving account {acct['name']} ({acct['account_id']}): "
                                      + "; ".join(loser.signals),
                                      f"This copy: {la['name']} under {la['parent_name'] or 'no parent'}, status {la['status']}, "
                                      f"revenue {_money(la['lifetime_revenue'])}, AR {_money(la['outstanding_ar'])}"]
                lconf = confidence
                loser_contacts = self.contacts_by_account.get(la["account_id"], [])
                if loser_contacts:
                    lev.append("This copy has contacts that stay attached to it: "
                               + ", ".join(f"{c['name']} ({c['title']})" for c in loser_contacts))
                if (la["lifetime_revenue"] or 0) > 0 or (la["outstanding_ar"] or 0) > 0:
                    lev.append("WARNING: this copy carries billing history; confirm with billing before inactivating.")
                    lconf = "low"
                fields = {"duplicate_of_account": acct["account_id"], "status": "Inactive",
                          "note": _append_note(la["note"], f"Duplicate of {acct['account_id']} ({loc['name']}, "
                                               f"{loc['street']})", self.today)}
                proposals.append(self._proposal(
                    "duplicate", "Duplicate account", la["account_id"],
                    f"Mark duplicate: {la['name']} -> {acct['name']}",
                    f"Stale second record for {loc['name']} at the same address.",
                    [self._patch(la, fields)], lev, lconf, loc, la, near))

        # Accounts under Bellhaven that no website community claimed.
        for acct in live:
            if acct["parent_id"] != pid or acct["account_id"] in claimed or acct["status"] != "Active":
                continue
            successors = [a for a in live if a["account_id"] != acct["account_id"] and a["parent_id"] != pid
                          and street_key(a["billing_street"]) == street_key(acct["billing_street"])
                          and a["billing_zip"] == acct["billing_zip"]]
            ev = [f"CRM: {acct['name']}, {acct['billing_street']}, {acct['billing_city']}, {acct['billing_state']} "
                  f"{acct['billing_zip']} under Bellhaven · revenue {_money(acct['lifetime_revenue'])}, "
                  f"AR {_money(acct['outstanding_ar'])}",
                  "Not found among the communities on the Bellhaven website (directory, home and about pages)."]
            if successors:
                succ = successors[0]
                ev.append(f"Another account at the same address belongs to a different parent: {succ['name']} "
                          f"({succ['account_id']}) under {succ['parent_name'] or 'no parent'}; the facility appears to "
                          "have changed ownership.")
                if has_billing_lock(acct):
                    ev.append("SOP: revenue history AND outstanding AR -> keep the old account exactly as is and set "
                              "chow_current_account to the existing successor account (no new account needed).")
                    proposals.append(self._proposal(
                        "sold_chow", "Not on website: sold (CHOW)", acct["account_id"],
                        f"CHOW: {acct['name']} -> {succ['name']}",
                        f"{acct['name']} left Bellhaven; {succ['name']} now holds the facility.",
                        [self._patch(acct, {"chow_current_account": succ["account_id"]})], ev, "high", None, acct))
                else:
                    ev.append("SOP: no billing lock, and the new owner's account already exists -> mark this copy as a "
                              "duplicate of the successor instead of creating a second account under the new parent.")
                    fields = {"duplicate_of_account": succ["account_id"], "status": "Inactive",
                              "note": _append_note(acct["note"], f"Facility now under {succ['parent_name']} as "
                                                   f"{succ['account_id']}", self.today)}
                    proposals.append(self._proposal(
                        "duplicate", "Not on website: sold", acct["account_id"],
                        f"Mark duplicate: {acct['name']} -> {succ['name']}",
                        f"{acct['name']} left Bellhaven; the new owner's account already exists.",
                        [self._patch(acct, fields)], ev, "medium", None, acct))
                report.append({"location": None, "classification": "Not on website (sold)", "account_id": acct["account_id"],
                               "account_name": acct["name"]})
            else:
                ev.append("No other CRM account shares its address, so the new owner (or a closure) is unknown.")
                fields = {"status": "Needs Review",
                          "note": _append_note(acct["note"], "No longer listed on the Bellhaven website; ownership "
                                               "unknown. Verify (sold/closed) before re-parenting.", self.today)}
                proposals.append(self._proposal(
                    "not_on_website", "Not on website", acct["account_id"], f"Flag for review: {acct['name']}",
                    f"{acct['name']} is under Bellhaven in the CRM but no longer on Bellhaven's website.",
                    [self._patch(acct, fields)], ev, "medium", None, acct))
                report.append({"location": None, "classification": "Not on website", "account_id": acct["account_id"],
                               "account_name": acct["name"]})
        return proposals, report

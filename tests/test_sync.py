import copy

import pytest

from bellhaven_sync.apply import StaleProposal, apply_proposal
from bellhaven_sync.matcher import Matcher
from bellhaven_sync.normalize import name_similarity, street_key
from bellhaven_sync.store import Store

BH = "001BELLHAVEN"
OTHER = "001OTHERPARENT"
TODAY = "2026-10-16"


def acct(account_id, name, street, city, state, zip_, parent_id=BH, **kw):
    base = {"account_id": account_id, "name": name, "parent_id": parent_id,
            "parent_name": {BH: "Bellhaven Senior Living (Parent Account)", OTHER: "Harborview Care Group (Parent Account)"}.get(parent_id, ""),
            "billing_street": street, "billing_city": city, "billing_state": state, "billing_zip": zip_,
            "care_type": "Assisted Living", "status": "Active", "phone": "(555) 000-0000", "lifetime_revenue": 0,
            "outstanding_ar": 0, "chow_current_account": "", "duplicate_of_account": "", "note": ""}
    base.update(kw)
    return base


def loc(slug, name, street, city, state, zip_, **kw):
    base = {"slug": slug, "url": f"https://x/communities/{slug}", "name": name, "street": street, "city": city,
            "state": state, "zip": zip_, "care_offerings": ["Assisted Living"], "administrator": "", "phone": "(555) 000-0000",
            "found_on": ["directory"]}
    base.update(kw)
    return base


PARENTS = [acct(BH, "Bellhaven Senior Living (Parent Account)", "", "", "", "", parent_id=""),
           acct(OTHER, "Harborview Care Group (Parent Account)", "", "", "", "", parent_id="")]


class FakeCRM:
    def __init__(self, accounts):
        self.accounts = {a["account_id"]: copy.deepcopy(a) for a in accounts}
        self.n = 0

    def get_account(self, account_id):
        return copy.deepcopy(self.accounts[account_id])

    def update_account(self, account_id, fields):
        self.accounts[account_id].update(fields)
        return self.accounts[account_id]

    def create_account(self, fields):
        self.n += 1
        new = acct(f"001NEW{self.n}", fields["name"], "", "", "", "")
        new.update(fields)
        self.accounts[new["account_id"]] = new
        return new

    def list_accounts(self):
        return list(self.accounts.values())


def run(locations, accounts, contacts=()):
    return Matcher(locations, accounts, list(contacts), today=TODAY).run()


def by_kind(proposals, kind):
    return [p for p in proposals if p["kind"] == kind]


def test_street_normalization():
    assert street_key("4850 NW Sylvania Ave") == street_key("4850 Northwest Sylvania Avenue")
    assert street_key("3313 Wilmington Pk") == street_key("3313 Wilmington Pike")
    assert street_key("199 Barks Rd W") == street_key("199 Barks Road West")


def test_name_similarity_ignores_brand_words():
    assert name_similarity("Bellhaven Rehab and Nursing of Grove City",
                           "Bellhaven Rehabilitation & Nursing of Grove City") == 1.0


def test_exact_match_needs_no_proposal():
    a = acct("001A", "Bellhaven of Akron", "55 Ridgewood Ave", "Akron", "OH", "44313")
    props, report = run([loc("akron", "Bellhaven of Akron", "55 Ridgewood Avenue", "Akron", "OH", "44313")], PARENTS + [a])
    assert props == []
    assert report[0]["classification"] == "Confident match"


def test_wrong_parent_without_ar_is_reparented_directly():
    a = acct("001A", "Old Name", "1 Main St", "Lima", "OH", "45801", parent_id=OTHER, lifetime_revenue=47000, outstanding_ar=0)
    props, _ = run([loc("lima", "Bellhaven of Lima", "1 Main St", "Lima", "OH", "45801")], PARENTS + [a])
    (p,) = props
    assert p["kind"] == "reparent"
    assert p["ops"][0]["fields"]["parent_id"] == BH
    assert p["ops"][0]["fields"]["name"] == "Bellhaven of Lima"


def test_wrong_parent_with_revenue_and_ar_follows_chow_sop():
    a = acct("001A", "Bellhaven of Tiffin", "45 St Lawrence Dr", "Tiffin", "OH", "44883", parent_id=OTHER,
             lifetime_revenue=84000, outstanding_ar=12400)
    props, _ = run([loc("tiffin", "Bellhaven of Tiffin", "45 St Lawrence Dr", "Tiffin", "OH", "44883")], PARENTS + [a])
    (p,) = props
    assert p["kind"] == "chow"
    create, patch = p["ops"]
    assert create["op"] == "create" and create["fields"]["parent_id"] == BH
    # Old account: only the CHOW pointer changes; parent stays put.
    assert patch["fields"] == {"chow_current_account": "$ref:new"}

    crm = FakeCRM(PARENTS + [a])
    apply_proposal(crm, p)
    old = crm.accounts["001A"]
    assert old["parent_id"] == OTHER
    new = crm.accounts[old["chow_current_account"]]
    assert new["parent_id"] == BH and new["name"] == "Bellhaven of Tiffin"


def test_duplicates_at_same_address_keep_the_bellhaven_copy():
    keep = acct("001KEEP", "Bellhaven Shores of Erie", "4930 W Lake Rd", "Erie", "PA", "16505")
    dup = acct("001DUP", "Harborview Shores of Erie", "4930 West Lake Road", "Erie", "PA", "16505", parent_id=OTHER)
    props, _ = run([loc("erie", "Bellhaven Shores of Erie", "4930 W Lake Rd", "Erie", "PA", "16505")], PARENTS + [keep, dup])
    (p,) = by_kind(props, "duplicate")
    assert p["ops"][0]["account_id"] == "001DUP"
    assert p["ops"][0]["fields"]["duplicate_of_account"] == "001KEEP"
    assert p["ops"][0]["fields"]["status"] == "Inactive"


def test_unmatched_location_is_created_and_other_state_namesake_ignored():
    namesake = acct("001CO", "Amberly Manor", "918 S Nevada Ave", "Colorado Springs", "CO", "80903", parent_id=OTHER)
    props, _ = run([loc("amberly", "Amberly Manor", "4390 Darrow Rd", "Hudson", "OH", "44236")], PARENTS + [namesake])
    (p,) = props
    assert p["kind"] == "create"
    assert any("Same-name account ignored" in e for e in p["evidence"])


def test_bellhaven_account_missing_from_site_is_flagged_or_chowed():
    gone = acct("001GONE", "Bellhaven of Coldwater", "90 N Michigan Ave", "Coldwater", "MI", "49036")
    sold = acct("001SOLD", "Bellhaven of Sandusky", "2715 Columbus Ave", "Sandusky", "OH", "44870",
                lifetime_revenue=130000, outstanding_ar=5200)
    buyer = acct("001BUYER", "Millstone Care of Sandusky", "2715 Columbus Ave", "Sandusky", "OH", "44870", parent_id=OTHER)
    props, _ = run([], PARENTS + [gone, sold, buyer])
    kinds = {p["ops"][0]["account_id"]: p for p in props}
    assert kinds["001GONE"]["kind"] == "not_on_website"
    assert kinds["001GONE"]["ops"][0]["fields"]["status"] == "Needs Review"
    assert kinds["001SOLD"]["kind"] == "sold_chow"
    assert kinds["001SOLD"]["ops"][0]["fields"] == {"chow_current_account": "001BUYER"}


def test_reruns_never_repropose_decided_items(tmp_path):
    store = Store(tmp_path / "state.db")
    a = acct("001A", "Old Name", "1 Main St", "Lima", "OH", "45801", parent_id=OTHER)
    d = acct("001D", "Lima Dup", "1 Main Street", "Lima", "OH", "45801", parent_id=OTHER)
    locations = [loc("lima", "Bellhaven of Lima", "1 Main St", "Lima", "OH", "45801")]
    crm = FakeCRM(PARENTS + [a, d])

    props, report = run(locations, crm.list_accounts())
    _, new = store.record_run(1, 4, props, report)
    assert new == 2

    # Same data again: nothing new.
    props, report = run(locations, crm.list_accounts())
    assert store.record_run(1, 4, props, report)[1] == 0

    # Approve the re-parent, reject the duplicate, then re-run on the updated CRM.
    reparent = next(p for p in store.proposals("pending") if p["kind"] == "reparent")
    dup = next(p for p in store.proposals("pending") if p["kind"] == "duplicate")
    store.decide(reparent["key"], "applied", result=apply_proposal(crm, reparent["payload"]))
    store.decide(dup["key"], "rejected")

    props, report = run(locations, crm.list_accounts())
    assert [p["kind"] for p in props] == ["duplicate"]  # the fixed account needs nothing now
    assert store.record_run(1, 4, props, report)[1] == 0  # and the rejected duplicate is not re-queued
    assert store.proposals("pending") == []


def test_apply_refuses_stale_proposal():
    a = acct("001A", "Old Name", "1 Main St", "Lima", "OH", "45801", parent_id=OTHER)
    props, _ = run([loc("lima", "Bellhaven of Lima", "1 Main St", "Lima", "OH", "45801")], PARENTS + [a])
    crm = FakeCRM(PARENTS + [a])
    crm.accounts["001A"]["name"] = "Someone edited this"
    with pytest.raises(StaleProposal):
        apply_proposal(crm, props[0])
    assert crm.accounts["001A"]["parent_id"] == OTHER

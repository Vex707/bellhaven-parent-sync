# Bellhaven parent-link sync

Keeps the CRM's facility → parent-company links for **Bellhaven Senior Living** in step with Bellhaven's own website.
It scrapes every community, matches each one to a CRM account, and turns every discrepancy into a proposal with evidence.
A reviewer then approves or rejects each proposal in a small local app. **Nothing is written to the CRM until a reviewer approves it.**

```
website ──scrape──▶ locations ─┐
                               ├─▶ matcher ──▶ proposals (+evidence) ──▶ SQLite queue ──▶ review app ──approve──▶ CRM API
CRM API ──read────▶ accounts ──┘                                         (idempotent)        (local)    (stale-checked)
          contacts
```

## Results

All 35 proposals were reviewed and approved, and each was verified as applied in the CRM.

| | Before | After |
|---|---|---|
| CRM accounts | 121 | 127 (4 new communities + 2 CHOW successors) |
| Website communities fully in sync (right parent, name, address, phone) | 10 of 35 | 35 of 35 |
| Communities under the right parent | 25 of 35 | 35 of 35 |
| Pipeline re-run | 35 proposals | 0 proposals (all confident matches) |

The full writeup is in [WRITEUP.md](WRITEUP.md).

## Running it

```bash
python -m venv .venv && .venv/Scripts/activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                   # then put your CRM token in .env

python -m bellhaven_sync.pipeline    # scrape + match + queue proposals (read-only against the CRM)
python -m bellhaven_sync.app         # review UI at http://127.0.0.1:5050
pytest -q                            # offline tests (fake CRM)
```

The review app also has a **Run pipeline now** button. Its tabs are *Pending*, *Approved & applied*, *Rejected*, *Failed*, *Superseded*, and a *Match report* that classifies all 35 communities and the orphaned accounts.
Every account id links to the CRM browser, so you can check a change right after it lands.

## Layout

| File | Role |
|---|---|
| `bellhaven_sync/scraper.py` | Walks the paginated directory by following *Next* links. It also collects community links from the home and about pages, then parses each detail page (name, address, care offerings, administrator, phone). |
| `bellhaven_sync/normalize.py` | Normalizes addresses (`NW Sylvania Ave` = `Northwest Sylvania Avenue`, `Wilmington Pk` = `Pike`), names, and phones, and maps website care offerings to the CRM picklist. |
| `bellhaven_sync/matcher.py` | Scores candidates, resolves duplicates, applies the billing SOP, and builds proposals with evidence. Pure logic, with no I/O. |
| `bellhaven_sync/store.py` | SQLite queue of proposals and decisions, which is where idempotency lives. |
| `bellhaven_sync/apply.py` | Executes an approved proposal. It re-reads each account first and refuses the write if the account changed since the proposal was made. |
| `bellhaven_sync/app.py` | Flask review app. |
| `.github/workflows/daily-sync.yml`, `schedule/crontab.txt` | Daily schedule, either one. |

## Matching approach

**Scraping.** The directory says "34 communities" but the home page says 35. The 35th, *Bellhaven Meadows of Findlay*, appears only in a "new this year" notice on the home page. So the scraper treats the directory as incomplete and also harvests community links from the home and about pages. Each detail page is the source of truth.

**Scoring.** A candidate account must be in the same state. Its score is a sum of independent signals:

| Signal | Points |
|---|---|
| Normalized street matches exactly | 45 (30 if only the number and street name match) |
| City matches / zip matches | 10 each |
| Name similarity, with brand words ("Bellhaven", "of", "at", "the") removed | up to 25 |
| Phone matches the website | 10 |
| A CRM contact is the website's listed administrator | 20 |

A candidate needs **≥ 60** to be treated as the same facility. Two signals carry a lot of weight:
- **Address beats name.** Renamed facilities (*Riverbend Manor Care Center* → *Bellhaven of Chagrin Falls*, *Sunny Acres* → *Bellhaven Willow Creek*) match on address. Same-name facilities elsewhere (the *Amberly Manor* in Colorado Springs under Juniper Point) are ignored.
- **The administrator contact is strong evidence when the address is broken.** For example, Ashtabula's CRM street is a PO box, but the administrator, name, phone and zip all agree.

*Union Square Senior Living* (Juniper Point) shares a city and part of a name with *Bellhaven at Union Square*. But the street and the administrator differ, so it scores 36 and I create a new account. The near-miss is shown in the evidence so a reviewer can overrule.

**Duplicates.** When several accounts match one community (Monroe has three, Kettering three, Erie, Port Clinton and Owosso two each), one survivor is kept. Survivors rank on:
1. billing history
2. already under Bellhaven
3. Active status
4. phone matches the website
5. administrator contact matches
6. score
7. account id (deterministic tie-break)

Each loser gets `duplicate_of_account = survivor` and `status = Inactive`, plus a note. A loser that still has billing history, or contacts, is called out in the evidence.

**Classification → action:**

| Situation | Proposal |
|---|---|
| Matched, under Bellhaven, fields agree | none ("Confident match" in the report) |
| Matched, under Bellhaven, name/street/zip/phone/care type/status differ | `update`: set the fields to the website values. Street changes are proposed only when the *normalized* address differs, so `Ave` vs `Avenue` isn't noise. |
| Matched, wrong parent, **no** revenue history or **no** open AR | `reparent`: set `parent_id` to Bellhaven, plus any field fixes |
| Matched, wrong parent, revenue **and** AR > 0 | `chow`: create a new account under Bellhaven from the website data, and set the old account's `chow_current_account` to it. The old account is otherwise untouched (no rename, no note). |
| On the website, no account scores ≥ 60 | `create` under Bellhaven |
| Under Bellhaven, not on the website, another parent's account at the same address | Facility was sold. With revenue + AR, `sold_chow`: point `chow_current_account` at the buyer's existing account, because creating another would make a duplicate. Without the billing lock, mark it a duplicate of the buyer's account. |
| Under Bellhaven, not on the website, no successor | `not_on_website`: `status = Needs Review` plus a note. I can't tell a closure from a sale to an unknown owner, so the parent is left alone instead of guessed. |

Every write except the CHOW pointer appends a dated `[bellhaven-sync]` line to `note`, so the CRM keeps its own audit trail.

## Billing SOP

`has_billing_lock()` is true when `lifetime_revenue > 0 AND outstanding_ar > 0`, and it is checked before any parent change.
The current data has three cases:
- **Marietta** ($51,250 revenue / $3,800 AR) and **Tiffin** ($84,000 / $12,400): CHOW to new Bellhaven accounts.
- **Sandusky** ($130,000 / $5,200): left Bellhaven for Millstone, so it gets a CHOW pointer to the existing Millstone account.

Lima ($47,000 / $0) and Findlay ($22,000 / $0) have revenue but no AR, so they are re-parented directly.

## Safe re-runs

- **Proposal identity.** Each proposal key is `kind : subject : hash(target values)`. Notes, with their run dates, and before-snapshots are excluded. A re-run over unchanged data reproduces the same keys, and any key already in the store (pending or decided) is skipped. Approved fixes stop being proposed because the CRM now matches. Rejected ones stay rejected. If the website later changes the target values, that is a genuinely new proposal.
- **Superseded proposals.** A pending proposal that the latest run no longer produces is marked *superseded*, so nobody approves something out of date.
- **Stale-check on apply.** `apply.py` compares each account against the proposal's before-snapshot and refuses the write if someone edited it in the meantime.
- **CHOW idempotency.** An account with `chow_current_account` or `duplicate_of_account` set is treated as resolved and never matched again.

## Schedule

`.github/workflows/daily-sync.yml` runs daily at 11:00 UTC. It only generates proposals and never writes to the CRM. It persists `data/state.db` across runs with `actions/cache`. In production I'd move that state into a shared database the review app reads, and alert the reviewer when new proposals arrive. `schedule/crontab.txt` has the equivalent cron line.

## Judgment calls a reviewer should know about

- **Kettering survivor.** None of the three accounts at 3313 Wilmington Pike has billing history, contacts or a phone match. *Kettering Care Centre* (Harborview) wins the tie-break on name similarity and is flagged medium confidence.
- **Phones.** The website's phone is treated as authoritative and synced. Those changes are visible as their own field rows, so they can be rejected on their own.
- **Alliance and Coldwater** go to *Needs Review* rather than being re-parented or inactivated, because there's no evidence of who owns them now.
- **Contacts on duplicate copies** stay where they are. Re-homing them is listed under next steps.

## What I'd build next

- Re-home contacts from inactivated duplicates onto the survivor, and create the administrator contact for new accounts.
- A "re-open" action for rejected proposals, and per-field approve/reject within one proposal.
- Run it for every parent operator, not just Bellhaven: the matcher is parent-agnostic except for the website scraper.
- Change alerts (Slack or email) when a run produces new proposals, and a weekly digest of *Needs Review* accounts.
- Use an address-validation API (USPS/Smarty) instead of hand-rolled normalization, to catch zip/street typos like Portsmouth's 45626 vs 45662 with a second source.

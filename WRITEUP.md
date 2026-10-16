# Writeup

## Matching approach

I treated Bellhaven's website as the source of truth and the address as the main identity signal. Names drift with rebrands. Addresses mostly don't.

**Scraping.** The directory says "34 communities" and the home page says 35. The missing one, Bellhaven Meadows of Findlay, appears only in a home-page announcement. So the scraper follows the directory's pagination and also collects community links from the home and about pages, then reads every detail page.

**Scoring.** Candidates must be in the same state. Each one is then scored on independent signals:
- normalized street match (`NW … Ave` = `Northwest … Avenue`)
- city and zip
- name similarity with brand words like "Bellhaven", "of" and "at" removed
- phone
- whether a CRM contact is the website's listed administrator

At 60 points or more, a candidate is treated as the same facility. Address-first matching caught renames like Riverbend Manor → Bellhaven of Chagrin Falls and Sunny Acres → Bellhaven Willow Creek. It also ignored a same-name "Amberly Manor" in Colorado. The administrator signal matched Ashtabula, whose CRM street was a PO box. Union Square Senior Living (Juniper Point) shares a city and a partial name with Bellhaven at Union Square, but its street and administrator differ, so I created a new account and showed the near-miss as evidence.

**Duplicates.** When several accounts match one community, one survivor is kept. The ranking is billing history first, then already under Bellhaven, active, phone match and administrator contact. The losers get `duplicate_of_account` and Inactive. The duplicates were in Monroe (3 accounts), Kettering (3), Erie, Port Clinton and Owosso.

**Billing SOP.** Before any parent change, I check `lifetime_revenue > 0 AND outstanding_ar > 0`.
- Marietta and Tiffin met it. Their old Cedar Trail accounts were left untouched, and new Bellhaven accounts were created and linked through `chow_current_account`.
- Lima and Findlay had revenue but no AR, so they were re-parented directly.
- Sandusky left Bellhaven for Millstone with open AR. Millstone already had an account for it, so I pointed the CHOW field there instead of creating a duplicate.
- Alliance and Coldwater are no longer on the site and have no successor, so I set them to Needs Review rather than guess an owner.

**Safety.** Proposals are keyed by kind, account and target values. A re-run never re-queues anything pending or decided. Before writing, the app re-reads each account and refuses the write if it changed since the proposal was made.

**Result.** I approved all 35 proposals after reviewing their evidence; I didn't reject any. The CRM went from 121 to 127 accounts. A fresh run now finds all 35 communities as confident matches and proposes nothing.

## How I used AI tools

I used Claude Code as the engineer and acted as the reviewer and decision-maker.
- **Exploration.** Claude read the brief, mapped the API from its OpenAPI spec, scraped the site and dumped all 121 accounts and 67 contacts. Together we listed the traps: acquired-parent accounts, same-address duplicates, renames, CHOW cases, and the community missing from the directory.
- **Build.** It wrote the scraper, matcher, review app and offline tests with a fake CRM covering the SOP branches, duplicates, idempotency and stale-write refusal.
- **Checking its work.** I didn't take the pipeline's output on faith. We dry-ran the matcher read-only and checked every proposal against the raw data before anything could write. We confirmed that a second run queues nothing new. After my approvals, we verified that every applied field matched the live CRM.
- **Judgment calls.** I went back over the weakest calls: the Kettering tie-break, the phone updates, and Sunny Acres' lower match score. I agreed with all of them, so the end state reflects my review rather than the tool's defaults.

## What I'd build next

- Move contacts from inactivated duplicates to the survivor. For example, one Owosso contact is still on the inactive copy.
- Per-field approval within a proposal, and a re-open action for rejected items.
- Persist the state in a shared database, and alert reviewers when the daily run finds something new.
- Generalize the scraper per operator so the same matcher covers every parent company, not just Bellhaven.
- Use address validation (USPS/Smarty) as a second source for street and zip corrections.

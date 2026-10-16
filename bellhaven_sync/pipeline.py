"""Daily entry point: scrape -> read CRM -> match -> queue new proposals. Never writes to the CRM.

    python -m bellhaven_sync.pipeline
"""
import argparse
import json
from collections import Counter

from . import config
from .crm import CRMClient
from .matcher import Matcher
from .scraper import scrape
from .store import Store


def run(store: Store | None = None, crm: CRMClient | None = None) -> dict:
    store = store or Store()
    crm = crm or CRMClient()
    locations = [l.to_dict() for l in scrape()]
    accounts = crm.list_accounts()
    contacts = crm.list_contacts()

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "last_scrape.json").write_text(json.dumps(locations, indent=1), encoding="utf-8")

    proposals, report = Matcher(locations, accounts, contacts).run()
    run_id, new = store.record_run(len(locations), len(accounts), proposals, report)
    return {"run_id": run_id, "locations": len(locations), "accounts": len(accounts), "proposals": len(proposals),
            "new_proposals": new, "already_decided_or_queued": len(proposals) - new,
            "classification": dict(Counter(r["classification"] for r in report))}


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(run(), indent=2))


if __name__ == "__main__":
    main()

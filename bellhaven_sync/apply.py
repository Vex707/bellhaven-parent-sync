"""Execute an approved proposal against the CRM.

Before writing, every patched account is re-read and compared with the
snapshot the proposal was built from. If anything changed in between, the
apply is refused (status 'failed') rather than overwriting newer data.
"""
from .crm import CRMClient, CRMError


class StaleProposal(RuntimeError):
    pass


def _check_fresh(crm: CRMClient, op: dict) -> None:
    current = crm.get_account(op["account_id"])
    drift = {k: (v, current.get(k, "")) for k, v in op["before"].items() if (current.get(k) or "") != (v or "")}
    if drift:
        detail = "; ".join(f"{k}: expected {a!r}, now {b!r}" for k, (a, b) in drift.items())
        raise StaleProposal(f"Account {op['account_id']} changed since this proposal was made ({detail}). Re-run the pipeline.")


def apply_proposal(crm: CRMClient, proposal: dict) -> dict:
    ops = proposal["ops"]
    for op in ops:
        if op["op"] == "patch":
            _check_fresh(crm, op)

    refs, log = {}, []
    for op in ops:
        fields = {k: (refs[v[5:]] if isinstance(v, str) and v.startswith("$ref:") else v) for k, v in op["fields"].items()}
        if op["op"] == "create":
            created = crm.create_account(fields)
            created = created.get("data", created)
            refs[op["ref"]] = created["account_id"]
            log.append({"op": "create", "account_id": created["account_id"], "fields": fields})
        elif op["op"] == "patch":
            crm.update_account(op["account_id"], fields)
            log.append({"op": "patch", "account_id": op["account_id"], "fields": fields})
        else:
            raise CRMError(f"Unknown op {op['op']}")
    return {"applied": log}

"""Local review app. Nothing reaches the CRM until a reviewer clicks Approve.

    python -m bellhaven_sync.app      # then open http://127.0.0.1:5050
"""
import traceback
from collections import Counter

from flask import Flask, flash, g, redirect, render_template_string, request, url_for

from . import config
from .apply import StaleProposal, apply_proposal
from .crm import CRMClient, CRMError
from .matcher import FIELD_LABELS
from .pipeline import run as run_pipeline
from .store import Store

app = Flask(__name__)
app.secret_key = "local-review-only"
store = Store()

TABS = [("pending", "Pending review"), ("applied", "Approved & applied"), ("rejected", "Rejected"),
        ("failed", "Failed"), ("superseded", "Superseded"), ("report", "Match report")]

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bellhaven Sync Review</title>
<style>
 :root { --ink:#1f2a27; --muted:#68736f; --line:#dde3e0; --bg:#f4f6f5; --card:#fff; --green:#2e5d50; --gold:#b8901d;
         --red:#b3392f; --blue:#2f5f9e; --chip:#eef2f0; }
 * { box-sizing:border-box; } body { margin:0; font:15px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; color:var(--ink); background:var(--bg); }
 header { background:var(--green); color:#fff; padding:14px 24px; display:flex; flex-wrap:wrap; gap:12px; align-items:center; justify-content:space-between; }
 header h1 { font-size:18px; margin:0; } header .meta { font-size:13px; opacity:.85; }
 header form button { background:var(--gold); }
 .wrap { max-width:1100px; margin:0 auto; padding:18px 16px 80px; }
 nav.tabs { display:flex; flex-wrap:wrap; gap:6px; margin:4px 0 18px; }
 nav.tabs a { padding:7px 14px; border-radius:999px; background:var(--card); border:1px solid var(--line); color:var(--ink); text-decoration:none; font-size:14px; }
 nav.tabs a.on { background:var(--ink); color:#fff; border-color:var(--ink); }
 .flash { padding:10px 14px; border-radius:6px; margin-bottom:12px; background:#e7f3ec; border:1px solid #b9dcc6; }
 .flash.error { background:#fbeaea; border-color:#eab8b4; }
 .filters { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:14px; font-size:13px; }
 .filters a { color:var(--blue); text-decoration:none; padding:3px 9px; border-radius:4px; background:var(--chip); }
 .filters a.on { background:var(--blue); color:#fff; }
 .card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px 18px; margin-bottom:14px; }
 .card h2 { font-size:16px; margin:4px 0 2px; } .card .sum { color:var(--muted); margin:0 0 10px; }
 .chips span { display:inline-block; font-size:12px; font-weight:600; padding:2px 9px; border-radius:999px; background:var(--chip); margin-right:6px; }
 .chips .high { background:#e3f1e8; color:#1d6b44; } .chips .medium { background:#fbf1d6; color:#80600c; } .chips .low { background:#fbe3e1; color:var(--red); }
 details { margin:8px 0; } summary { cursor:pointer; font-weight:600; font-size:14px; }
 ul.ev { margin:6px 0 0; padding-left:20px; font-size:14px; } ul.ev li { margin-bottom:4px; }
 table { width:100%; border-collapse:collapse; font-size:14px; margin-top:8px; }
 th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
 th { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
 td.old { color:var(--red); text-decoration:line-through; } td.new { color:#1d6b44; font-weight:600; }
 .opfor { font-size:13px; margin-top:12px; color:var(--muted); } .opfor a { color:var(--blue); }
 .actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:14px; }
 .actions input[type=text] { flex:1; min-width:200px; padding:8px 10px; border:1px solid var(--line); border-radius:6px; font:inherit; }
 button { padding:8px 16px; border:0; border-radius:6px; font:600 14px/1 inherit; color:#fff; cursor:pointer; }
 button.approve { background:var(--green); } button.reject { background:var(--red); }
 .decided { font-size:13px; color:var(--muted); margin-top:10px; }
 pre { white-space:pre-wrap; font-size:12px; background:var(--chip); padding:8px; border-radius:6px; }
 .empty { color:var(--muted); padding:30px; text-align:center; }
 .stats { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:16px; }
 .stats div { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 14px; font-size:13px; color:var(--muted); }
 .stats b { display:block; font-size:20px; color:var(--ink); }
 @media (max-width:600px) { .hide-sm { display:none; } }
</style></head><body>
<header>
  <div><h1>Bellhaven parent-link sync · review queue</h1>
  <div class="meta">{% if last_run %}Last run #{{ last_run.id }} at {{ last_run.started_at }} · {{ last_run.locations }} website communities · {{ last_run.accounts }} CRM accounts · {{ last_run.new_proposals }} new proposals{% else %}No runs yet{% endif %}</div></div>
  <form method="post" action="{{ url_for('run_now') }}"><button>Run pipeline now</button></form>
</header>
<div class="wrap">
  {% for cat, msg in get_flashed_messages(with_categories=true) %}<div class="flash {{ cat }}">{{ msg }}</div>{% endfor %}
  <nav class="tabs">{% for key, label in tabs %}<a class="{{ 'on' if key == tab }}" href="{{ url_for('index', tab=key) }}">{{ label }}{% if counts.get(key) %} ({{ counts[key] }}){% endif %}</a>{% endfor %}</nav>

  {% if tab == 'report' %}
    {% if last_run %}
    <div class="stats">{% for k, v in report_counts.items() %}<div><b>{{ v }}</b>{{ k }}</div>{% endfor %}</div>
    <div class="card"><table><tr><th>Website community</th><th>Classification</th><th>CRM account</th><th class="hide-sm">Score</th></tr>
    {% for r in last_run.report %}<tr><td>{{ r.location or '—' }}</td><td>{{ r.classification }}</td>
      <td>{% if r.account_id %}<a href="{{ crm_url(r.account_id) }}" target="_blank">{{ r.account_name }}</a>{% if r.duplicates %} <span style="color:var(--muted)">(+{{ r.duplicates|length }} duplicate{{ 's' if r.duplicates|length > 1 }})</span>{% endif %}{% else %}—{% endif %}</td>
      <td class="hide-sm">{{ r.score or '' }}</td></tr>{% endfor %}</table></div>
    {% else %}<div class="empty">Run the pipeline to see the match report.</div>{% endif %}
  {% else %}
    {% if categories %}<div class="filters"><a class="{{ 'on' if not cat }}" href="{{ url_for('index', tab=tab) }}">All</a>
      {% for c, n in categories.items() %}<a class="{{ 'on' if c == cat }}" href="{{ url_for('index', tab=tab, cat=c) }}">{{ c }} ({{ n }})</a>{% endfor %}</div>{% endif %}
    {% for p in items %}{% set pl = p.payload %}
    <div class="card" id="{{ p.key }}">
      <div class="chips"><span>{{ p.category }}</span><span class="{{ pl.confidence }}">{{ pl.confidence }} confidence</span></div>
      <h2>{{ p.title }}</h2><p class="sum">{{ pl.summary }}</p>
      {% for op in pl.ops %}
        <div class="opfor">{% if op.op == 'create' %}<b>Create new account</b>{% else %}<b>Update</b> <a href="{{ crm_url(op.account_id) }}" target="_blank">{{ op.account_id }}</a>{% if pl.account and pl.account.account_id == op.account_id %} · {{ pl.account.name }}{% endif %}{% endif %}</div>
        <table><tr><th>Field</th>{% if op.op == 'patch' %}<th>Current</th>{% endif %}<th>Proposed</th></tr>
        {% for f, v in op.fields.items() %}<tr><td>{{ labels.get(f, f) }}</td>
          {% if op.op == 'patch' %}<td class="old">{{ show(f, op.before.get(f)) }}</td>{% endif %}
          <td class="new">{{ show(f, v) }}</td></tr>{% endfor %}</table>
      {% endfor %}
      <details {{ 'open' if tab == 'pending' }}><summary>Evidence</summary><ul class="ev">{% for e in pl.evidence %}<li>{{ e }}</li>{% endfor %}</ul></details>
      {% if pl.candidates %}<details><summary>Candidate accounts considered ({{ pl.candidates|length }})</summary><table><tr><th>Account</th><th>Parent</th><th class="hide-sm">Address</th><th>Score</th></tr>
        {% for c in pl.candidates %}<tr><td><a href="{{ crm_url(c.account_id) }}" target="_blank">{{ c.name }}</a></td><td>{{ c.parent_name or '—' }}</td><td class="hide-sm">{{ c.address }}</td><td>{{ c.score }}</td></tr>{% endfor %}</table></details>{% endif %}
      {% if p.status == 'pending' %}
      <form class="actions" method="post" action="{{ url_for('decide', key=p.key) }}">
        <input type="text" name="note" placeholder="Reviewer note (optional)">
        <button class="approve" name="action" value="approve">Approve &amp; apply</button>
        <button class="reject" name="action" value="reject">Reject</button>
      </form>
      {% else %}
      <div class="decided">{{ p.status|capitalize }} {{ p.decided_at }}{% if p.reviewer_note %} · “{{ p.reviewer_note }}”{% endif %}</div>
      {% if p.result %}<details><summary>Result</summary><pre>{{ p.result | tojson(indent=1) }}</pre></details>{% endif %}
      {% endif %}
    </div>
    {% else %}<div class="empty">Nothing here.</div>{% endfor %}
  {% endif %}
</div></body></html>"""


@app.template_global()
def crm_url(account_id):
    return config.crm_browser_url(account_id)


@app.template_global()
def show(field, value):
    if value in (None, ""):
        return "(blank)"
    if isinstance(value, str) and value.startswith("$ref:"):
        return "→ id of the new account created above"
    if field in ("parent_id", "duplicate_of_account", "chow_current_account"):
        name = g.names.get(value)
        return f"{value} ({name})" if name else value
    return value


@app.before_request
def load_account_names():
    """Live id -> name map so account references in the queue are readable."""
    try:
        g.names = {a["account_id"]: a["name"] for a in CRMClient().list_accounts()}
    except CRMError:
        g.names = {}


@app.route("/")
def index():
    tab = request.args.get("tab", "pending")
    cat = request.args.get("cat")
    all_items = store.proposals()
    counts = Counter(p["status"] for p in all_items)
    items = [p for p in all_items if p["status"] == tab] if tab != "report" else []
    categories = Counter(p["category"] for p in items)
    if cat:
        items = [p for p in items if p["category"] == cat]
    last_run = store.latest_run()
    report_counts = Counter(r["classification"] for r in last_run["report"]) if last_run else {}
    return render_template_string(PAGE, tabs=TABS, tab=tab, cat=cat, items=items, counts=counts, labels=FIELD_LABELS,
                                  categories=dict(sorted(categories.items())), last_run=last_run,
                                  report_counts=dict(report_counts))


@app.post("/proposals/<path:key>")
def decide(key):
    p = store.get(key)
    if not p or p["status"] != "pending":
        flash("That proposal is no longer pending.", "error")
        return redirect(url_for("index"))
    note = request.form.get("note", "").strip()
    if request.form.get("action") == "reject":
        store.decide(key, "rejected", note)
        flash(f"Rejected: {p['title']}")
        return redirect(url_for("index"))
    try:
        result = apply_proposal(CRMClient(), p["payload"])
        store.decide(key, "applied", note, result)
        flash(f"Applied to CRM: {p['title']}")
    except (StaleProposal, CRMError) as e:
        store.decide(key, "failed", note, {"error": str(e)})
        flash(f"Not applied: {e}", "error")
    except Exception as e:  # keep the reviewer informed rather than 500ing
        store.decide(key, "failed", note, {"error": str(e), "trace": traceback.format_exc()})
        flash(f"Not applied (unexpected error): {e}", "error")
    return redirect(url_for("index"))


@app.post("/run")
def run_now():
    try:
        summary = run_pipeline(store)
        flash(f"Run #{summary['run_id']}: {summary['proposals']} proposals from the current data, "
              f"{summary['new_proposals']} new (the rest were already queued or decided).")
    except Exception as e:
        flash(f"Pipeline run failed: {e}", "error")
    return redirect(url_for("index"))


def main():
    config.require_token()
    app.run(host="127.0.0.1", port=5050, debug=False)


if __name__ == "__main__":
    main()

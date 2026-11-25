"""
Create a BRAND-NEW Plaid connection (fresh link) for a bank login.

Only use this when the normal relink (relink_account.bat, update mode) cannot
repair a connection. A fresh link creates a new access token and new account
IDs, so the Finance Dashboard and Budget app must be switched over afterwards
(ask Claude). The result is saved, encrypted, to new_link_result.json in the
Transaction Database folder; nothing else is changed by this script.

Usage:
  new_link.bat
"""
import json
import os
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from flask import Flask, request as freq
import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_transactions import LinkTokenTransactions
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.products import Products

DB_DIR   = Path(r"C:\Users\jbond\OneDrive - Lehi City\Parks\Desktop\Excel\Transaction Database")
ENV_PATH = DB_DIR / ".env"
OUT_PATH = DB_DIR / "new_link_result.json"
PORT     = 5001

load_dotenv(ENV_PATH)
fernet = Fernet(os.getenv("ENCRYPTION_KEY", "").encode())
client = plaid_api.PlaidApi(plaid.ApiClient(plaid.Configuration(
    host=plaid.Environment.Production,
    api_key={
        "clientId": os.getenv("PLAID_CLIENT_ID", ""),
        "secret":   os.getenv("PLAID_PRODUCTION_SECRET", ""),
    },
)))

if OUT_PATH.exists():
    print(f"A previous result is still waiting to be used:\n  {OUT_PATH}")
    print("Tell Claude before linking again (it may already hold a new connection).")
    input("Press Enter to exit.")
    raise SystemExit

link_token = client.link_token_create(LinkTokenCreateRequest(
    user=LinkTokenCreateRequestUser(client_user_id="user"),
    client_name="Finance Dashboard",
    products=[Products("transactions")],
    additional_consented_products=[Products("liabilities")],
    transactions=LinkTokenTransactions(days_requested=365),
    country_codes=[CountryCode("US")],
    language="en",
)).link_token

app    = Flask(__name__)
done   = threading.Event()
result = {}

HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>New Bank Connection</title>
<style>
  body {{ font-family: -apple-system, sans-serif; display: flex; align-items: center;
          justify-content: center; height: 100vh; margin: 0; background: #f2f2f7; }}
  .msg {{ text-align: center; color: #1c1c1e; max-width: 420px; }}
  p {{ color: #6e6e73; }}
</style></head>
<body><div class="msg" id="msg"><h2>Opening Plaid Link…</h2></div>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
  const handler = Plaid.create({{
    token: '{link_token}',
    onSuccess: function(public_token, metadata) {{
      fetch('/done', {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
                       body: JSON.stringify({{public_token: public_token}})}});
      document.getElementById('msg').innerHTML =
        '<h2>Done!</h2><p>Close this window and go back to the black window.</p>';
    }},
    onEvent: function(eventName, metadata) {{
      fetch('/log', {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
                      body: JSON.stringify({{event: eventName, meta: metadata}})}});
    }},
    onExit: function(err, metadata) {{
      fetch('/log', {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
                      body: JSON.stringify({{event: 'EXIT', error: err, meta: metadata}})}});
      document.getElementById('msg').innerHTML =
        '<h2>Cancelled</h2><p>Nothing was changed. Close this window.</p>';
    }},
  }});
  handler.open();
</script></body></html>""".format(link_token=link_token)


@app.route("/")
def index():
    return HTML


LOG_PATH = DB_DIR / "new_link_log.txt"


@app.route("/log", methods=["POST"])
def log_route():
    d = freq.get_json(silent=True) or {}
    m = d.get("meta") or {}
    e = d.get("error") or {}
    line = " | ".join(str(x) for x in [
        datetime.now().strftime("%H:%M:%S"), d.get("event"),
        m.get("institution_name") or "", m.get("view_name") or "",
        m.get("error_code") or e.get("error_code") or "",
        m.get("error_message") or e.get("error_message") or "",
        "request_id=" + str(m.get("request_id") or ""),
        "session=" + str(m.get("link_session_id") or ""),
    ])
    print("  [Plaid] " + line)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return "", 204


@app.route("/done", methods=["POST"])
def done_route():
    result["public_token"] = (freq.get_json(silent=True) or {}).get("public_token")
    done.set()
    return "", 204


def open_browser():
    import time
    time.sleep(1)
    webbrowser.open(f"http://127.0.0.1:{PORT}")


threading.Thread(target=open_browser, daemon=True).start()
threading.Thread(target=lambda: app.run(port=PORT, debug=False, use_reloader=False), daemon=True).start()

print(f"\nBrowser opening at http://127.0.0.1:{PORT}")
print("Tip: copy that address into a private window if a different person")
print("signed in to this bank in your normal browser.")
print("Complete the Plaid flow, then come back here. Ctrl+C cancels.\n")

done.wait()
if not result.get("public_token"):
    print("No connection was returned. Nothing changed.")
    input("Press Enter to exit.")
    raise SystemExit

exch = client.item_public_token_exchange(
    ItemPublicTokenExchangeRequest(public_token=result["public_token"]))
access_token = exch.access_token
accounts = client.accounts_get(AccountsGetRequest(access_token=access_token)).accounts

OUT_PATH.write_text(json.dumps({
    "created": datetime.now().isoformat(timespec="seconds"),
    "item_id": exch.item_id,
    "encrypted_token": fernet.encrypt(access_token.encode()).decode(),
    "accounts": [{
        "account_id": a.account_id,
        "name": a.name,
        "official_name": a.official_name,
        "mask": a.mask,
        "type": str(a.type),
        "subtype": str(a.subtype) if a.subtype else "",
    } for a in accounts],
}, indent=2))

print("New connection created. Accounts on it:")
for a in accounts:
    print(f"  {a.name[:32]:<32} {str(a.subtype or a.type):<12} {a.mask}")
print("\nSaved (encrypted). Tell Claude it's done; nothing else has been changed yet.")
input("Press Enter to exit.")

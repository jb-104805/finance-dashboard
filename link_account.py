"""
Update an existing Plaid bank connection to add missing accounts.
Run this from the Transaction Database folder's venv when an account
is missing from the dashboard.

Usage:
  python link_account.py
"""
import os
import sqlite3
import threading
import webbrowser
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from flask import Flask, request as freq
import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.country_code import CountryCode
from plaid.model.products import Products

DB_PATH  = Path(r"C:\Users\jbond\OneDrive - Lehi City\Parks\Desktop\Excel\Transaction Database\finance.db")
ENV_PATH = Path(r"C:\Users\jbond\OneDrive - Lehi City\Parks\Desktop\Excel\Transaction Database\.env")
PORT     = 5001

load_dotenv(ENV_PATH)

fernet = Fernet(os.getenv("ENCRYPTION_KEY", "").encode())
cfg    = plaid.Configuration(
    host=plaid.Environment.Production,
    api_key={
        "clientId": os.getenv("PLAID_CLIENT_ID", ""),
        "secret":   os.getenv("PLAID_PRODUCTION_SECRET", ""),
    },
)
client = plaid_api.PlaidApi(plaid.ApiClient(cfg))


def _decrypt(enc: str) -> str:
    return fernet.decrypt(enc.encode()).decode()


def get_institutions():
    """Return list of (institution_name, encrypted_access_token)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT DISTINCT i.name AS inst_name, a.access_token
        FROM institutions i
        JOIN accounts a ON a.institution_id = i.id
        WHERE a.access_token IS NOT NULL
        ORDER BY i.name
    """).fetchall()
    conn.close()
    return [(r["inst_name"], r["access_token"]) for r in rows]


def create_link_token(access_token: str) -> str:
    resp = client.link_token_create(LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id="user"),
        client_name="Finance Dashboard",
        access_token=access_token,
        country_codes=[CountryCode("US")],
        language="en",
        additional_consented_products=[Products("liabilities")],
    ))
    return resp.link_token


# ── Pick institution ──────────────────────────────────────────────────────────

institutions = get_institutions()
if not institutions:
    print("No linked institutions found in finance.db.")
    input("Press Enter to exit.")
    raise SystemExit

print("\nLinked institutions:")
for i, (name, _) in enumerate(institutions, 1):
    print(f"  {i}. {name}")

while True:
    try:
        choice = int(input("\nEnter the number of the institution to update: "))
        if 1 <= choice <= len(institutions):
            break
    except ValueError:
        pass
    print("  Invalid choice, try again.")

inst_name, enc_token = institutions[choice - 1]
print(f"\nCreating update link for {inst_name}...")

try:
    access_token = _decrypt(enc_token)
    link_token   = create_link_token(access_token)
except Exception as e:
    print(f"Error: {e}")
    input("Press Enter to exit.")
    raise SystemExit

# ── Local Flask server to host Plaid Link ─────────────────────────────────────

app  = Flask(__name__)
done = threading.Event()

HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>Update Bank Connection</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; display: flex; align-items: center;
            justify-content: center; height: 100vh; margin: 0; background: #f2f2f7; }}
    .msg {{ text-align: center; color: #1c1c1e; }}
    h2   {{ font-size: 1.4rem; margin-bottom: 8px; }}
    p    {{ color: #8e8e93; font-size: 0.95rem; }}
  </style>
</head>
<body>
<div class="msg" id="msg">
  <h2>Opening Plaid Link…</h2>
  <p>If the window does not appear, check your browser.</p>
</div>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
  const handler = Plaid.create({{
    token: '{link_token}',
    onSuccess: function(public_token, metadata) {{
      fetch('/done', {{method: 'POST'}});
      document.getElementById('msg').innerHTML =
        '<h2>Done!</h2><p>Close this window and run Sync on your dashboard to see the new account.</p>';
    }},
    onExit: function(err, metadata) {{
      document.getElementById('msg').innerHTML =
        '<h2>Cancelled</h2><p>Close this window and run link_account.py again if needed.</p>';
    }},
  }});
  handler.open();
</script>
</body>
</html>""".format(link_token=link_token)


@app.route("/")
def index():
    return HTML


@app.route("/done", methods=["POST"])
def done_route():
    done.set()
    return "", 204


def open_browser():
    import time
    time.sleep(1)
    webbrowser.open(f"http://127.0.0.1:{PORT}")


threading.Thread(target=open_browser, daemon=True).start()

print(f"Opening browser at http://127.0.0.1:{PORT}")
print("Complete the Plaid flow, then come back here.")
print("Press Ctrl+C to cancel.\n")

server = threading.Thread(
    target=lambda: app.run(port=PORT, debug=False, use_reloader=False),
    daemon=True,
)
server.start()

done.wait()
print("\nPlaid Link completed successfully.")
print("Now tap Sync on your dashboard — the new account will appear automatically.")
input("Press Enter to exit.")

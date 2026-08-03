"""
GitHub Actions sync: pulls latest data from Plaid, writes everything
back to a single GitHub Gist (dashboard.json).

Environment variables (set as GitHub Actions Secrets):
  PLAID_CLIENT_ID, PLAID_PRODUCTION_SECRET, ENCRYPTION_KEY,
  GIST_ID, GH_TOKEN
"""
import hashlib
import json
import os
from datetime import date, datetime, timedelta

import requests
from cryptography.fernet import Fernet
import plaid
from plaid.api import plaid_api
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest

PLAID_CLIENT_ID = os.environ["PLAID_CLIENT_ID"]
PLAID_SECRET    = os.environ["PLAID_PRODUCTION_SECRET"]
ENCRYPTION_KEY  = os.environ["ENCRYPTION_KEY"]
GIST_ID         = os.environ["GIST_ID"]
GH_TOKEN        = os.environ["GH_TOKEN"].strip()
GIST_FILE       = "dashboard.json"

fernet = Fernet(ENCRYPTION_KEY.encode())
cfg = plaid.Configuration(
    host=plaid.Environment.Production,
    api_key={"clientId": PLAID_CLIENT_ID, "secret": PLAID_SECRET},
)
client = plaid_api.PlaidApi(plaid.ApiClient(cfg))

GH_HEADERS = {
    "Authorization": f"token {GH_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}


def _decrypt(enc: str) -> str:
    return fernet.decrypt(enc.encode()).decode()


def _hash(enc: str) -> str:
    return hashlib.sha256(enc.encode()).hexdigest()[:16]


def _str(obj) -> str:
    """Safely extract string from Plaid enum or plain value."""
    return obj.value if hasattr(obj, "value") else str(obj)


def load_state() -> dict:
    r = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=GH_HEADERS)
    r.raise_for_status()
    content = r.json()["files"][GIST_FILE]["content"]
    return json.loads(content)


def save_state(state: dict) -> None:
    r = requests.patch(
        f"https://api.github.com/gists/{GIST_ID}",
        headers=GH_HEADERS,
        json={"files": {GIST_FILE: {"content": json.dumps(state, indent=2)}}},
    )
    r.raise_for_status()


def sync_transactions(state: dict) -> None:
    """Cursor-based incremental sync for all institutions."""
    txns    = {t["id"]: t for t in state.get("transactions", [])}
    cursors = dict(state.get("cursors", {}))

    for enc_token in state.get("tokens", []):
        try:
            access_token = _decrypt(enc_token)
        except Exception as e:
            print(f"  Decrypt error: {e}")
            continue

        h      = _hash(enc_token)
        cursor = cursors.get(h, "")
        added = modified = removed = 0

        while True:
            try:
                resp = client.transactions_sync(
                    TransactionsSyncRequest(access_token=access_token, cursor=cursor, count=500)
                )
            except Exception as e:
                body = getattr(e, "body", "") or ""
                if "ITEM_LOGIN_REQUIRED" in str(body):
                    print(f"  {h[:8]}: ITEM_LOGIN_REQUIRED — run link_account.py to re-authenticate this institution")
                else:
                    print(f"  {h[:8]}: sync error — {e}")
                break

            for txn in resp.added:
                txns[txn.transaction_id] = {
                    "id":       txn.transaction_id,
                    "acct_id":  txn.account_id,
                    "date":     str(txn.date),
                    "amount":   txn.amount,
                    "merchant": txn.merchant_name or txn.name,
                    "pending":  bool(txn.pending),
                    "tag":      "",
                }
                added += 1

            for txn in resp.modified:
                existing_tag = txns.get(txn.transaction_id, {}).get("tag", "")
                txns[txn.transaction_id] = {
                    "id":       txn.transaction_id,
                    "acct_id":  txn.account_id,
                    "date":     str(txn.date),
                    "amount":   txn.amount,
                    "merchant": txn.merchant_name or txn.name,
                    "pending":  bool(txn.pending),
                    "tag":      existing_tag,
                }
                modified += 1

            for t in resp.removed:
                txns.pop(t.transaction_id, None)
                removed += 1

            cursor = resp.next_cursor
            if not resp.has_more:
                break

        cursors[h] = cursor
        print(f"  {h[:8]}: +{added} added  {modified} modified  {removed} removed")

    cutoff = (date.today() - timedelta(days=365)).isoformat()
    state["transactions"] = [t for t in txns.values() if t["date"] >= cutoff]
    state["cursors"]      = cursors


def sync_balances(state: dict) -> None:
    """Fetch live balances and update account metadata."""
    acct_map  = {a["id"]: a for a in state.get("accounts", [])}
    live_bals = {}

    for enc_token in state.get("tokens", []):
        try:
            access_token = _decrypt(enc_token)
        except Exception:
            continue

        try:
            resp = client.accounts_balance_get(
                AccountsBalanceGetRequest(access_token=access_token)
            )
        except Exception as e:
            print(f"  Balance error: {e}")
            continue

        for acct in resp.accounts:
            current   = acct.balances.current
            available = acct.balances.available
            limit     = acct.balances.limit
            acct_type = acct.type.value if hasattr(acct.type, "value") else str(acct.type)
            if acct_type == "credit" and not current and available is not None and limit is not None:
                current = limit - available  # Plaid reports last-statement $0 but new charges exist
            live_bals[acct.account_id] = current or 0.0

            if acct.account_id not in acct_map:
                print(f"  New account discovered: {acct.name} — defaulting to included")
                acct_map[acct.account_id] = {
                    "id":      acct.account_id,
                    "name":    acct.name,
                    "type":    _str(acct.type),
                    "subtype": _str(acct.subtype),
                    "include": True,
                }

    state["accounts"] = list(acct_map.values())

    checking = 0.0
    cc_cards = []
    for acct in state["accounts"]:
        if not acct.get("include"):
            continue
        bal = live_bals.get(acct["id"], 0.0)
        if acct.get("subtype") == "checking":
            checking += bal
        elif acct.get("type") == "credit":
            cc_cards.append({"name": acct["name"], "balance": bal})

    state["checking"] = checking
    state["cc_cards"] = sorted(cc_cards, key=lambda x: x["name"])


def main():
    print("Loading state from Gist...")
    state = load_state()

    print("Syncing transactions...")
    sync_transactions(state)

    print("Syncing balances...")
    sync_balances(state)

    state["synced_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    print("Saving updated state to Gist...")
    save_state(state)
    print("Done.")


if __name__ == "__main__":
    main()

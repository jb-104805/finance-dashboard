"""
One-time setup script. Run this on your Windows PC to:
  1. Read your existing finance.db
  2. Create a GitHub Gist with all your data
  3. Print the exact values to paste into GitHub Secrets and index.html

Usage:
  python setup.py
"""
import json
import sqlite3
import sys
from pathlib import Path

import requests

OLD_DB = Path(r"C:\Users\jbond\OneDrive - Lehi City\Parks\Desktop\Excel\Transaction Database\finance.db")
HERE   = Path(__file__).resolve().parent


def create_gist(content: dict, pat: str) -> tuple[str, str]:
    """Creates a secret Gist, returns (gist_id, raw_url)."""
    r = requests.post(
        "https://api.github.com/gists",
        headers={"Authorization": f"token {pat}", "Accept": "application/vnd.github.v3+json"},
        json={
            "description": "Finance dashboard state — DO NOT SHARE THIS URL",
            "public": False,
            "files": {"dashboard.json": {"content": json.dumps(content, indent=2)}},
        },
    )
    if not r.ok:
        print(f"  GitHub API error: {r.status_code} — {r.text}")
        sys.exit(1)
    data    = r.json()
    gist_id = data["id"]
    raw_url = data["files"]["dashboard.json"]["raw_url"]
    return gist_id, raw_url


def main():
    print("=" * 60)
    print("Finance Dashboard — One-Time Setup")
    print("=" * 60)

    if not OLD_DB.exists():
        print(f"\nERROR: Cannot find finance.db at:\n  {OLD_DB}")
        print("Make sure the path is correct.")
        sys.exit(1)

    pat = input("\nPaste your GitHub Personal Access Token\n(needs 'gist' and 'workflow' scopes): ").strip()
    if not pat:
        print("No token provided. Exiting.")
        sys.exit(1)

    print("\nReading data from finance.db...")
    conn = sqlite3.connect(OLD_DB)
    conn.row_factory = sqlite3.Row

    # Encrypted access tokens (one per institution)
    tokens = [r[0] for r in conn.execute(
        "SELECT DISTINCT access_token FROM accounts WHERE access_token IS NOT NULL"
    ).fetchall()]

    # Sync cursors
    cursors = {}
    for r in conn.execute("SELECT access_token_hash, cursor FROM sync_cursors"):
        cursors[r["access_token_hash"]] = r["cursor"]

    # Account metadata (carry over include flags)
    accounts = []
    for r in conn.execute("SELECT id, name, type, subtype, include_in_calculation FROM accounts"):
        accounts.append({
            "id":      r["id"],
            "name":    r["name"],
            "type":    r["type"] or "",
            "subtype": r["subtype"] or "",
            "include": bool(r["include_in_calculation"]),
        })

    # Commitments
    commitments = []
    for r in conn.execute("SELECT id, description, estimated_amount FROM commitments ORDER BY sort_order"):
        commitments.append({
            "id":          r["id"],
            "description": r["description"],
            "amount":      r["estimated_amount"],
        })

    # Income projections
    income = []
    for r in conn.execute("SELECT id, description, expected_amount FROM income_projections ORDER BY sort_order"):
        income.append({
            "id":          r["id"],
            "description": r["description"],
            "amount":      r["expected_amount"],
        })

    # Recent transactions (last 45 days) with tags
    transactions = []
    for r in conn.execute(
        """
        SELECT t.id, t.account_id, t.date, t.amount,
               COALESCE(t.merchant_name, t.name) AS merchant,
               t.pending, COALESCE(t.tag, '') AS tag
        FROM transactions t
        WHERE t.date >= date('now', '-45 days')
        ORDER BY t.date DESC, t.id DESC
        """
    ):
        transactions.append({
            "id":       r["id"],
            "acct_id":  r["account_id"],
            "date":     r["date"],
            "amount":   r["amount"],
            "merchant": r["merchant"],
            "pending":  bool(r["pending"]),
            "tag":      r["tag"],
        })

    conn.close()

    state = {
        "synced_at":    "",
        "checking":     0.0,
        "cc_cards":     [],
        "tokens":       tokens,
        "cursors":      cursors,
        "accounts":     accounts,
        "transactions": transactions,
        "commitments":  commitments,
        "income":       income,
    }

    print(f"  {len(tokens)} institution(s), {len(accounts)} account(s), "
          f"{len(transactions)} recent transaction(s), "
          f"{len(commitments)} commitment(s), {len(income)} income item(s)")

    print("\nCreating GitHub Gist...")
    gist_id, raw_url = create_gist(state, pat)
    print(f"  Gist ID: {gist_id}")

    # Write config file for easy copy-paste into index.html
    config = {
        "GIST_ID":    gist_id,
        "GH_PAT":     pat,
        "REPO_OWNER": "FILL_IN_YOUR_GITHUB_USERNAME",
        "REPO_NAME":  "FILL_IN_YOUR_REPO_NAME",
    }
    config_path = HERE / "gist_config.json"
    config_path.write_text(json.dumps(config, indent=2))

    print("\n" + "=" * 60)
    print("STEP 1 — Add these 5 Secrets to your GitHub repo:")
    print("  (Settings → Secrets and variables → Actions → New repository secret)")
    print("=" * 60)
    print(f"  GIST_ID               = {gist_id}")
    print(f"  GH_TOKEN              = {pat}")
    print(f"  PLAID_CLIENT_ID       = (from your .env file)")
    print(f"  PLAID_PRODUCTION_SECRET = (from your .env file)")
    print(f"  ENCRYPTION_KEY        = (from your .env file)")

    print("\n" + "=" * 60)
    print("STEP 2 — Fill in the CONFIG block at the top of index.html:")
    print("=" * 60)
    print(f'  GIST_ID:    "{gist_id}",')
    print(f'  GH_PAT:     "{pat}",')
    print(f'  REPO_OWNER: "your-github-username",')
    print(f'  REPO_NAME:  "your-repo-name",')

    print("\n" + "=" * 60)
    print("STEP 3 — After pushing to GitHub:")
    print("  • Go to Settings → Pages → Branch: main → Save")
    print("  • Go to Actions → Sync Plaid Data → Run workflow")
    print("  • Once it finishes, your dashboard is live!")
    print("=" * 60)
    print(f"\nConfig also saved to: {config_path}")
    print("(This file is in .gitignore — do not commit it)")
    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Automated login → API token for the Xynetweb backend.

The login page hashes the password as:
    inner = md5(account + password)
    final = md5(account + inner + checkCode)     # checkCode = a captcha
The captcha endpoint conveniently returns the code in JSON, so the whole flow is
scriptable. The returned `session_key` IS the Authorization token used everywhere.

We store only the account + `inner` hash in .creds.json (git-ignored) — never the
plaintext password (so a leak can't reveal a password reused elsewhere). The `inner`
hash is still a login credential; keep .creds.json private.

CLI:
    ../.venv_orders/bin/python auth.py --set-creds          # prompts for account+password
    ../.venv_orders/bin/python auth.py                      # print a fresh token
"""
import argparse
import hashlib
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CREDS = os.path.join(HERE, ".creds.json")
TOKEN_FILE = os.path.join(HERE, ".token")
BASE = "https://xcx.xynetweb.com"
HEADERS = {
    "Content-Type": "application/json;charset=utf-8",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.xynetweb.com",
    "Referer": "https://www.xynetweb.com/",
}


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def make_inner(account, password):
    return md5(account + password)


def save_creds(account, password=None, inner=None):
    inner = inner or make_inner(account, password)
    with open(CREDS, "w") as f:
        json.dump({"account": account, "inner": inner}, f)
    os.chmod(CREDS, 0o600)


def load_creds():
    """Credentials from env vars (for cloud/Container Apps) or the local file.

    Env (preferred for deployment): XYNET_ACCOUNT + either XYNET_INNER (the
    md5(account+password) hash) or XYNET_PASSWORD (plaintext). Falls back to
    the git-ignored .creds.json written by `auth.py --set-creds`.
    """
    acc = os.environ.get("XYNET_ACCOUNT")
    inner = os.environ.get("XYNET_INNER")
    pw = os.environ.get("XYNET_PASSWORD")
    if acc and inner:
        return {"account": acc, "inner": inner}
    if acc and pw:
        return {"account": acc, "inner": make_inner(acc, pw)}
    if os.path.exists(CREDS):
        with open(CREDS) as f:
            return json.load(f)
    return None


def have_creds():
    return load_creds() is not None


def _post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers=HEADERS, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=25).read())


def _get(path):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    return json.loads(urllib.request.urlopen(req, timeout=25).read())


def login(account=None, inner=None):
    """Perform login, return a fresh session token. Uses stored creds if not given."""
    if not (account and inner):
        c = load_creds()
        if not c:
            raise RuntimeError("No credentials. Run: auth.py --set-creds")
        account, inner = c["account"], c["inner"]
    code = _get("/sram/comm/login/getCheckCode")["data"]
    final = md5(account + inner + str(code))
    resp = _post("/sram/comm/login/onLogin", {
        "password": final, "account": account, "checkCode": code,
        "language": "en", "channel": "1"})
    if resp.get("code") != "H0000":
        msg = (resp.get("data") or {}).get("msg", resp.get("code"))
        raise RuntimeError(f"Login failed: {msg}")
    token = resp["data"]["session_key"]
    with open(TOKEN_FILE, "w") as f:
        f.write(token)
    os.chmod(TOKEN_FILE, 0o600)
    return token


def get_token(force=False):
    """Return a usable token: cached .token unless force, else fresh login."""
    if not force and os.path.exists(TOKEN_FILE):
        t = open(TOKEN_FILE).read().strip()
        if t:
            return t
    return login()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set-creds", action="store_true")
    ap.add_argument("--account")
    ap.add_argument("--password")
    args = ap.parse_args()
    if args.set_creds or (args.account and args.password):
        import getpass
        acc = args.account or input("Account (phone): ").strip()
        pw = args.password or getpass.getpass("Password: ")
        save_creds(acc, password=pw)
        print(f"Saved credentials for {acc} to {CREDS}")
        print("Token:", login())
    else:
        print(get_token(force=True))


if __name__ == "__main__":
    main()

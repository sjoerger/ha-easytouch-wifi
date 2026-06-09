#!/usr/bin/env python3
"""Test DynamoDB Updates table access using authenticated Cognito credentials.

Authenticates with Cognito (SRP), exchanges the ID token for Identity Pool
credentials, then queries the Updates and BetaUpdates tables.

Usage:
    python3 scripts/test_dynamo_updates.py <username> <password> [model_number]

model_number defaults to "352" (first 3 chars of a typical serial).
"""

import base64
import hmac
import hashlib
import sys
import boto3
from pycognito import Cognito

REGION          = "us-east-1"
USER_POOL_ID    = "us-east-1_M9Hs9kugG"
CLIENT_ID       = "2mfujqa7vidd2td9h08k9m061s"
CLIENT_SECRET   = "bbq2v6edtn25j0pgdus7tsi4fq93rgiqfq0g0ener8m896o2euu"
IDENTITY_POOL   = "us-east-1:527ec272-ac1b-4301-8e46-464669790d6e"
TABLE_NAME      = "Updates"
BETA_TABLE_NAME = "BetaUpdates"

if len(sys.argv) < 3:
    print("Usage: test_dynamo_updates.py <username> <password> [model_number]")
    sys.exit(1)

username = sys.argv[1]
password = sys.argv[2]
model    = sys.argv[3] if len(sys.argv) > 3 else "352"

# ── Step 1: Cognito SRP auth ──────────────────────────────────────────────────
print(f"[*] Authenticating with Cognito as {username} ...")
u = Cognito(
    user_pool_id=USER_POOL_ID,
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    username=username,
)
u.authenticate(password=password)
print(f"[+] Authenticated")
print(f"[+] Cognito username (post-auth): {u.username!r}")
print(f"[+] Refresh token: {u.refresh_token[:40]}...")

# ── Step 2: Exchange ID token for Identity Pool credentials ───────────────────
print(f"\n[*] Exchanging ID token for Identity Pool credentials ...")
identity_client = boto3.client("cognito-identity", region_name=REGION)

id_resp = identity_client.get_id(
    IdentityPoolId=IDENTITY_POOL,
    Logins={f"cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}": u.id_token},
)
identity_id = id_resp["IdentityId"]
print(f"[+] Identity ID: {identity_id}")

creds_resp = identity_client.get_credentials_for_identity(
    IdentityId=identity_id,
    Logins={f"cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}": u.id_token},
)
creds = creds_resp["Credentials"]
print(f"[+] Got credentials (expires {creds['Expiration']})")

# ── Step 3: Test refresh token flow ──────────────────────────────────────────
print(f"\n[*] Testing refresh token → new ID token ...")

# Cognito requires SECRET_HASH in refresh calls — compute it manually since
# pycognito's renew_access_token() omits it for client-secret pools.
def _secret_hash(username: str, client_id: str, client_secret: str) -> str:
    msg = (username + client_id).encode("utf-8")
    sig = hmac.new(client_secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return base64.b64encode(sig).decode()

cognito_idp = boto3.client("cognito-idp", region_name=REGION)

# Try both the original login username and the post-auth Cognito username
for hash_username in sorted({username, u.username}):
    print(f"    trying SECRET_HASH with username={hash_username!r}")
    try:
        refresh_resp = cognito_idp.initiate_auth(
            ClientId=CLIENT_ID,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={
                "REFRESH_TOKEN": u.refresh_token,
                "SECRET_HASH": _secret_hash(hash_username, CLIENT_ID, CLIENT_SECRET),
            },
        )
        new_id_token = refresh_resp["AuthenticationResult"]["IdToken"]
        print(f"[+] Token refresh successful with username={hash_username!r}")

        id_resp2 = identity_client.get_id(
            IdentityPoolId=IDENTITY_POOL,
            Logins={f"cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}": new_id_token},
        )
        creds_resp2 = identity_client.get_credentials_for_identity(
            IdentityId=id_resp2["IdentityId"],
            Logins={f"cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}": new_id_token},
        )
        creds = creds_resp2["Credentials"]
        print(f"[+] Fresh credentials via refresh token (expires {creds['Expiration']})")
        break
    except Exception as e:
        print(f"    failed: {e}")
else:
    print(f"[~] Refresh token flow failed — proceeding with initial-auth credentials")

# ── Step 4: Query DynamoDB ────────────────────────────────────────────────────
dynamo = boto3.client(
    "dynamodb",
    region_name=REGION,
    aws_access_key_id=creds["AccessKeyId"],
    aws_secret_access_key=creds["SecretKey"],
    aws_session_token=creds["SessionToken"],
)

for table in (TABLE_NAME, BETA_TABLE_NAME):
    print(f"\n[*] Querying {table} with Device_type={model!r} ...")
    try:
        resp = dynamo.get_item(
            TableName=table,
            Key={"Device_type": {"S": model}},
        )
        item = resp.get("Item")
        if item:
            print(f"[+] Record found:")
            for k, v in item.items():
                val = next(iter(v.values()))
                print(f"    {k:<20} {val}")
        else:
            print(f"[~] Table accessible but no record for model {model!r}")
    except dynamo.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        print(f"[!] {table}: {code} — {e.response['Error']['Message']}")
    except Exception as e:
        print(f"[!] {table}: {e}")

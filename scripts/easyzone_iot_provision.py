#!/usr/bin/env python3
"""
EasyZone RV - AWS IoT provisioning script
Authenticates with Cognito, discovers the IoT endpoint, and provisions
a new certificate + key pair for use with Home Assistant (or any MQTT client).

Usage:
    python3 easyzone_iot_provision.py --username <email> --password <password>
    python3 easyzone_iot_provision.py --username <email> --password <password> --client-id <id> --client-secret <secret>

Output files (saved to ./certs/):
    ca.pem          Amazon Root CA
    client.crt      IoT client certificate
    client.key      Private key
    endpoint.txt    MQTT broker hostname
    topics.txt      Observed topic prefix (if discoverable)

Requirements:
    pip install boto3 requests
"""

import argparse
import os
import sys
import urllib.request

import boto3
from botocore.exceptions import ClientError
from pycognito import Cognito

# ── App constants extracted from APK (resources.arsc) ────────────────────────
REGION            = "us-east-1"
USER_POOL_ID      = "us-east-1_M9Hs9kugG"
APP_CLIENT_ID     = "2mfujqa7vidd2td9h08k9m061s"
APP_CLIENT_SECRET = "bbq2v6edtn25j0pgdus7tsi4fq93rgiqfq0g0ener8m896o2euu"
IDENTITY_POOL_ID  = "us-east-1:527ec272-ac1b-4301-8e46-464669790d6e"
IOT_ENDPOINT      = "al2tvpwq2i0lf-ats.iot.us-east-1.amazonaws.com"
IOT_POLICY_NAME   = "MyAndroidPolicy"

AMAZON_ROOT_CA_URL = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
CERT_DIR = "certs"

# ── Helpers ───────────────────────────────────────────────────────────────────

REFRESH_TOKEN_FILE = "cognito_refresh.token"


def cognito_login(username: str, password: str) -> dict:
    """Authenticate via SRP, or silently refresh if a saved refresh token exists."""
    u = Cognito(
        user_pool_id=USER_POOL_ID,
        client_id=APP_CLIENT_ID,
        client_secret=APP_CLIENT_SECRET or None,
        username=username,
    )

    if os.path.exists(REFRESH_TOKEN_FILE):
        print(f"[*] Refreshing session from saved token ...")
        try:
            with open(REFRESH_TOKEN_FILE) as f:
                u.refresh_token = f.read().strip()
            u.renew_access_token()
            print(f"[+] Session refreshed.")
        except Exception as e:
            print(f"[~] Refresh failed ({e}), falling back to password auth ...")
            os.remove(REFRESH_TOKEN_FILE)
            return cognito_login(username, password)
    else:
        print(f"[*] Authenticating as {username} ...")
        try:
            u.authenticate(password=password)
        except Exception as e:
            sys.exit(f"[!] Cognito auth failed: {e}")
        print(f"[+] Authenticated via SRP.")
        with open(REFRESH_TOKEN_FILE, "w") as f:
            f.write(u.refresh_token)
        print(f"[+] Refresh token saved to {REFRESH_TOKEN_FILE} (valid ~30 days).")

    return {
        "IdToken":      u.id_token,
        "AccessToken":  u.access_token,
        "RefreshToken": u.refresh_token,
        "TokenType":    "Bearer",
    }


def get_identity_credentials(id_token: str) -> dict:
    """Exchange Cognito ID token for temporary AWS credentials via Identity Pool."""
    cognito_identity = boto3.client("cognito-identity", region_name=REGION)

    # Step 1: get an Identity ID
    login_key = f"cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"
    id_resp = cognito_identity.get_id(
        IdentityPoolId=IDENTITY_POOL_ID,
        Logins={login_key: id_token},
    )
    identity_id = id_resp["IdentityId"]
    print(f"[+] Identity ID: {identity_id}")

    # Step 2: exchange for credentials
    cred_resp = cognito_identity.get_credentials_for_identity(
        IdentityId=identity_id,
        Logins={login_key: id_token},
    )
    creds = cred_resp["Credentials"]
    print(f"[+] Got temporary AWS credentials (expire: {creds['Expiration']})")
    return creds, identity_id


def iot_client_from_creds(creds: dict):
    """Build a boto3 IoT client using temporary credentials."""
    return boto3.client(
        "iot",
        region_name=REGION,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretKey"],
        aws_session_token=creds["SessionToken"],
    )


def get_iot_endpoint(iot) -> str:
    try:
        resp = iot.describe_endpoint(endpointType="iot:Data-ATS")
        endpoint = resp["endpointAddress"]
        print(f"[+] IoT endpoint: {endpoint}")
        return endpoint
    except ClientError as e:
        print(f"[~] DescribeEndpoint failed: {e.response['Error']['Message']}")
        print(f"[~] Using known endpoint from APK: {IOT_ENDPOINT}")
        return IOT_ENDPOINT


def discover_iot_policy(iot, identity_id: str) -> str:
    """
    Try to find the IoT policy name by listing policies attached to the
    Cognito identity principal. Falls back to the known policy from the APK.
    """
    try:
        resp = iot.list_attached_policies(target=identity_id)
        policies = resp.get("policies", [])
        if policies:
            name = policies[0]["policyName"]
            print(f"[+] Found attached IoT policy: {name}")
            return name
    except ClientError as e:
        print(f"[~] Could not list policies: {e.response['Error']['Message']}")
    print(f"[~] Using known policy from APK: {IOT_POLICY_NAME}")
    return IOT_POLICY_NAME


def provision_certificate(iot, identity_id: str, policy_name: str | None) -> dict:
    """
    Create a new IoT certificate + key pair and attach the policy.
    Returns a dict with cert/key PEM strings.
    """
    print("[*] Creating new IoT certificate ...")
    resp = iot.create_keys_and_certificate(setAsActive=True)
    cert_arn  = resp["certificateArn"]
    cert_id   = resp["certificateId"]
    cert_pem  = resp["certificatePem"]
    key_pem   = resp["keyPair"]["PrivateKey"]

    print(f"[+] Certificate ARN: {cert_arn}")
    print(f"[+] Certificate ID:  {cert_id}")

    if policy_name:
        print(f"[*] Attaching policy '{policy_name}' to certificate ...")
        try:
            iot.attach_policy(policyName=policy_name, target=cert_arn)
            print(f"[+] Policy attached to certificate.")
        except ClientError as e:
            print(f"[~] Could not attach policy to cert: {e.response['Error']['Message']}")
            print("    You may need to attach it manually in the AWS IoT console.")
    else:
        print("[~] No policy name known — skipping policy attachment.")
        print("    Attach a policy manually in AWS IoT Console > Security > Certificates.")

    # Also try attaching to the identity principal (some app setups use this)
    if policy_name:
        try:
            iot.attach_policy(policyName=policy_name, target=identity_id)
            print(f"[+] Policy also attached to Cognito identity.")
        except ClientError:
            pass  # Not always needed

    return {"cert_pem": cert_pem, "key_pem": key_pem, "cert_id": cert_id, "cert_arn": cert_arn}


def fetch_root_ca() -> str:
    print("[*] Fetching Amazon Root CA ...")
    with urllib.request.urlopen(AMAZON_ROOT_CA_URL) as r:
        return r.read().decode()


def save_outputs(endpoint: str, cert_data: dict, root_ca: str, identity_id: str):
    os.makedirs(CERT_DIR, exist_ok=True)

    files = {
        "ca.pem":       root_ca,
        "client.crt":   cert_data["cert_pem"],
        "client.key":   cert_data["key_pem"],
        "endpoint.txt": endpoint + "\n",
    }
    for name, content in files.items():
        path = os.path.join(CERT_DIR, name)
        with open(path, "w") as f:
            f.write(content)
        print(f"[+] Saved {path}")

    # Print Home Assistant MQTT broker config snippet
    topic_hint = f"<serial>/cmd  and  <serial>/status"
    print(f"""
╔══════════════════════════════════════════════════════════════════════════╗
║  Home Assistant MQTT broker config (configuration.yaml)                 ║
╠══════════════════════════════════════════════════════════════════════════╣

mqtt:
  broker: {endpoint}
  port: 8883
  certificate: /config/certs/ca.pem
  client_cert: /config/certs/client.crt
  client_key: /config/certs/client.key
  client_id: ha-easyzone-{identity_id[-8:]}

# Topics (capture from app traffic to confirm your device serial):
#   Subscribe: <serial>/status  (or prefix/<serial>/status)
#   Publish:   <serial>/cmd     (or prefix/<serial>/cmd)
#
# Example status payload:
#   {{"mode":"cool_mode","power":"On","fan":"fan_auto","cool_sp":72,"ambient":76}}
#
# Example command payload:
#   {{"Type":"Change","Changes":{{"zone":0,"cool_sp":70}}}}
#   {{"Type":"Change","Changes":{{"zone":0,"mode":"cool_mode"}}}}
#   {{"Type":"Get Status","Zone":0}}

╚══════════════════════════════════════════════════════════════════════════╝

Certificate ID (save this): {cert_data['cert_id']}
""")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="EasyZone RV - AWS IoT provisioning")
    parser.add_argument("--username", required=True, help="App account email/username")
    parser.add_argument("--password", required=True, help="App account password")
    parser.add_argument("--client-id", default=None, help="Cognito app client ID (overrides APK default)")
    parser.add_argument("--client-secret", default=None, help="Cognito app client secret (overrides APK default)")
    args = parser.parse_args()

    if args.client_id:
        global APP_CLIENT_ID, APP_CLIENT_SECRET
        APP_CLIENT_ID = args.client_id
        APP_CLIENT_SECRET = args.client_secret or ""
        print(f"[*] Using client ID: {APP_CLIENT_ID}")

    # 1. Cognito login
    tokens = cognito_login(args.username, args.password)
    id_token = tokens["IdToken"]

    # 2. Get identity credentials
    creds, identity_id = get_identity_credentials(id_token)

    # 3. Build IoT client with those creds
    iot = iot_client_from_creds(creds)

    # 4. Discover endpoint
    endpoint = get_iot_endpoint(iot)

    # 5. Try to find the IoT policy name
    policy_name = discover_iot_policy(iot, identity_id)

    # 6. Provision a new certificate
    cert_data = provision_certificate(iot, identity_id, policy_name)

    # 7. Fetch root CA and save everything
    root_ca = fetch_root_ca()
    save_outputs(endpoint, cert_data, root_ca, identity_id)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
EasyZone RV - Device status watcher
Connects to AWS IoT and watches live status messages for a given device serial.

Usage:
    python3 watch_device.py --serial "352016109"
    python3 watch_device.py --serial "352016109" --request-status
    python3 watch_device.py --serial "352016364" --request-config
    python3 watch_device.py --serial "352016364" --request-config --request-config-zone 0

Options:
    --serial               Device serial number (required)
    --request-status       Send a Get Status request on connect to trigger an immediate update
    --request-config       Send a zoneless Get Config on connect (tests full CFG block with MAV/FA/SPL)
    --request-config-zone  Also send a per-zone Get Config for the given zone number (0-3)
    --certs-dir            Directory containing ca.pem, client.crt, client.key (default: ./certs)
    --endpoint             Override MQTT endpoint (default: read from certs/endpoint.txt)

Requirements:
    pip install paho-mqtt
"""

import argparse
import json
import os
import ssl
import sys
import time
from datetime import datetime

import paho.mqtt.client as mqtt

# ── Defaults ──────────────────────────────────────────────────────────────────
CERTS_DIR   = "certs"
MQTT_PORT   = 8883
CLIENT_ID   = "easyzone-watcher"

# Known field descriptions for pretty-printing status payloads
FIELD_LABELS = {
    "mode":     "Mode",
    "power":    "Power",
    "fan":      "Fan",
    "cool_sp":  "Cool setpoint",
    "heat_sp":  "Heat setpoint",
    "auto_sp":  "Auto setpoint",
    "sched_sp": "Schedule setpoint",
    "dry_sp":   "Dry setpoint",
    "ambient":  "Ambient temp",
    "ags":      "Auto gen start",
    "stg_en":   "Staging enabled",
    "light":    "Light",
    "push":     "Push notifications",
}


def load_endpoint(certs_dir: str, override: str | None) -> str:
    if override:
        return override
    path = os.path.join(certs_dir, "endpoint.txt")
    if not os.path.exists(path):
        sys.exit(f"[!] No endpoint.txt found in {certs_dir}. Run easyzone_iot_provision.py first.")
    return open(path).read().strip()


def build_tls_context(certs_dir: str) -> ssl.SSLContext:
    ca   = os.path.join(certs_dir, "ca.pem")
    cert = os.path.join(certs_dir, "client.crt")
    key  = os.path.join(certs_dir, "client.key")
    for f in [ca, cert, key]:
        if not os.path.exists(f):
            sys.exit(f"[!] Missing cert file: {f}. Run easyzone_iot_provision.py first.")
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca)
    ctx.load_cert_chain(certfile=cert, keyfile=key)
    return ctx


def pretty_print_message(topic: str, payload: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n[{ts}] topic: {topic}")
    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            # Print known fields with labels first, then any unknown fields
            printed = set()
            for key, label in FIELD_LABELS.items():
                if key in data:
                    print(f"  {label:<22} {data[key]}")
                    printed.add(key)
            for key, val in data.items():
                if key not in printed:
                    print(f"  {key:<22} {val}")
        else:
            print(f"  {data}")
    except json.JSONDecodeError:
        print(f"  {payload}")


def main():
    parser = argparse.ArgumentParser(description="EasyZone RV - Device status watcher")
    parser.add_argument("--serial",               required=True, help='Device serial number, e.g. "352016109"')
    parser.add_argument("--request-status",       action="store_true", help="Send Get Status on connect")
    parser.add_argument("--request-config",       action="store_true", help="Send zoneless Get Config on connect (triggers full MAV/FA/SPL response)")
    parser.add_argument("--request-config-zone",  type=int, default=None, metavar="ZONE", help="Also send a per-zone Get Config for zone 0-3")
    parser.add_argument("--certs-dir",            default=CERTS_DIR, help=f"Certs directory (default: {CERTS_DIR})")
    parser.add_argument("--endpoint",             default=None, help="Override MQTT endpoint")
    args = parser.parse_args()

    endpoint = load_endpoint(args.certs_dir, args.endpoint)
    tls_ctx  = build_tls_context(args.certs_dir)

    # Topic: "EasyTouch <serial>" — subscribe to it and all subtopics
    base_topic = f"EasyTouch {args.serial}"
    topics = [
        (base_topic,        0),
        (base_topic + "/#", 0),
    ]

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            print(f"[!] Connection failed: reason code {reason_code}")
            return
        print(f"[+] Connected to {endpoint}:{MQTT_PORT}")
        for topic, qos in topics:
            client.subscribe(topic, qos)
            print(f"[+] Subscribed: {topic}")

        if args.request_status:
            payload = json.dumps({"Type": "Get Status", "Zone": 0})
            client.publish(base_topic, payload)
            print(f"[*] Sent Get Status request to {base_topic}")

        if args.request_config:
            payload = json.dumps({"Type": "Get Config"})
            client.publish(base_topic, payload)
            print(f"[*] Sent zoneless Get Config to {base_topic}")

        if args.request_config_zone is not None:
            payload = json.dumps({"Type": "Get Config", "Zone": args.request_config_zone})
            client.publish(base_topic, payload)
            print(f"[*] Sent Get Config Zone={args.request_config_zone} to {base_topic}")

    def on_message(client, userdata, msg):
        pretty_print_message(msg.topic, msg.payload.decode("utf-8", errors="replace"))

    def on_disconnect(client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            print(f"\n[!] Unexpectedly disconnected (code {reason_code}), reconnecting ...")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID)
    client.tls_set_context(tls_ctx)
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    print(f"[*] Connecting to {endpoint}:{MQTT_PORT} ...")
    print(f"[*] Watching device: {base_topic}")
    print(f"[*] Press Ctrl+C to quit\n")

    try:
        client.connect(endpoint, MQTT_PORT, keepalive=60)
        client.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        print("\n[*] Disconnecting ...")
        client.disconnect()


if __name__ == "__main__":
    main()

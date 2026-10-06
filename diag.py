# -*- coding: utf-8 -*-
"""TEMPORARY diagnostic: what does the host provide for TLS trust?

Added to work out how the deployed app should find a CA bundle. Delete this
file and its route once the fix is settled.
"""

import os
import ssl
import sys

# Paths a CA bundle conventionally lives at.
CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",   # Debian / Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",     # RHEL / Fedora
    "/etc/ssl/cert.pem",                    # Alpine / macOS
    "/usr/local/etc/openssl/cert.pem",
    "/etc/ssl/certs",
    "cacert.pem",                           # next to the app
)


def report():
    paths = ssl.get_default_verify_paths()
    out = {
        "python": sys.version.split()[0],
        "openssl": ssl.OPENSSL_VERSION,
        "default_verify_paths": {
            "cafile": paths.cafile,
            "capath": paths.capath,
            "openssl_cafile": paths.openssl_cafile,
            "openssl_capath": paths.openssl_capath,
            "cafile_env": paths.openssl_cafile_env,
            "capath_env": paths.openssl_capath_env,
        },
        "env": {k: os.environ.get(k) for k in
                ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")},
        "candidates": [],
    }

    for path in CANDIDATES:
        entry = {"path": path, "exists": os.path.exists(path)}
        if entry["exists"]:
            entry["is_dir"] = os.path.isdir(path)
            if not entry["is_dir"]:
                try:
                    entry["bytes"] = os.path.getsize(path)
                except OSError as exc:
                    entry["bytes"] = str(exc)
        out["candidates"].append(entry)

    try:
        import certifi  # noqa: PLC0415 - optional, only present sometimes
        out["certifi"] = {
            "available": True,
            "where": certifi.where(),
            "exists": os.path.exists(certifi.where()),
        }
    except Exception as exc:  # noqa: BLE001
        out["certifi"] = {"available": False, "error": "%s: %s" % (type(exc).__name__, exc)}

    try:
        out["cwd"] = os.getcwd()
        out["cwd_listing"] = sorted(os.listdir("."))
    except Exception as exc:  # noqa: BLE001
        out["cwd"] = "unavailable: %s" % exc

    # What does a default context actually load?
    try:
        ctx = ssl.create_default_context()
        stats = ctx.cert_store_stats()
        out["default_context"] = {
            "loaded_x509": stats.get("x509"),
            "verify_mode": ctx.verify_mode.name,
        }
    except Exception as exc:  # noqa: BLE001
        out["default_context"] = {"error": "%s: %s" % (type(exc).__name__, exc)}

    return out

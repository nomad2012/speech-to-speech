#!/usr/bin/env python3
"""Generate a self-signed TLS certificate for local development.

Usage:
    python scripts/generate_cert.py              # writes cert/cert.pem and cert/key.pem
    python scripts/generate_cert.py --days 365    # custom validity (default 365)
    python scripts/generate_cert.py --country US  # custom subject (default /CN=localhost)

The certificate is trusted by browsers running on the same machine via
`localhost` / `127.0.0.1`. For remote LAN access you must either:
  • add the generated CA to your system/browser trust store, or
  • use a tool like [mkcert](https://github.com/FiloSottile/mkcert) which
    installs a local CA automatically.
"""

import argparse
import ipaddress
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    print("Install with: pip install cryptography", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Generate a self-signed cert for local dev.")
    parser.add_argument("--days", type=int, default=365, help="Certificate validity in days (default 365)")
    parser.add_argument("--country", default="US", help="Subject country (default US)")
    parser.add_argument("--output-dir", default="cert", help="Directory to write cert.pem and key.pem")
    args = parser.parse_args()

    # Generate private key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Build certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, args.country),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=args.days))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName("*.localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    cert_path = out / "cert.pem"
    key_path = out / "key.pem"

    # Write cert
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    # Write key (no passphrase for convenience)
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    print(f"Generated {cert_path} and {key_path}")
    print(f"Start the server with:")
    print(f"  uvicorn --app-dir demo server:app "
          f"--ssl-certfile {cert_path} --ssl-keyfile {key_path} --port 443")


if __name__ == "__main__":
    main()

"""
Canonical HMAC-SHA256 signing for Journiv backend -> Journiv Plus requests.

This module provides the shared signing primitive used by PlusServerClient.
It generates signatures over a canonical message format using the per-instance
secret obtained from the Journiv Plus registration handshake.

All signed requests include:
- X-Journiv-Install-ID (request header)
- X-Journiv-Timestamp (request header, replay protection)
- X-Journiv-Signature (HMAC-SHA256 over canonical message)

The signature is computed over:
- Protocol version
- HTTP method
- Request path
- Timestamp
- SHA256 hash of canonical JSON body
"""

import hmac
import hashlib
import json
from typing import Any, Dict


def generate_canonical_signature(
    *,
    method: str,
    path: str,
    timestamp: int,
    body: Dict[str, Any],
    secret: str,
) -> str:
    """
    Generate HMAC-SHA256 signature using canonical request format.

    Canonical format:
        JOURNIV-HMAC-V1
        <HTTP_METHOD>
        <PATH>
        <TIMESTAMP>
        <SHA256_HEX_OF_CANONICAL_JSON_BODY>

    This format is used for license-related and instance requests and provides:
    - Method + path binding (prevents request reuse across endpoints)
    - Body integrity (SHA256 hash of canonical JSON)
    - Timestamp for replay protection

    Args:
        method: HTTP method (e.g., "POST")
        path: Request path (e.g., "/api/v1/instance/version/check")
        timestamp: Unix timestamp in seconds
        body: Request body dictionary (will be canonicalized). Must be JSON-serializable.
        secret: Per-instance secret obtained from Journiv Plus registration

    Returns:
        Hex-encoded HMAC-SHA256 signature (64 characters)

    Raises:
        ValueError: If method or path contains newline characters, or if body contains non-JSON-serializable values

    Security Notes:
        - Uses per-instance secrets stored in the local database
        - Journiv Plus validates with constant-time comparison
        - Timestamp prevents replay attacks (validated server-side)
        - Body hash ensures request integrity
    """

    if '\n' in method or '\r' in method:
        raise ValueError(
            f"HTTP method contains invalid newline characters: {repr(method)}"
        )
    if '\n' in path or '\r' in path:
        raise ValueError(
            f"Request path contains invalid newline characters: {repr(path)}"
        )

    # Canonicalize the body: sort keys, no whitespace
    try:
        canonical_body = json.dumps(body, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"request body contains non-JSON-serializable value: {str(e)}"
        ) from e

    # Compute SHA256 hash of canonical body
    body_hash = hashlib.sha256(canonical_body.encode('utf-8')).hexdigest()

    # Build canonical message
    canonical_message = f"JOURNIV-HMAC-V1\n{method}\n{path}\n{timestamp}\n{body_hash}"

    # Compute HMAC-SHA256
    signature = hmac.new(
        secret.encode('utf-8'),
        canonical_message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return signature

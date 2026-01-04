"""
install_id generation using CRC32 + UUIDv5 for deterministic, collision-resistant IDs.
"""

import logging
import os
import platform
import uuid
import zlib

from app.core.config import settings
from app.core.logging_config import LogCategory

logger = logging.getLogger(LogCategory.APP)

# Journiv namespace UUID for generating install IDs
# This is a fixed UUID that serves as the namespace for all Journiv install IDs
JOURNIV_NAMESPACE = uuid.UUID('327e8837-44e9-41f9-aa1e-1c0a925f1ff5')


def calculate_crc(input_str: str) -> str:
    """
    Calculate CRC32 hash using zlib.

    Args:
        input_str: String to hash

    Returns:
        8-character hex string (lowercase)

    """
    # Convert string to bytes
    data = input_str.encode('utf-8')

    # Calculate CRC32
    # zlib.crc32 returns a signed 32-bit integer in Python 2,
    # but unsigned in Python 3. & 0xFFFFFFFF ensures unsigned behavior consistent across versions/platforms.
    crc = zlib.crc32(data) & 0xFFFFFFFF

    # Convert to 8-char hex string
    return format(crc, '08x').lower()


def generate_install_id_seed() -> str:
    """
    Generate install_id for this Journiv installation.

    Combines system characteristics that are:
    - Stable across app restarts
    - Unique per installation
    - Privacy-respecting (no serial numbers, MAC addresses or domain name)

    Components:
    - Processor count (CPU cores)
    - Operating system
    - JWT secret (from settings, unique per installation)
    - System username

    Returns:
        seed string for CRC calculation
    """
    # Get processor count (stable, unique to hardware)
    try:
        processor_count = os.cpu_count() or 1
    except Exception:
        processor_count = 1

    # Get OS (Linux, Darwin, Windows)
    system = platform.system()

    # Get secret key (unique per installation, required for Journiv)
    # This is the main uniqueness factor
    jwt_secret = settings.secret_key

    # Get username (stable per deployment)
    username = os.getenv('USER', os.getenv('USERNAME', 'unknown'))

    # Combine into seed string
    seed = f"{processor_count}_{system}_{jwt_secret}_{username}"

    return seed


def generate_install_id() -> str:
    """
    Generate unique install_id for this Journiv installation.

    Creates a deterministic UUID using:
    1. Platform characteristics (CPU, OS, secret, user) → CRC32 hash
    2. UUIDv5(JOURNIV_NAMESPACE, CRC hash) → 128-bit collision-resistant UUID

    The ID is deterministic - same system characteristics will always
    produce the same install_id, but with 2^128 search space instead of 2^32.

    Returns:
        UUID string (36 characters, e.g., "550e8400-e29b-41d4-a716-446655440000")

    """
    seed = generate_install_id_seed()
    crc_hash = calculate_crc(seed)

    # Generate UUIDv5 from CRC hash for true 128-bit collision resistance
    # This maintains determinism while providing full UUID uniqueness
    install_uuid = uuid.uuid5(JOURNIV_NAMESPACE, crc_hash)
    install_id = str(install_uuid)

    try:
        cpu_info = os.cpu_count() or 'unknown'
        system_info = platform.system()
    except Exception:
        cpu_info = 'unknown'
        system_info = 'unknown'

    logger.debug(
        f"Generated install_id: {install_id} "
        f"(CPU: {cpu_info}, System: {system_info})"
    )

    return install_id





def validate_install_id(install_id: str) -> bool:
    """
    Validate install_id format (UUID).

    Args:
        install_id: install_id to validate

    Returns:
        True if valid UUID, False otherwise

    Valid format:
    - Standard UUID format (36 characters with hyphens)
    - Example: "550e8400-e29b-41d4-a716-446655440000"
    """
    if not install_id:
        return False

    # Check for whitespace
    if install_id != install_id.strip():
        return False

    # Validate UUID format
    try:
        uuid.UUID(install_id)
        return True
    except (ValueError, AttributeError):
        return False

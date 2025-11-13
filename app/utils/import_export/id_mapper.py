"""
ID mapping utilities for import operations.

During import, UUIDs from external systems need to be remapped to new UUIDs
to avoid conflicts with existing data.
"""
import uuid
from typing import Dict, Optional


class IDMapper:
    """
    Maps old IDs to new UUIDs during import.

    This is critical because:
    1. Imported data may have UUIDs that conflict with existing data
    2. Foreign key relationships need to be preserved
    3. We need to track which external IDs map to which internal IDs
    """

    def __init__(self):
        """Initialize the ID mapper with empty mappings."""
        self._mappings: Dict[str, uuid.UUID] = {}

    def map(self, old_id: Optional[str]) -> uuid.UUID:
        """
        Map an old ID to a new UUID.

        If the old_id has been seen before, returns the same UUID.
        Otherwise, generates a new UUID and stores the mapping.

        Args:
            old_id: Original ID from source system (can be UUID string or any unique ID)

        Returns:
            New UUID for use in the target system
        """
        if old_id is None:
            # Generate a new UUID for null IDs
            return uuid.uuid4()

        # Convert to string for consistent mapping
        old_id_str = str(old_id)

        # Return existing mapping if available
        if old_id_str in self._mappings:
            return self._mappings[old_id_str]

        # Generate new UUID and store mapping
        new_id = uuid.uuid4()
        self._mappings[old_id_str] = new_id
        return new_id

    def get(self, old_id: str) -> Optional[uuid.UUID]:
        """
        Get the mapped UUID for an old ID without creating a new one.

        Args:
            old_id: Original ID from source system

        Returns:
            Mapped UUID if it exists, None otherwise
        """
        return self._mappings.get(str(old_id))

    def has(self, old_id: str) -> bool:
        """
        Check if an old ID has been mapped.

        Args:
            old_id: Original ID from source system

        Returns:
            True if the ID has been mapped, False otherwise
        """
        return str(old_id) in self._mappings

    def record(self, old_id: Optional[str], new_id: uuid.UUID):
        """
        Record an explicit mapping for a known new UUID.

        Args:
            old_id: Original ID from source system
            new_id: Newly created UUID in the target system
        """
        if old_id is None:
            return
        self._mappings[str(old_id)] = new_id

    def clear(self):
        """Clear all mappings."""
        self._mappings.clear()

    def size(self) -> int:
        """Get the number of mapped IDs."""
        return len(self._mappings)

    def get_all_mappings(self) -> Dict[str, uuid.UUID]:
        """
        Get all ID mappings.

        Returns:
            Dictionary of old_id -> new_uuid mappings
        """
        return self._mappings.copy()

    def as_string_mapping(self) -> Dict[str, str]:
        """Get mappings with UUIDs serialized as strings."""
        return {old: str(new_id) for old, new_id in self._mappings.items()}

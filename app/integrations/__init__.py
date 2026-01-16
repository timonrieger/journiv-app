"""
Integrations module for connecting Journiv to external services.

This module provides a modular system for integrating with self-hosted applications
like Immich, Jellyfin, and Audiobookshelf.

Architecture:
- app/models/integration.py: Database models for integrations and cached data
- schemas.py: Pydantic schemas for API requests/responses
- router.py: FastAPI endpoints for integration management
- service.py: Business logic and provider orchestration
- tasks.py: Background sync tasks
- {provider}.py: Provider-specific client implementations (immich, jellyfin, audiobookshelf)

Design Principles:
- Each user connects with their own credentials (API keys/tokens)
- Base URLs can be shared (family server) or per-user
- Tokens are encrypted at rest using Fernet
- Background sync runs periodically to cache metadata
- Modular provider registry for easy extension

Future Extensions:
- Add new providers by creating a {provider}.py module
- Implement connect(), list_assets(), and sync() functions
- Register in PROVIDER_REGISTRY in service.py
- No changes to core architecture required
"""

from app.models.integration import Integration, IntegrationProvider

__all__ = [
    "Integration",
    "IntegrationProvider",
]

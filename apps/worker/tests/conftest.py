"""Worker test configuration.

Registers every ORM mapper before any test module is imported. See
``eip/models.py`` — the relay writes audit events, and ``AuditEvent``'s foreign
key cannot resolve unless the identity models are loaded as well.
"""

import eip.models  # noqa: F401

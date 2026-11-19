"""Constants for the CookieNVR integration."""

import logging
from datetime import timedelta

DOMAIN = "cookienvr"
LOGGER = logging.getLogger(__package__)

CONF_URL = "url"
CONF_TOKEN = "token"
CONF_VERIFY_SSL = "verify_ssl"

# Range of CookieNVR `apiVersion` this integration understands.
API_VERSION_MIN = 1
API_VERSION_MAX = 1

POLL_INTERVAL = timedelta(seconds=60)
SSE_BACKOFF_MIN = 1
SSE_BACKOFF_MAX = 30
# The NVR sends a ping comment every 20 s; a silent socket this long is dead.
SSE_READ_TIMEOUT = 45
REQUEST_TIMEOUT = 15

EVENT_COOKIENVR = "cookienvr_event"

ISSUE_NVR_OUTDATED = "nvr_outdated"
ISSUE_INTEGRATION_OUTDATED = "integration_outdated"

PTZ_NUDGE_SPEED = 0.5
PTZ_NUDGE_SECONDS = 0.3
EXPORT_TIMEOUT = 15 * 60
EXPORT_POLL_SECONDS = 1.0

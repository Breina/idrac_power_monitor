"""Client factory for Redfish and legacy IPMI iDRAC backends."""

from requests.exceptions import RequestException

from .idrac_rest import IdracMock, IdracRest, InvalidAuth
from .legacy_ipmi import IdracLegacyIpmi


def create_client(host: str, username: str, password: str, interval: int):
    """Create the best supported client for the target iDRAC."""
    if host == "MOCK":
        return IdracMock(host, username, password, interval)

    redfish = IdracRest(host, username, password, interval)

    try:
        result = redfish.get_path("/redfish/v1")
        if result.status_code == 200:
            # Validate the Redfish path with a real data call so setup errors
            # surface here rather than later during entity creation.
            redfish.get_device_info()
            return redfish
        if result.status_code in (401, 403):
            raise InvalidAuth()
    except InvalidAuth:
        raise
    except RequestException:
        # Fall through to legacy IPMI probing.
        pass

    legacy = IdracLegacyIpmi(host, username, password, interval)
    legacy.get_device_info()
    return legacy

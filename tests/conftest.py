"""Shared fixtures."""
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


LOG_PATHS = (
    'https://10.0.0.2/redfish/v1/Managers/iDRAC.Embedded.1/LogServices',
    'https://10.0.0.2/redfish/v1/Managers/iDRAC.Embedded.1/Logs/Sel',
)


def no_event_log(aioclient_mock) -> None:
    """Answer 404 for the System Event Log, as an iDRAC without a readable one does.

    Register after a test's own resources: the mocker answers with the first match.
    """
    for url in LOG_PATHS:
        aioclient_mock.get(url, status=404, text='')

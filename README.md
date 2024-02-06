# iDrac power monitor

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)

This integration will grab the power usage from your Dell servers' iDrac system.

For this to work, the Redfish service must be running on it.

## Installation

1. Install this integration with HACS, or copy the contents of this
repository into the `custom_components/idrac_power` directory
2. Restart HA
3. Go to `Configuration` -> `Integrations` and click the `+ Add Integration` 
button
4. Select `iDrac power monitor` from the list
5. Enter the IP address or hostname (NO `http://` !) of your iDrac instance, its username (`root` by default) and its password (`calvin` by default).


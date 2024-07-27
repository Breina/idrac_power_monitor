# iDRAC power monitor

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)

This integration will grab infomations from your Dell servers' iDRAC system :

- Server status
- Power consumption
- CPU and air temperature
- Fan speed

You can also start and shutdown the server from Home Assistant.

For this to work, the Redfish service must be running on it.

Tested on iDRAC 7 and 8 on multiple Dell PowerEdge servers.

## Screenshots

![Alt text](imgs/entities.png)

## Installation

1. Install this integration with HACS, or copy the contents of this
   repository into the `custom_components/idrac_power` directory
2. Restart HA
3. Go to `Configuration` -> `Integrations` and click the `+ Add Integration`
   button
4. Select `iDRAC power monitor` from the list
5. Enter the IP address or hostname (NO `http://` !) of your iDRAC instance, its username (`root` by default) and its password (`calvin` by default).

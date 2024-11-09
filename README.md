# iDRAC power monitor

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/Breina/idrac_power_monitor/validate.yaml)
![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/Breina/idrac_power_monitor/hassfest.yaml)

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

> **Note**
> 
> This integration requires [HACS](https://github.com/hacs/integration) to be installed

1. Open HACS
2. `+ EXPLORE & DOWNLOAD REPOSITORIES`
3. Find `iDRAC power monitor` in this list
4. `DOWNLOAD THIS REPOSITORY WITH HACS`
5. `DOWNLOAD`
6. Restart Home Assistant (_Settings_ > _System_ > _RESTART_)
7. Go to `Configuration` -> `Integrations` and click the `+ Add Integration`
   button
8. Select `iDRAC power monitor` from the list
9. Enter the IP address or hostname (NO `http://` !) of your iDRAC instance, its username (`root` by default) and its password (`calvin` by default).

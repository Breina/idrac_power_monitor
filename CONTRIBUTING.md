# Contributing

## Layout

```
custom_components/idrac_power/
  client.py        data model (IdracData, PowerSupply, Alert…), errors, the IdracClient interface
  redfish.py       iDRAC 7/8/9 over Redfish (energy falls back on the web APIs)
  legacy.py        iDRAC 6 (and 7/8 without Redfish) over the web GUI's /data API
  mock.py          a fake iDRAC: add an entry with MOCK as host
  factory.py       picks the API a host speaks and builds its client
  coordinator.py   one poll per interval for all entities of an iDRAC; event log tracking
  entity.py        base entities, and adding entities as the iDRAC starts reporting them
  sensor.py, binary_sensor.py
                   entity descriptions: server-wide ones and one per fan / probe / PSU
  button.py, switch.py, event.py
  diagnostics.py   the diagnostics download
tests/             pytest with Home Assistant's test harness; captured iDRAC answers in fixtures/
scripts/           idrac_probe.py, to query an iDRAC outside Home Assistant
```

Entity unique ids are `<service tag>_<model>_<suffix>` and must never change: users' entity ids and history hang on them.

## Tests

The tests need Python 3.14 (what `requirements_test.txt` pins Home Assistant to) on Linux or macOS. On Windows, use WSL.

```
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```

Lint with `ruff check` (configured in `pyproject.toml`).

Please add a test for what you fix, ideally with the iDRAC's real answer: the debug log prints them.

## Releasing

Actions > **Release** > _Run workflow_ on `master`, with the version (for example `1.9.0`). It runs the tests, hassfest and the HACS check, then sets that version in `manifest.json`, commits it, tags `v1.9.0` and publishes the release with generated notes. Edit the notes afterwards if needed.

Don't push version tags by hand: a tag that moves or is deleted turns its release back into a draft, which HACS does not offer.

"""Query an iDRAC with the integration's own clients and print what they see.

Usage (from the repository root, in an environment with Home Assistant installed):
    python scripts/idrac_probe.py HOST USER PASSWORD [--api redfish|legacy] [--raw]

--raw also prints the XML the legacy (/data) API returns, which is what to
attach to an issue when an iDRAC 6 reading is missing or wrong. Credentials
never appear in the output.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.idrac_power.client import create_ssl_context  # noqa: E402
from custom_components.idrac_power.legacy import INFO_KEYS, POLL_KEYS, IdracLegacy  # noqa: E402
from custom_components.idrac_power.redfish import IdracRedfish  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('host')
    parser.add_argument('user')
    parser.add_argument('password')
    parser.add_argument('--api', choices=['redfish', 'legacy'])
    parser.add_argument('--raw', action='store_true')
    args = parser.parse_args()

    context = create_ssl_context()
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        for api in [args.api] if args.api else ['redfish', 'legacy']:
            cls = IdracRedfish if api == 'redfish' else IdracLegacy
            client = cls(session, context, args.host, args.user, args.password)
            print(f'=== {api}')
            try:
                print(await client.get_info())
                data = await client.fetch()
                for field, value in vars(data).items():
                    print(f'{field}: {value}')
                if args.raw and isinstance(client, IdracLegacy):
                    for keys in (INFO_KEYS, POLL_KEYS):
                        response = await session.post(f'{client.base_url}/data', params={'get': keys},
                                                      headers=client._headers(), ssl=context)
                        print(f'--- /data?get={keys}\n{await response.text()}')
                return 0
            except Exception as err:  # noqa: BLE001 - a diagnostic tool reports everything
                print(f'{type(err).__name__}: {err}')
            finally:
                await client.close()
    return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))

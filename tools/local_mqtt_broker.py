from __future__ import annotations

import asyncio
import logging
import os
import signal

from amqtt.broker import Broker


MQTT_PORT = int(os.getenv("AUTOFLEET_MQTT_PORT", "3889"))

CONFIG = {
    "listeners": {
        "default": {
            "type": "tcp",
            "bind": f"0.0.0.0:{MQTT_PORT}",
        }
    },
    "sys_interval": 10,
    "auth": {
        "allow-anonymous": True,
    },
    "topic-check": {
        "enabled": False,
    },
}


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    broker = Broker(CONFIG)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await broker.start()
    print(f"AutoFleet local MQTT broker listening on 0.0.0.0:{MQTT_PORT}", flush=True)
    try:
        await stop_event.wait()
    finally:
        await broker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

"""Single-process loopback launcher; Windows psycopg needs SelectorEventLoop."""
import argparse
import asyncio
import sys

import uvicorn

from .main import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=args.port,
                            access_log=False, proxy_headers=False)
    server = uvicorn.Server(config)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        runner.run(server.serve())


if __name__ == "__main__":
    main()

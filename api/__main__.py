"""Run the API: `uv run python -m api [--port 8000]`.

psycopg's async connections can't use Windows' default ProactorEventLoop, so on Windows the server
runs on a SelectorEventLoop. Elsewhere this is the same as `uvicorn api.main:app`.
"""

import argparse
import asyncio
import sys

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Sufra Ops Agent API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = uvicorn.Server(uvicorn.Config("api.main:app", host=args.host, port=args.port, loop="none"))
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(server.serve(), loop_factory=loop_factory)


if __name__ == "__main__":
    main()

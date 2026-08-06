"""Localhost-only TCP proxy for supervised IB connection-loss QA drills."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
from collections.abc import Sequence

LOOPBACK_HOST = "127.0.0.1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--upstream-host", default=LOOPBACK_HOST)
    parser.add_argument("--upstream-port", type=int, required=True)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if not ipaddress.ip_address(args.upstream_host).is_loopback:
        raise ValueError("IB fault proxy upstream must be a loopback address.")
    for label, port in {
        "listen port": args.listen_port,
        "upstream port": args.upstream_port,
    }.items():
        if not 1 <= port <= 65535:
            raise ValueError(f"{label} must be between 1 and 65535.")
    if args.listen_port == args.upstream_port:
        raise ValueError("listen port and upstream port must differ.")


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(64 * 1024):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass


async def _handle_connection(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    *,
    upstream_host: str,
    upstream_port: int,
) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(
            upstream_host,
            upstream_port,
        )
    except OSError:
        client_writer.close()
        await client_writer.wait_closed()
        return

    tasks = {
        asyncio.create_task(_relay(client_reader, upstream_writer)),
        asyncio.create_task(_relay(upstream_reader, client_writer)),
    }
    try:
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        for writer in (client_writer, upstream_writer):
            writer.close()
        await asyncio.gather(
            client_writer.wait_closed(),
            upstream_writer.wait_closed(),
            return_exceptions=True,
        )


async def _serve(args: argparse.Namespace) -> None:
    server = await asyncio.start_server(
        lambda reader, writer: _handle_connection(
            reader,
            writer,
            upstream_host=args.upstream_host,
            upstream_port=args.upstream_port,
        ),
        LOOPBACK_HOST,
        args.listen_port,
    )
    print(
        f"IB fault proxy ready on {LOOPBACK_HOST}:{args.listen_port} "
        f"-> {args.upstream_host}:{args.upstream_port}",
        flush=True,
    )
    async with server:
        await server.serve_forever()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _validate_args(args)
        asyncio.run(_serve(args))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

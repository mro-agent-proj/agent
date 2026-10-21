"""Offline administration commands. No credentials or caller context accepted from LLMs."""

import argparse
import asyncio
import json
import os

from .db import engine


async def run(args):
    database = engine(os.environ["DATABASE_URL"])
    try:
        if args.command == "ingest":
            from .ingest import ingest

            return await ingest(database, args.source)
        if args.command == "embed":
            from .embedding import build_embeddings

            return await build_embeddings(database, args.release, batch_size=args.batch_size)
    finally:
        await database.dispose()


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument(
        "--source", default=os.environ.get("MRO_SOURCE_DIR", "data/source/airbus-mcp-data-v1")
    )
    embed = commands.add_parser("embed")
    embed.add_argument("--release", required=True)
    embed.add_argument("--batch-size", type=int, default=64)
    commands.add_parser("serve")
    args = parser.parse_args()
    if args.command == "serve":
        from mro_mcp.rag.server import main as serve

        serve()
    else:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False))


if __name__ == "__main__":
    main()

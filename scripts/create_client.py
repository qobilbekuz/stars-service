#!/usr/bin/env python3
"""API mijoz va kalit yaratish CLI.

Ishlatish:
    python -m scripts.create_client --name "Mening botim" --webhook-url https://example.com/hook
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import (
    generate_api_key,
    generate_webhook_secret,
    hash_api_key,
    key_hint,
)
from app.db.session import SessionLocal, engine
from app.models.api_client import ApiClient


async def main() -> None:
    parser = argparse.ArgumentParser(description="Yangi API mijoz yaratish")
    parser.add_argument("--name", required=True, help="Mijoz nomi")
    parser.add_argument("--webhook-url", default=None, help="Webhook manzili")
    parser.add_argument("--provider", default=None, help="Majburiy provayder: mock|fragment|botapi")
    parser.add_argument("--rate-limit", type=int, default=None, help="Daqiqasiga so'rovlar limiti")
    parser.add_argument("--test", action="store_true", help="sk_test_ prefiksli kalit")
    args = parser.parse_args()

    api_key = generate_api_key(test=args.test)
    webhook_secret = generate_webhook_secret()

    async with SessionLocal() as db:
        client = ApiClient(
            name=args.name,
            key_hash=hash_api_key(api_key),
            key_hint=key_hint(api_key),
            webhook_url=args.webhook_url,
            webhook_secret=webhook_secret,
            forced_provider=args.provider,
            rate_limit_per_minute=args.rate_limit,
        )
        db.add(client)
        await db.commit()
        await db.refresh(client)

        print("\n✅ Mijoz yaratildi\n")
        print(f"  ID              : {client.id}")
        print(f"  Nomi            : {client.name}")
        print(f"  API kalit       : {api_key}")
        print(f"  Webhook secret  : {webhook_secret}")
        print("\n⚠️  API kalit boshqa ko'rsatilmaydi — hoziroq saqlab qo'ying!\n")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

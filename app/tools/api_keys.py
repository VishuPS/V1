import argparse
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.auth import issue_api_key
from app.config import get_settings
from app.db import SessionLocal
from app.models import ApiClient, ApiKey, MonthlyUsage


def parse_expiration(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("Expiration must include a timezone")
    return parsed


def require_client(session, identifier: str) -> ApiClient:
    client = session.scalar(
        select(ApiClient).where(ApiClient.identifier == identifier)
    )
    if client is None:
        raise SystemExit(f"Client not found: {identifier}")
    return client


def require_key(session, key_prefix: str) -> ApiKey:
    record = session.scalar(
        select(ApiKey)
        .options(joinedload(ApiKey.client))
        .where(ApiKey.key_prefix == key_prefix)
    )
    if record is None:
        raise SystemExit(f"API key not found: {key_prefix}")
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Administer developer API keys")
    commands = parser.add_subparsers(dest="command", required=True)

    create_client = commands.add_parser("create-client")
    create_client.add_argument("--identifier", required=True)
    create_client.add_argument("--name")
    create_client.add_argument("--plan", default="FREE")

    create_key = commands.add_parser("create-key")
    create_key.add_argument("--client", required=True)
    create_key.add_argument("--name")
    create_key.add_argument(
        "--expires-at",
        type=parse_expiration,
        help="ISO 8601 timestamp with timezone, e.g. 2027-01-01T00:00:00+00:00",
    )

    list_keys = commands.add_parser("list-keys")
    list_keys.add_argument("--client")

    for command in ("revoke", "reactivate"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--key-prefix", required=True)

    change_plan = commands.add_parser("change-plan")
    change_plan.add_argument("--client", required=True)
    change_plan.add_argument("--plan", required=True)

    usage = commands.add_parser("usage")
    usage.add_argument("--client")
    usage.add_argument("--key-prefix")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    with SessionLocal() as session:
        if args.command == "create-client":
            plan = args.plan.upper()
            if plan not in settings.plan_limits:
                raise SystemExit(f"Unknown plan: {plan}")
            if session.scalar(
                select(ApiClient).where(ApiClient.identifier == args.identifier)
            ):
                raise SystemExit(f"Client already exists: {args.identifier}")
            client = ApiClient(
                identifier=args.identifier,
                display_name=args.name,
                plan=plan,
            )
            session.add(client)
            session.commit()
            print(f"Created client: {client.identifier} plan={client.plan}")
            return

        if args.command == "create-key":
            client = require_client(session, args.client)
            record, raw_key = issue_api_key(
                session,
                client,
                settings,
                name=args.name,
                expires_at=args.expires_at,
            )
            print(f"Created API key: {record.key_prefix} client={client.identifier}")
            print("Raw API key (shown once; store it securely):")
            print(raw_key)
            return

        if args.command == "list-keys":
            statement = select(ApiKey).options(joinedload(ApiKey.client))
            if args.client:
                statement = statement.join(ApiClient).where(
                    ApiClient.identifier == args.client
                )
            for record in session.scalars(statement.order_by(ApiKey.created_at)):
                print(
                    f"{record.key_prefix} client={record.client.identifier} "
                    f"plan={record.client.plan} active={record.active} "
                    f"expires={record.expires_at or '-'} "
                    f"last_used={record.last_used_at or '-'}"
                )
            return

        if args.command in {"revoke", "reactivate"}:
            record = require_key(session, args.key_prefix)
            record.active = args.command == "reactivate"
            session.commit()
            print(f"{args.command}d API key: {record.key_prefix}")
            return

        if args.command == "change-plan":
            plan = args.plan.upper()
            if plan not in settings.plan_limits:
                raise SystemExit(f"Unknown plan: {plan}")
            client = require_client(session, args.client)
            client.plan = plan
            session.commit()
            print(f"Changed client plan: {client.identifier} plan={plan}")
            return

        statement = (
            select(MonthlyUsage)
            .join(ApiKey)
            .join(ApiClient)
            .options(joinedload(MonthlyUsage.api_key).joinedload(ApiKey.client))
        )
        if args.client:
            statement = statement.where(ApiClient.identifier == args.client)
        if args.key_prefix:
            statement = statement.where(ApiKey.key_prefix == args.key_prefix)
        for usage in session.scalars(
            statement.order_by(MonthlyUsage.period_start.desc())
        ):
            print(
                f"period={usage.period_start} "
                f"client={usage.api_key.client.identifier} "
                f"key={usage.api_key.key_prefix} "
                f"requests={usage.request_count} lookups={usage.lookup_count} "
                f"last_request={usage.last_request_at or '-'}"
            )


if __name__ == "__main__":
    main()

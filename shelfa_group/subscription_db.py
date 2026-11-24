"""Subscription rows, entitlement priority, and account-plan recalculation.

The user.account_mode column remains the effective plan used by contact and
message limits. Store subscriptions never overwrite an active administrator
override. An expired override falls back to the best entitled store subscription.
"""

import json
import sqlite3
import time
from datetime import datetime, timezone

from shelfa_group.db import (
    ACCOUNT_MODES,
    FREE_ACCOUNT_MODE,
    LIMITED_ACCOUNT_MODE,
    PREMIUM_ACCOUNT_MODE,
    get_account_mode_limits,
    get_db,
)

SOURCE_FREE = "FREE"
SOURCE_APPLE = "APPLE_SUBSCRIPTION"
SOURCE_GOOGLE = "GOOGLE_SUBSCRIPTION"
SOURCE_ADMIN = "ADMIN_OVERRIDE"
ENTITLED_STATUSES = {"ACTIVE", "GRACE_PERIOD", "CANCELED", "BILLING_RETRY"}
PLAN_RANK = {
    FREE_ACCOUNT_MODE: 0,
    LIMITED_ACCOUNT_MODE: 1,
    PREMIUM_ACCOUNT_MODE: 2,
}
_BACKFILL_ID = "backfill_admin_override_v1"


class SubscriptionOwnershipConflict(ValueError):
    pass


def now() -> int:
    return int(time.time())


def to_iso(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(int(timestamp), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def migrate(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(user)")}
    if "admin_override_plan" not in columns:
        conn.execute("ALTER TABLE user ADD COLUMN admin_override_plan TEXT")
    if "admin_override_expires_at" not in columns:
        conn.execute(
            "ALTER TABLE user ADD COLUMN admin_override_expires_at INTEGER"
        )
    if "entitlement_source" not in columns:
        conn.execute(
            """
            ALTER TABLE user
            ADD COLUMN entitlement_source TEXT NOT NULL DEFAULT 'FREE'
            """
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration (
            id TEXT PRIMARY KEY,
            applied_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            platform TEXT NOT NULL CHECK (platform IN ('APPLE', 'GOOGLE')),
            environment TEXT NOT NULL CHECK (environment IN ('Sandbox', 'Production')),
            product_id TEXT NOT NULL,
            purchase_token TEXT,
            transaction_id TEXT,
            original_transaction_id TEXT,
            subscription_status TEXT NOT NULL,
            account_plan TEXT NOT NULL CHECK (account_plan IN ('limited', 'premium')),
            start_date INTEGER,
            expiration_date INTEGER,
            auto_renew INTEGER NOT NULL DEFAULT 0,
            acknowledgement_state TEXT,
            linked_purchase_token TEXT,
            last_event_at INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_subscriptions_google_token
        ON subscriptions(purchase_token)
        WHERE purchase_token IS NOT NULL AND purchase_token != ''
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_subscriptions_apple_original
        ON subscriptions(platform, environment, original_transaction_id)
        WHERE original_transaction_id IS NOT NULL AND original_transaction_id != ''
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_subscriptions_user
        ON subscriptions(user_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subscription_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER,
            user_id INTEGER,
            platform TEXT NOT NULL,
            event_type TEXT NOT NULL,
            notification_id TEXT,
            summary_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_subscription_events_notification
        ON subscription_events(platform, notification_id)
        WHERE notification_id IS NOT NULL AND notification_id != ''
        """
    )
    applied = conn.execute(
        "SELECT 1 FROM schema_migration WHERE id = ?",
        (_BACKFILL_ID,),
    ).fetchone()
    if applied is None:
        conn.execute(
            """
            UPDATE user
            SET admin_override_plan = account_mode,
                entitlement_source = 'ADMIN_OVERRIDE'
            WHERE account_mode IN ('limited', 'premium')
              AND admin_override_plan IS NULL
            """
        )
        conn.execute(
            "INSERT INTO schema_migration (id, applied_at) VALUES (?, ?)",
            (_BACKFILL_ID, now()),
        )


def _entitled(row: sqlite3.Row, timestamp: int) -> bool:
    if row["subscription_status"] not in ENTITLED_STATUSES:
        return False
    expires = row["expiration_date"]
    if expires is not None and int(expires) <= timestamp:
        return False
    return True


def _override_active(user: sqlite3.Row, timestamp: int) -> bool:
    plan = user["admin_override_plan"]
    if plan not in ACCOUNT_MODES:
        return False
    expires = user["admin_override_expires_at"]
    return expires is None or int(expires) > timestamp


def recompute_entitlement(
    conn: sqlite3.Connection,
    user_id: int,
    timestamp: int | None = None,
) -> str | None:
    timestamp = now() if timestamp is None else int(timestamp)
    user = conn.execute("SELECT * FROM user WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        return None
    if _override_active(user, timestamp):
        plan = user["admin_override_plan"]
        source = SOURCE_ADMIN
    else:
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        best = None
        for row in rows:
            if not _entitled(row, timestamp):
                continue
            if best is None or PLAN_RANK[row["account_plan"]] > PLAN_RANK[best["account_plan"]]:
                best = row
        if best is None:
            plan = FREE_ACCOUNT_MODE
            source = SOURCE_FREE
        else:
            plan = best["account_plan"]
            source = SOURCE_APPLE if best["platform"] == "APPLE" else SOURCE_GOOGLE
    conn.execute(
        """
        UPDATE user
        SET account_mode = ?, entitlement_source = ?
        WHERE id = ?
        """,
        (plan, source, user_id),
    )
    return plan


def expire_elapsed(conn: sqlite3.Connection, timestamp: int | None = None) -> set[int]:
    timestamp = now() if timestamp is None else int(timestamp)
    rows = conn.execute(
        """
        SELECT id, user_id, platform FROM subscriptions
        WHERE expiration_date IS NOT NULL
          AND expiration_date <= ?
          AND subscription_status IN (
              'ACTIVE', 'GRACE_PERIOD', 'CANCELED', 'BILLING_RETRY', 'PENDING'
          )
        """,
        (timestamp,),
    ).fetchall()
    user_ids = {int(row["user_id"]) for row in rows}
    for row in rows:
        conn.execute(
            """
            UPDATE subscriptions
            SET subscription_status = 'EXPIRED', updated_at = ?
            WHERE id = ?
            """,
            (timestamp, row["id"]),
        )
        _insert_event(
            conn,
            subscription_id=row["id"],
            user_id=row["user_id"],
            platform=row["platform"],
            event_type="expiration_sweep",
            notification_id=None,
            summary={"subscription_id": row["id"], "status": "EXPIRED"},
            timestamp=timestamp,
        )
    for user_id in user_ids:
        recompute_entitlement(conn, user_id, timestamp)
    return user_ids


def set_admin_override(
    conn: sqlite3.Connection,
    user_id: int,
    plan: str,
    expires_at: int | None,
) -> None:
    if plan not in ACCOUNT_MODES:
        raise ValueError("Invalid account plan")
    conn.execute(
        """
        UPDATE user
        SET admin_override_plan = ?, admin_override_expires_at = ?
        WHERE id = ?
        """,
        (plan, expires_at, user_id),
    )
    recompute_entitlement(conn, user_id)


def clear_admin_override(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute(
        """
        UPDATE user
        SET admin_override_plan = NULL, admin_override_expires_at = NULL
        WHERE id = ?
        """,
        (user_id,),
    )
    recompute_entitlement(conn, user_id)


def _insert_event(
    conn: sqlite3.Connection,
    *,
    subscription_id: int | None,
    user_id: int | None,
    platform: str,
    event_type: str,
    notification_id: str | None,
    summary: dict,
    timestamp: int,
) -> None:
    conn.execute(
        """
        INSERT INTO subscription_events (
            subscription_id, user_id, platform, event_type, notification_id,
            summary_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            subscription_id,
            user_id,
            platform,
            event_type,
            notification_id,
            json.dumps(summary, separators=(",", ":"), sort_keys=True),
            timestamp,
        ),
    )


def _find_row(
    conn: sqlite3.Connection,
    *,
    platform: str,
    environment: str,
    purchase_token: str | None,
    original_transaction_id: str | None,
    linked_purchase_token: str | None,
) -> sqlite3.Row | None:
    if purchase_token:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE purchase_token = ?",
            (purchase_token,),
        ).fetchone()
        if row is not None:
            return row
    if original_transaction_id:
        row = conn.execute(
            """
            SELECT * FROM subscriptions
            WHERE platform = ? AND environment = ? AND original_transaction_id = ?
            """,
            (platform, environment, original_transaction_id),
        ).fetchone()
        if row is not None:
            return row
    if linked_purchase_token:
        return conn.execute(
            "SELECT * FROM subscriptions WHERE purchase_token = ?",
            (linked_purchase_token,),
        ).fetchone()
    return None


def notification_already_processed(platform: str, notification_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM subscription_events
            WHERE platform = ? AND notification_id = ?
            """,
            (platform, notification_id),
        ).fetchone()
    return row is not None


def find_owner_id(
    *,
    platform: str,
    environment: str,
    purchase_token: str | None,
    original_transaction_id: str | None,
    linked_purchase_token: str | None,
) -> int | None:
    with get_db() as conn:
        row = _find_row(
            conn,
            platform=platform,
            environment=environment,
            purchase_token=purchase_token,
            original_transaction_id=original_transaction_id,
            linked_purchase_token=linked_purchase_token,
        )
    return None if row is None else int(row["user_id"])


def apply_store_subscription(
    user_id: int,
    purchase: dict,
    *,
    event_type: str,
    notification_id: str | None = None,
    authoritative: bool = True,
) -> dict:
    timestamp = now()
    event_time = purchase.get("event_time")
    if event_time is None:
        event_time = timestamp
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = _find_row(
                conn,
                platform=purchase["platform"],
                environment=purchase["environment"],
                purchase_token=purchase.get("purchase_token"),
                original_transaction_id=purchase.get("original_transaction_id"),
                linked_purchase_token=None,
            )
            linked = purchase.get("linked_purchase_token")
            linked_row = None
            if linked:
                linked_row = conn.execute(
                    "SELECT * FROM subscriptions WHERE purchase_token = ?",
                    (linked,),
                ).fetchone()
            owner_row = existing or linked_row
            if owner_row is not None and int(owner_row["user_id"]) != int(user_id):
                raise SubscriptionOwnershipConflict(
                    "This purchase is already associated with another account"
                )
            stale = False
            if (
                existing is not None
                and not authoritative
                and existing["last_event_at"] is not None
                and int(event_time) < int(existing["last_event_at"])
            ):
                stale = True
            summary = {
                "product_id": purchase["product_id"],
                "status": purchase["subscription_status"],
                "account_plan": purchase["account_plan"],
                "expiration_date": purchase.get("expiration_date"),
                "environment": purchase["environment"],
                "authoritative": authoritative,
                "stale": stale,
            }
            if stale:
                _insert_event(
                    conn,
                    subscription_id=existing["id"],
                    user_id=user_id,
                    platform=purchase["platform"],
                    event_type="ignored_stale_" + event_type,
                    notification_id=notification_id,
                    summary=summary,
                    timestamp=timestamp,
                )
                conn.commit()
                status = read_status(conn, user_id)
                status["applied"] = False
                status["reason"] = "stale"
                return status

            last_event_at = timestamp if authoritative else int(event_time)
            if existing is not None and existing["last_event_at"] is not None:
                last_event_at = max(last_event_at, int(existing["last_event_at"]))
            values = (
                purchase["platform"],
                purchase["environment"],
                purchase["product_id"],
                purchase.get("purchase_token"),
                purchase.get("transaction_id"),
                purchase.get("original_transaction_id"),
                purchase["subscription_status"],
                purchase["account_plan"],
                purchase.get("start_date"),
                purchase.get("expiration_date"),
                1 if purchase.get("auto_renew") else 0,
                purchase.get("acknowledgement_state"),
                purchase.get("linked_purchase_token"),
                last_event_at,
                timestamp,
            )
            if existing is None:
                cursor = conn.execute(
                    """
                    INSERT INTO subscriptions (
                        user_id, platform, environment, product_id, purchase_token,
                        transaction_id, original_transaction_id, subscription_status,
                        account_plan, start_date, expiration_date, auto_renew,
                        acknowledgement_state, linked_purchase_token, last_event_at,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, *values, timestamp),
                )
                subscription_id = cursor.lastrowid
            else:
                subscription_id = existing["id"]
                conn.execute(
                    """
                    UPDATE subscriptions SET
                        user_id = ?,
                        platform = ?,
                        environment = ?,
                        product_id = ?,
                        purchase_token = ?,
                        transaction_id = ?,
                        original_transaction_id = ?,
                        subscription_status = ?,
                        account_plan = ?,
                        start_date = ?,
                        expiration_date = ?,
                        auto_renew = ?,
                        acknowledgement_state = ?,
                        linked_purchase_token = ?,
                        last_event_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (user_id, *values, subscription_id),
                )
            if linked:
                conn.execute(
                    """
                    UPDATE subscriptions
                    SET subscription_status = 'EXPIRED', updated_at = ?
                    WHERE purchase_token = ? AND id != ? AND user_id = ?
                    """,
                    (timestamp, linked, subscription_id, user_id),
                )
            _insert_event(
                conn,
                subscription_id=subscription_id,
                user_id=user_id,
                platform=purchase["platform"],
                event_type=event_type,
                notification_id=notification_id,
                summary=summary,
                timestamp=timestamp,
            )
            expire_elapsed(conn, timestamp)
            recompute_entitlement(conn, user_id, timestamp)
            conn.commit()
            status = read_status(conn, user_id)
            status["applied"] = True
            return status
        except sqlite3.IntegrityError as error:
            conn.rollback()
            message = str(error)
            if "ux_subscription_events_notification" in message or "notification_id" in message:
                status = get_status(user_id)
                status["applied"] = False
                status["duplicate"] = True
                return status
            if "ux_subscriptions" in message or "UNIQUE" in message:
                raise SubscriptionOwnershipConflict(
                    "This purchase is already associated with another account"
                ) from error
            raise


def record_notification(
    *,
    platform: str,
    event_type: str,
    notification_id: str | None,
    summary: dict,
    user_id: int | None = None,
    subscription_id: int | None = None,
) -> bool:
    """Record a notification. Returns False when the notification id was already stored."""
    timestamp = now()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _insert_event(
                conn,
                subscription_id=subscription_id,
                user_id=user_id,
                platform=platform,
                event_type=event_type,
                notification_id=notification_id,
                summary=summary,
                timestamp=timestamp,
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            conn.rollback()
            return False


def get_status(user_id: int) -> dict:
    timestamp = now()
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        expire_elapsed(conn, timestamp)
        recompute_entitlement(conn, user_id, timestamp)
        status = read_status(conn, user_id)
        conn.commit()
        return status


def read_status(conn: sqlite3.Connection, user_id: int) -> dict:
    user = conn.execute("SELECT * FROM user WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        raise KeyError(user_id)
    timestamp = now()
    rows = conn.execute(
        """
        SELECT * FROM subscriptions
        WHERE user_id = ?
        ORDER BY updated_at DESC, id DESC
        """,
        (user_id,),
    ).fetchall()
    entitled = [row for row in rows if _entitled(row, timestamp)]
    selected = None
    if entitled:
        selected = max(entitled, key=lambda row: PLAN_RANK[row["account_plan"]])
    elif rows:
        selected = rows[0]
    limits = get_account_mode_limits()
    plan = user["account_mode"]
    if plan == PREMIUM_ACCOUNT_MODE:
        contact_limit = None
        message_limit = None
    else:
        mode_limits = limits.get(plan) or limits.get(FREE_ACCOUNT_MODE)
        contact_limit = mode_limits["max_contacts"] if mode_limits else None
        message_limit = (
            mode_limits["max_message_history_per_contact"] if mode_limits else None
        )
    override = None
    if user["admin_override_plan"]:
        override = {
            "plan": user["admin_override_plan"].upper(),
            "expiresAt": to_iso(user["admin_override_expires_at"]),
            "active": _override_active(user, timestamp),
        }
    subscription = None
    if selected is not None:
        subscription = {
            "platform": selected["platform"],
            "productId": selected["product_id"],
            "plan": selected["account_plan"].upper(),
            "status": selected["subscription_status"],
            "expiresAt": to_iso(selected["expiration_date"]),
            "autoRenew": bool(selected["auto_renew"]),
            "environment": selected["environment"],
        }
    return {
        "effectivePlan": plan.upper(),
        "entitlementSource": user["entitlement_source"],
        "subscription": subscription,
        "limits": {
            "contacts": contact_limit,
            "messagesPerContact": message_limit,
        },
        "adminOverride": override,
    }


def limits_for_plan(plan: str) -> tuple[int | None, int | None]:
    if plan == PREMIUM_ACCOUNT_MODE:
        return None, None
    limits = get_account_mode_limits()
    selected = limits.get(plan) or limits[FREE_ACCOUNT_MODE]
    return selected["max_contacts"], selected["max_message_history_per_contact"]

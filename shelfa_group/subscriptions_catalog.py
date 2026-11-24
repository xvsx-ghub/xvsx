"""Store product identifiers. Prices are never stored here."""

import os

from shelfa_group.db import LIMITED_ACCOUNT_MODE, PREMIUM_ACCOUNT_MODE


def product_ids() -> dict[str, str]:
    return {
        "android_limited": os.environ.get("SHELFA_ANDROID_LIMITED_PRODUCT_ID", "shelfa_limited"),
        "android_premium": os.environ.get("SHELFA_ANDROID_PREMIUM_PRODUCT_ID", "shelfa_premium"),
        "android_base_plan": os.environ.get("SHELFA_ANDROID_BASE_PLAN_ID", "monthly"),
        "ios_limited": os.environ.get("SHELFA_IOS_LIMITED_PRODUCT_ID", "com.shelfa.limited.monthly"),
        "ios_premium": os.environ.get("SHELFA_IOS_PREMIUM_PRODUCT_ID", "com.shelfa.premium.monthly"),
    }


def plan_for_product(product_id: str) -> str | None:
    ids = product_ids()
    if product_id in {ids["android_limited"], ids["ios_limited"]}:
        return LIMITED_ACCOUNT_MODE
    if product_id in {ids["android_premium"], ids["ios_premium"]}:
        return PREMIUM_ACCOUNT_MODE
    return None


def catalog_plans() -> list[dict]:
    from shelfa_group.subscription_db import limits_for_plan

    ids = product_ids()
    plans = []
    for plan, android_key, ios_key in (
        (LIMITED_ACCOUNT_MODE, "android_limited", "ios_limited"),
        (PREMIUM_ACCOUNT_MODE, "android_premium", "ios_premium"),
    ):
        contacts, messages = limits_for_plan(plan)
        plans.append(
            {
                "plan": plan.upper(),
                "androidProductId": ids[android_key],
                "iosProductId": ids[ios_key],
                "androidBasePlanId": ids["android_base_plan"],
                "contactsLimit": contacts,
                "messagesLimit": messages,
            }
        )
    return plans

# Shelfa subscriptions

The backend is the only component that can activate a paid account plan. Android
uses Google Play Billing Library 7.1.1. iOS uses StoreKit 2. Prices, currencies,
and billing periods are read from the stores at runtime and are never hardcoded.

A Shelfa account is the numeric `user.id`. Purchase requests also send the
registered `device_id`, which is the same device check already used for calls.
A verified purchase is stored against that user id. The same purchase token or
Apple original transaction id cannot be attached to a second account.

`user.account_mode` is the effective plan. Contact and message-history limits
continue to read it. The priority is:

1. An unexpired administrator override.
2. The highest active store subscription. `ACTIVE`, `GRACE_PERIOD`,
   `BILLING_RETRY`, and `CANCELED` remain entitled until `expiration_date`.
3. Free.

`PENDING`, `ON_HOLD`, `PAUSED`, `EXPIRED`, and `REVOKED` do not grant paid
access. Canceling in the store does not remove access before the period ends.
Downgrade does not delete contacts or messages. New contacts beyond the current
limit are rejected, and message history requests return only the allowed number
of recent messages.

## Product identifiers

These identifiers are not already configured in the project. Create them in the
stores before testing purchases.

| Plan | Google Play product | Base plan | Apple product |
| --- | --- | --- | --- |
| Limited | `shelfa_limited` | `monthly` (`P1M`) | `com.shelfa.limited.monthly` |
| Premium | `shelfa_premium` | `monthly` (`P1M`) | `com.shelfa.premium.monthly` |

Apple subscription group: Shelfa Account Plans. Premium is level 1. Limited is
level 2. Both products are monthly auto-renewable subscriptions.

Override identifiers with `SHELFA_ANDROID_LIMITED_PRODUCT_ID`,
`SHELFA_ANDROID_PREMIUM_PRODUCT_ID`, `SHELFA_ANDROID_BASE_PLAN_ID`,
`SHELFA_IOS_LIMITED_PRODUCT_ID`, and `SHELFA_IOS_PREMIUM_PRODUCT_ID` if the
store products use different ids. The mobile catalog endpoint returns the
configured ids. The apps still load the price from the store.

## API

Authenticated calls require the registered nickname and device id. There is no
separate bearer token in the current Shelfa API.

- `GET /api/subscriptions/products` returns plans, product ids, and the current
  configured limits. It does not return prices.
- `POST /api/subscriptions/verify` verifies a Google purchase token or an Apple
  signed transaction with the store, then updates the account.
- `GET /api/subscriptions/status` returns the effective plan, entitlement
  source, subscription state, and limits.
- `POST /api/subscriptions/apple/notifications` accepts App Store Server
  Notifications V2 (`signedPayload`).
- `POST /api/subscriptions/google/notifications` accepts a Google Cloud Pub/Sub
  push envelope. Configure either `SHELFA_GOOGLE_PUBSUB_VERIFICATION_TOKEN` as
  the push token query parameter, or both `SHELFA_GOOGLE_PUBSUB_AUDIENCE` and
  `SHELFA_GOOGLE_PUBSUB_SERVICE_ACCOUNT` for OIDC.

Notification handlers load the current subscription from Apple or Google before
changing an account. Unknown purchases are not assigned to an account.
Duplicate notification ids are ignored. A non-authoritative event older than
the last applied store event does not roll the subscription backward.

## Server credentials

Install these Python packages on the server before enabling live verification:

```bash
pip install app-store-server-library google-api-python-client google-auth
```

The application does not load `.env` by itself. Export the variables or use a
systemd `EnvironmentFile`.

Google:

- `SHELFA_GOOGLE_PACKAGE_NAME` — `com.shelfa`.
- `SHELFA_GOOGLE_SERVICE_ACCOUNT_FILE` — Play Developer API service account JSON.
  Grant that account access in Play Console. Do not ship this file in the app.
- Pub/Sub authentication variables described above.

Apple:

- `SHELFA_APPLE_BUNDLE_ID` — `com.shelfa`.
- `SHELFA_APPLE_ISSUER_ID` — App Store Connect issuer id.
- `SHELFA_APPLE_KEY_ID` — the In-App Purchase key id.
- `SHELFA_APPLE_PRIVATE_KEY_PATH` — the `.p8` file. Keep it on the server only.
- `SHELFA_APPLE_APP_APPLE_ID` — numeric App Store app id. Required for
  production signature verification.
- `SHELFA_APPLE_ROOT_CERTIFICATES_DIR` — Apple root certificates (`.cer`,
  `.der`, or `.pem`) from Apple PKI.
- `SHELFA_APPLE_ALLOW_SANDBOX` — `true` while App Review and sandbox testing
  need to verify sandbox transactions. Set `false` to reject sandbox purchases.
- `SHELFA_APPLE_ENABLE_ONLINE_CHECKS` — certificate revocation checks. Default
  `true`.

If a credential is missing, verification returns HTTP 503 and does not activate
a plan. There is no production mode that trusts the client.

## Google Play Console

1. Create a subscription named Limited with product id `shelfa_limited`.
2. Create a subscription named Premium with product id `shelfa_premium`.
3. Add a base plan with id `monthly` and billing period 1 month to each.
4. Set prices in Play Console. The app reads the localized price from Google Play.
5. Activate both subscriptions.
6. Create a Google Cloud service account, enable the Google Play Android
   Developer API, and invite the service account in Play Console with
   permission to view financial data and manage orders.
7. Create a Pub/Sub topic for Real-time developer notifications and a push
   subscription aimed at
   `https://<host>/api/subscriptions/google/notifications`.
8. Authenticate the push subscription with the verification token or an OIDC
   service account.
9. In Play Console, set the Real-time developer notifications topic.
10. Add license testers and use a licensed tester account for purchases.
11. Upload the app to an internal testing track. Billing does not work from a
    sideloaded build that is not installed through Play.
12. Publish the testing or production release after the subscriptions and the
    backend credentials are active.

Upgrade from Limited to Premium uses Google Play's prorated replacement.
Downgrade uses deferred replacement. The app acknowledges a purchase only
after the backend has stored it. The server also acknowledges when Google
reports the purchase as not yet acknowledged.

## App Store Connect

1. Create the subscription group Shelfa Account Plans.
2. Create the monthly Limited subscription `com.shelfa.limited.monthly`.
3. Create the monthly Premium subscription `com.shelfa.premium.monthly`.
4. Set Premium to level 1 and Limited to level 2.
5. Add prices. The app displays StoreKit's localized price.
6. Add localizations for the subscription display name and description.
7. Upload the App Review screenshot for each subscription.
8. Set the App Store Server Notifications V2 production and sandbox URLs to
   `https://<host>/api/subscriptions/apple/notifications`.
9. Create an App Store Connect API key with access to In-App Purchase and
   store the key id, issuer id, and `.p8` on the server.
10. Create sandbox testers and test on a signed build.
11. Submit the subscriptions with the app version that contains this purchase
   flow.

StoreKit 2 verifies the transaction on device. The app sends the signed
transaction to the backend and finishes the StoreKit transaction only after
the backend accepts it. Restore uses `AppStore.sync()` and current
entitlements. Manage Subscription opens Apple's subscription management sheet.
A subscription bought on Apple must be managed on Apple, and a subscription
bought on Google Play must be managed on Google Play. The paid plan still
follows the Shelfa account on the other platform.

## What is not production-proven yet

Live purchases cannot be completed in this repository. They need the store
products, the server credentials above, a Play testing track, and an Apple
sandbox account. Automated tests cover entitlement rules with fake store
clients. They do not call Apple or Google.

"""Apple App Store Server API and Google Play Developer API clients.

Purchase tokens, private keys, and signed payloads are never logged.
Automated tests inject a fake client. Production never accepts an unverified
purchase, including when credentials are missing.
"""

import base64
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from shelfa_group.subscriptions_catalog import plan_for_product, product_ids


class StoreNotConfigured(RuntimeError):
    pass


class StoreVerificationError(ValueError):
    pass


class StoreAuthenticationError(PermissionError):
    pass


@dataclass(frozen=True)
class VerifiedPurchase:
    platform: str
    environment: str
    product_id: str
    account_plan: str
    status: str
    purchase_token: str | None
    transaction_id: str | None
    original_transaction_id: str | None
    start_date: int | None
    expiration_date: int | None
    auto_renew: bool
    acknowledgement_state: str | None
    linked_purchase_token: str | None
    bundle_or_package: str
    event_time: int | None = None

    def as_dict(self) -> dict:
        return {
            "platform": self.platform,
            "environment": self.environment,
            "product_id": self.product_id,
            "account_plan": self.account_plan,
            "subscription_status": self.status,
            "purchase_token": self.purchase_token,
            "transaction_id": self.transaction_id,
            "original_transaction_id": self.original_transaction_id,
            "start_date": self.start_date,
            "expiration_date": self.expiration_date,
            "auto_renew": self.auto_renew,
            "acknowledgement_state": self.acknowledgement_state,
            "linked_purchase_token": self.linked_purchase_token,
            "event_time": self.event_time,
        }


def parse_time(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, (int, float)):
        number = int(value)
        if number > 10_000_000_000:
            return number // 1000
        return number
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return parse_time(int(text))
        normalized = text.replace("Z", "+00:00")
        return int(datetime.fromisoformat(normalized).timestamp())
    return None


def _environment_name(value) -> str:
    if value is None:
        return ""
    name = getattr(value, "name", None) or str(value)
    lowered = name.lower()
    if "sandbox" in lowered:
        return "Sandbox"
    if "production" in lowered:
        return "Production"
    return name


def _require_known_product(product_id: str, bundle_or_package: str, expected_package: str) -> str:
    if bundle_or_package and bundle_or_package != expected_package:
        raise StoreVerificationError("Incorrect application identifier")
    plan = plan_for_product(product_id)
    if plan is None:
        raise StoreVerificationError("Incorrect product identifier")
    return plan


def _apple_status(status_value, revocation_date, expiration_date, auto_renew: bool, timestamp: int) -> str:
    if revocation_date is not None:
        return "REVOKED"
    code = getattr(status_value, "value", status_value)
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = None
    if code == 5:
        return "REVOKED"
    if code == 4:
        return "GRACE_PERIOD"
    if code == 3:
        return "BILLING_RETRY"
    if code == 2:
        return "EXPIRED"
    if expiration_date is not None and expiration_date <= timestamp:
        return "EXPIRED"
    if code == 1 or code is None:
        return "CANCELED" if not auto_renew else "ACTIVE"
    return "ACTIVE"


class AppleAppStoreClient:
    def __init__(self, signing_key: bytes, key_id: str, issuer_id: str, bundle_id: str, app_apple_id: int | None, root_certificates: list[bytes], allow_sandbox: bool):
        self.signing_key = signing_key
        self.key_id = key_id
        self.issuer_id = issuer_id
        self.bundle_id = bundle_id
        self.app_apple_id = app_apple_id
        self.root_certificates = root_certificates
        self.allow_sandbox = allow_sandbox

    @classmethod
    def from_environment(cls) -> "AppleAppStoreClient":
        key_path = os.environ.get("SHELFA_APPLE_PRIVATE_KEY_PATH", "")
        key_id = os.environ.get("SHELFA_APPLE_KEY_ID", "")
        issuer_id = os.environ.get("SHELFA_APPLE_ISSUER_ID", "")
        bundle_id = os.environ.get("SHELFA_APPLE_BUNDLE_ID", "com.shelfa")
        raw_app_id = os.environ.get("SHELFA_APPLE_APP_APPLE_ID", "")
        cert_dir = os.environ.get("SHELFA_APPLE_ROOT_CERTIFICATES_DIR", "")
        if not key_path or not key_id or not issuer_id or not cert_dir:
            raise StoreNotConfigured("Apple App Store Server API is not configured")
        certificates = _load_certificates(cert_dir)
        if not certificates:
            raise StoreNotConfigured("Apple root certificates were not found")
        app_apple_id = int(raw_app_id) if raw_app_id else None
        allow_sandbox = os.environ.get("SHELFA_APPLE_ALLOW_SANDBOX", "true").lower() not in {
            "0",
            "false",
            "no",
        }
        return cls(
            Path(key_path).read_bytes(),
            key_id,
            issuer_id,
            bundle_id,
            app_apple_id,
            certificates,
            allow_sandbox,
        )

    def verify_signed_transaction(self, signed_transaction: str, expected_product_id: str | None = None) -> VerifiedPurchase:
        if not signed_transaction:
            raise StoreVerificationError("Missing signed transaction")
        decoded, environment = self._verify_transaction(signed_transaction)
        return self._purchase_from_decoded(decoded, environment, expected_product_id, signed_transaction)

    def decode_notification(self, signed_payload: str) -> dict:
        if not signed_payload:
            raise StoreVerificationError("Missing signed notification")
        decoded = self._verify_notification(signed_payload)
        data = _value(decoded, "data")
        signed_transaction = _value(data, "signedTransactionInfo")
        if not signed_transaction:
            raise StoreVerificationError("Notification did not include a transaction")
        environment = _environment_name(_value(data, "environment"))
        transaction = self._verify_transaction(signed_transaction, environment or None)[0]
        renewal = _value(data, "signedRenewalInfo")
        renewal_decoded = None
        if renewal:
            renewal_decoded = self._verify_renewal(renewal, environment or None)
        notification_type = _value(decoded, "notificationType")
        type_name = getattr(notification_type, "name", None) or str(notification_type or "")
        return {
            "notification_id": str(_value(decoded, "notificationUUID") or ""),
            "notification_type": type_name,
            "signed_transaction": signed_transaction,
            "transaction": transaction,
            "renewal": renewal_decoded,
            "environment": environment,
        }

    def _purchase_from_decoded(self, decoded, environment: str, expected_product_id: str | None, signed_transaction: str) -> VerifiedPurchase:
        bundle_id = str(_value(decoded, "bundleId") or "")
        product_id = str(_value(decoded, "productId") or "")
        if expected_product_id and expected_product_id != product_id:
            raise StoreVerificationError("Incorrect product identifier")
        if environment == "Sandbox" and not self.allow_sandbox:
            raise StoreVerificationError("Invalid environment combination")
        if environment not in {"Sandbox", "Production"}:
            raise StoreVerificationError("Invalid environment combination")
        if environment == "Production" and self.app_apple_id is None:
            raise StoreNotConfigured("SHELFA_APPLE_APP_APPLE_ID is required for production verification")
        plan = _require_known_product(product_id, bundle_id, self.bundle_id)
        revocation = parse_time(_value(decoded, "revocationDate"))
        expiration = parse_time(_value(decoded, "expiresDate"))
        timestamp = int(datetime.now(timezone.utc).timestamp())
        status = _apple_status(None, revocation, expiration, True, timestamp)
        return VerifiedPurchase(
            platform="APPLE",
            environment=environment,
            product_id=product_id,
            account_plan=plan,
            status=status,
            purchase_token=None,
            transaction_id=str(_value(decoded, "transactionId") or ""),
            original_transaction_id=str(_value(decoded, "originalTransactionId") or ""),
            start_date=parse_time(_value(decoded, "purchaseDate")),
            expiration_date=expiration,
            auto_renew=status == "ACTIVE",
            acknowledgement_state=None,
            linked_purchase_token=None,
            bundle_or_package=bundle_id,
            event_time=parse_time(_value(decoded, "signedDate")) or timestamp,
        )

    def subscription_status(self, transaction_id: str, signed_transaction: str, expected_product_id: str | None = None) -> VerifiedPurchase:
        """Prefer the App Store Server API status, and fall back to the verified transaction."""
        verified = self.verify_signed_transaction(signed_transaction, expected_product_id)
        try:
            status_payload = self._api_status(transaction_id or verified.transaction_id or "", verified.environment)
        except StoreVerificationError:
            raise
        except Exception:
            return verified
        if status_payload is None:
            return verified
        return status_payload

    def _api_status(self, transaction_id: str, environment: str) -> VerifiedPurchase | None:
        if not transaction_id:
            return None
        client = self._api_client(environment)
        response = client.get_all_subscription_statuses(transaction_id)
        groups = _value(response, "data") or []
        selected = None
        selected_status = None
        for group in groups:
            for item in _value(group, "lastTransactions") or []:
                signed = _value(item, "signedTransactionInfo")
                if not signed:
                    continue
                decoded, _environment = self._verify_transaction(signed, environment)
                if str(_value(decoded, "transactionId") or "") == transaction_id or selected is None:
                    selected = (decoded, signed, _value(item, "signedRenewalInfo"), item)
                    selected_status = _value(item, "status")
                    if str(_value(decoded, "transactionId") or "") == transaction_id:
                        break
        if selected is None:
            return None
        decoded, signed, renewal_signed, _item = selected
        renewal = self._verify_renewal(renewal_signed, environment) if renewal_signed else None
        auto_renew_value = _value(renewal, "autoRenewStatus")
        auto_renew = str(getattr(auto_renew_value, "value", auto_renew_value)) in {"1", "True", "true"}
        expiration = parse_time(_value(decoded, "expiresDate"))
        grace = parse_time(_value(renewal, "gracePeriodExpiresDate")) if renewal else None
        revocation = parse_time(_value(decoded, "revocationDate"))
        timestamp = int(datetime.now(timezone.utc).timestamp())
        status = _apple_status(selected_status, revocation, grace or expiration, auto_renew, timestamp)
        if status == "GRACE_PERIOD" and grace:
            expiration = grace
        product_id = str(_value(decoded, "productId") or "")
        plan = _require_known_product(product_id, str(_value(decoded, "bundleId") or ""), self.bundle_id)
        return VerifiedPurchase(
            platform="APPLE",
            environment=environment,
            product_id=product_id,
            account_plan=plan,
            status=status,
            purchase_token=None,
            transaction_id=str(_value(decoded, "transactionId") or ""),
            original_transaction_id=str(_value(decoded, "originalTransactionId") or ""),
            start_date=parse_time(_value(decoded, "purchaseDate")),
            expiration_date=expiration,
            auto_renew=auto_renew,
            acknowledgement_state=None,
            linked_purchase_token=None,
            bundle_or_package=self.bundle_id,
            event_time=timestamp,
        )

    def _environment(self, name: str):
        from appstoreserverlibrary.models.Environment import Environment

        return Environment.SANDBOX if name == "Sandbox" else Environment.PRODUCTION

    def _api_client(self, environment: str):
        from appstoreserverlibrary.api_client import AppStoreServerAPIClient

        return AppStoreServerAPIClient(
            self.signing_key,
            self.key_id,
            self.issuer_id,
            self.bundle_id,
            self._environment(environment),
        )

    def _verifier(self, environment: str):
        from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier

        online = os.environ.get("SHELFA_APPLE_ENABLE_ONLINE_CHECKS", "true").lower() not in {
            "0",
            "false",
            "no",
        }
        app_apple_id = self.app_apple_id if environment == "Production" else None
        return SignedDataVerifier(
            self.root_certificates,
            online,
            self._environment(environment),
            self.bundle_id,
            app_apple_id,
        )

    def _verify_transaction(self, signed_transaction: str, environment: str | None = None):
        environments = [environment] if environment else ["Production", "Sandbox"]
        last_error = None
        for name in environments:
            if name == "Sandbox" and not self.allow_sandbox:
                continue
            try:
                decoded = self._verifier(name).verify_and_decode_signed_transaction(signed_transaction)
                return decoded, name
            except Exception as error:
                last_error = error
        raise StoreVerificationError("Invalid Apple transaction signature") from last_error

    def _verify_notification(self, signed_payload: str):
        last_error = None
        for name in ("Production", "Sandbox"):
            if name == "Sandbox" and not self.allow_sandbox:
                continue
            try:
                return self._verifier(name).verify_and_decode_notification(signed_payload)
            except Exception as error:
                last_error = error
        raise StoreVerificationError("Invalid Apple notification signature") from last_error

    def _verify_renewal(self, signed_renewal: str, environment: str | None):
        environments = [environment] if environment else ["Production", "Sandbox"]
        last_error = None
        for name in environments:
            try:
                return self._verifier(name).verify_and_decode_renewal_info(signed_renewal)
            except Exception as error:
                last_error = error
        raise StoreVerificationError("Invalid Apple renewal information") from last_error


class GooglePlayClient:
    def __init__(self, package_name: str, service_account_file: str):
        self.package_name = package_name
        self.service_account_file = service_account_file

    @classmethod
    def from_environment(cls) -> "GooglePlayClient":
        package_name = os.environ.get("SHELFA_GOOGLE_PACKAGE_NAME", "com.shelfa")
        account_file = os.environ.get("SHELFA_GOOGLE_SERVICE_ACCOUNT_FILE", "")
        if not account_file:
            raise StoreNotConfigured("Google Play Developer API is not configured")
        return cls(package_name, account_file)

    def _service(self):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        credentials = service_account.Credentials.from_service_account_file(
            self.service_account_file,
            scopes=["https://www.googleapis.com/auth/androidpublisher"],
        )
        return build("androidpublisher", "v3", credentials=credentials, cache_discovery=False)

    def get_subscription(self, purchase_token: str, expected_product_id: str | None = None) -> VerifiedPurchase:
        if not purchase_token:
            raise StoreVerificationError("Missing purchase token")
        try:
            payload = (
                self._service()
                .purchases()
                .subscriptionsv2()
                .get(packageName=self.package_name, token=purchase_token)
                .execute()
            )
        except Exception as error:
            status = getattr(getattr(error, "resp", None), "status", None)
            if status in {400, 404, 410}:
                raise StoreVerificationError("Invalid Google purchase") from error
            raise StoreVerificationError("Google Play verification failed") from error
        return self._parse(payload, purchase_token, expected_product_id)

    def acknowledge(self, product_id: str, purchase_token: str) -> None:
        if product_id not in product_ids().values():
            raise StoreVerificationError("Incorrect product identifier")
        (
            self._service()
            .purchases()
            .subscriptions()
            .acknowledge(
                packageName=self.package_name,
                subscriptionId=product_id,
                token=purchase_token,
                body={},
            )
            .execute()
        )

    def _parse(self, payload: dict, purchase_token: str, expected_product_id: str | None) -> VerifiedPurchase:
        line_items = payload.get("lineItems") or []
        if not line_items:
            raise StoreVerificationError("Invalid Google purchase")
        line = max(line_items, key=lambda item: parse_time(item.get("expiryTime")) or 0)
        product_id = str(line.get("productId") or "")
        if expected_product_id and expected_product_id != product_id:
            raise StoreVerificationError("Incorrect product identifier")
        plan = _require_known_product(product_id, self.package_name, self.package_name)
        state = str(payload.get("subscriptionState") or "")
        status = {
            "SUBSCRIPTION_STATE_PENDING": "PENDING",
            "SUBSCRIPTION_STATE_ACTIVE": "ACTIVE",
            "SUBSCRIPTION_STATE_IN_GRACE_PERIOD": "GRACE_PERIOD",
            "SUBSCRIPTION_STATE_ON_HOLD": "ON_HOLD",
            "SUBSCRIPTION_STATE_PAUSED": "PAUSED",
            "SUBSCRIPTION_STATE_CANCELED": "CANCELED",
            "SUBSCRIPTION_STATE_EXPIRED": "EXPIRED",
            "SUBSCRIPTION_STATE_PENDING_PURCHASE_CANCELED": "EXPIRED",
        }.get(state)
        if status is None:
            raise StoreVerificationError("Invalid Google subscription state")
        auto_renew = bool((line.get("autoRenewingPlan") or {}).get("autoRenewEnabled"))
        if status == "ACTIVE" and not auto_renew:
            status = "CANCELED"
        environment = "Sandbox" if payload.get("testPurchase") is not None else "Production"
        acknowledgement = str(payload.get("acknowledgementState") or "")
        timestamp = int(datetime.now(timezone.utc).timestamp())
        return VerifiedPurchase(
            platform="GOOGLE",
            environment=environment,
            product_id=product_id,
            account_plan=plan,
            status=status,
            purchase_token=purchase_token,
            transaction_id=str(payload.get("latestOrderId") or "") or None,
            original_transaction_id=None,
            start_date=parse_time(payload.get("startTime")),
            expiration_date=parse_time(line.get("expiryTime")),
            auto_renew=auto_renew,
            acknowledgement_state=acknowledgement or None,
            linked_purchase_token=payload.get("linkedPurchaseToken") or None,
            bundle_or_package=self.package_name,
            event_time=timestamp,
        )


def assert_pubsub_authorized(authorization: str | None, query_token: str | None) -> None:
    expected_token = os.environ.get("SHELFA_GOOGLE_PUBSUB_VERIFICATION_TOKEN", "")
    audience = os.environ.get("SHELFA_GOOGLE_PUBSUB_AUDIENCE", "")
    service_account = os.environ.get("SHELFA_GOOGLE_PUBSUB_SERVICE_ACCOUNT", "")
    if not expected_token and not (audience and service_account):
        raise StoreNotConfigured("Google Pub/Sub authentication is not configured")
    if expected_token:
        provided = query_token or ""
        if not hmac.compare_digest(provided, expected_token):
            raise StoreAuthenticationError("Invalid Pub/Sub verification token")
    if audience and service_account:
        header = authorization or ""
        if not header.startswith("Bearer "):
            raise StoreAuthenticationError("Missing Pub/Sub OIDC token")
        token = header.removeprefix("Bearer ").strip()
        try:
            from google.auth.transport import requests as google_requests
            from google.oauth2 import id_token

            claims = id_token.verify_oauth2_token(token, google_requests.Request(), audience)
        except Exception as error:
            raise StoreAuthenticationError("Invalid Pub/Sub OIDC token") from error
        if claims.get("email") != service_account or not claims.get("email_verified", False):
            raise StoreAuthenticationError("Unexpected Pub/Sub service account")


def decode_pubsub_message(body: dict) -> tuple[str, dict]:
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, dict):
        raise StoreVerificationError("Invalid Pub/Sub message")
    message_id = str(message.get("messageId") or "")
    raw = message.get("data")
    if not message_id or not raw:
        raise StoreVerificationError("Invalid Pub/Sub message")
    try:
        decoded = base64.b64decode(raw)
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as error:
        raise StoreVerificationError("Invalid Pub/Sub message") from error
    if not isinstance(payload, dict):
        raise StoreVerificationError("Invalid Pub/Sub message")
    return message_id, payload


def _load_certificates(directory: str) -> list[bytes]:
    path = Path(directory)
    if not path.is_dir():
        return []
    certificates = []
    for certificate in sorted(path.iterdir()):
        if certificate.suffix.lower() in {".cer", ".der", ".pem"}:
            certificates.append(certificate.read_bytes())
    return certificates


def _value(obj, name: str):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)

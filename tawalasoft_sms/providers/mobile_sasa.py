# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

"""Mobile Sasa adapter. Reference: https://docs.mobilesasa.com/sms

Design notes
------------
* Auth is a static bearer token beginning with mbs_. No refresh flow.
* We send on /v2/send/message because it takes our own trackingId and
  returns the billed part count.
* HTTP 200 does not mean the send was accepted. Always read responseCode.
* 0409 blocks an identical message to the same number twice in one day.
  Templates carry the document name so legitimate repeats get through.
* On timeout we must not resend: the message may already be queued at the
  carrier. Return PENDING_CONFIRMATION and resolve via /v1/dlr.

Identifier note
---------------
Two identifiers are in play and they are not interchangeable:

  trackingId          our record name, echoed back on webhooks
  messageId           the gateway's UUID, the key /v1/dlr looks up

Webhooks use the first, lookups the second. Both are stored on the record.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe.utils import get_system_timezone

from tawalasoft_sms.providers.base import (
	CONFIG_ERROR,
	DELIVERED,
	DUPLICATE,
	FAILED,
	NO_BALANCE,
	PENDING_CONFIRMATION,
	QUEUED,
	REJECTED,
	SENT,
	BalanceInfo,
	DeliveryUpdate,
	SendResult,
	SMSProvider,
)

DEFAULT_BASE_URL = "https://api.mobilesasa.com"
DEFAULT_TIMEOUT = 20

# responseCode -> (our status, retryable, needs_lookup)
RESPONSE_CODES = {
	"0200": (SENT, False, False),
	"0201": (SENT, False, False),
	"0401": (CONFIG_ERROR, False, False),   # bad or missing token
	"0402": (NO_BALANCE, True, False),      # nothing billed; retry after top-up
	"0403": (CONFIG_ERROR, False, False),   # token lacks scope
	"0404": (CONFIG_ERROR, False, False),   # unknown sender ID
	"0409": (DUPLICATE, False, False),      # same-day duplicate, deliberate
	"0422": (REJECTED, False, False),       # validation failure, never retry
}

DLR_STATUSES = {
	"delivered": DELIVERED,
	"failed": FAILED,
	"rejected": REJECTED,
	"expired": FAILED,
	"undelivered": FAILED,
	"queued": QUEUED,
	"submitted": SENT,
	"sent": SENT,
}


def _parse_delivery_time(value):
	"""Gateway returns ISO 8601 with an offset. Frappe Datetime columns are
	naive and stored in the site timezone, so convert and drop the offset.
	"""
	if not value:
		return None

	try:
		dt = datetime.fromisoformat(str(value))
	except (ValueError, TypeError):
		return None

	if dt.tzinfo is not None:
		try:
			dt = dt.astimezone(ZoneInfo(get_system_timezone()))
		except Exception:
			pass

		dt = dt.replace(tzinfo=None)

	return dt.strftime("%Y-%m-%d %H:%M:%S")


class MobileSasaProvider(SMSProvider):
	provider_type = "Mobile Sasa"
	supports_tracking_id = True
	supports_webhooks = True

	# --- plumbing ---------------------------------------------------------

	@property
	def base_url(self):
		return (self.doc.base_url or DEFAULT_BASE_URL).rstrip("/")

	@property
	def token(self):
		return self.doc.get_password("api_token")

	def _headers(self):
		return {
			"Authorization": "Bearer {0}".format(self.token),
			"Content-Type": "application/json",
			"Accept": "application/json",
		}

	def _request(self, method, path, payload=None):
		return requests.request(
			method,
			"{0}{1}".format(self.base_url, path),
			headers=self._headers(),
			json=payload,
			timeout=self.doc.timeout or DEFAULT_TIMEOUT,
		)

	# --- sending ----------------------------------------------------------

	def send(self, phone, message, tracking_id, sender_id=None):
		payload = {
			"senderID": sender_id or self.doc.default_sender_id,
			"phone": phone,
			"message": message,
			"trackingId": tracking_id,
		}

		try:
			response = self._request("POST", "/v2/send/message", payload)
		except requests.Timeout:
			return SendResult(
				success=False,
				status=PENDING_CONFIRMATION,
				error_message="Request timed out; outcome unconfirmed",
				needs_lookup=True,
			)
		except requests.RequestException as exc:
			return SendResult(
				success=False,
				status=PENDING_CONFIRMATION,
				error_message=str(exc),
				needs_lookup=True,
			)

		# 429 and 5xx: the gateway did not accept it, so retrying is safe.
		if response.status_code == 429 or response.status_code >= 500:
			return SendResult(
				success=False,
				status=QUEUED,
				error_code=str(response.status_code),
				error_message="Gateway unavailable; will retry with backoff",
				retryable=True,
				raw={"http_status": response.status_code},
			)

		try:
			body = response.json()
		except ValueError:
			return SendResult(
				success=False,
				status=PENDING_CONFIRMATION,
				error_message="Unparseable response from gateway",
				needs_lookup=True,
				raw={"http_status": response.status_code},
			)

		code = str(body.get("responseCode") or "")
		status, retryable, needs_lookup = RESPONSE_CODES.get(code, (FAILED, False, False))
		accepted = bool(body.get("status")) and code in ("0200", "0201")

		return SendResult(
			success=accepted,
			status=status,
			provider_message_id=body.get("messageId"),
			parts=int(body.get("cost") or 0),
			error_code=None if accepted else code,
			error_message=None if accepted else body.get("message"),
			retryable=retryable,
			needs_lookup=needs_lookup,
			raw=body,
		)

	# --- delivery reports -------------------------------------------------

	def parse_webhook(self, payload, headers=None):
		"""One status per callback: {trackingId, phone, status, deliveredAt}."""
		if not payload or not payload.get("trackingId"):
			return []

		raw_status = str(payload.get("status") or "").strip().lower()

		if raw_status not in DLR_STATUSES:
			return []

		return [
			DeliveryUpdate(
				tracking_id=payload.get("trackingId"),
				status=DLR_STATUSES[raw_status],
				phone=payload.get("phone"),
				delivered_at=_parse_delivery_time(payload.get("deliveredAt")),
				reason=payload.get("reason"),
				raw=payload,
			)
		]

	def fetch_status(self, tracking_id, provider_message_id=None):
		"""Look up one message.

		Keys on the gateway's messageId, not our trackingId. A successful
		response carries no responseCode — only status: true and a messages
		array, with the delivery state nested under deliveryStatus. The
		top-level "message" field holds the SMS body, not a status.
		"""
		lookup_id = provider_message_id or tracking_id

		if not lookup_id:
			return None

		try:
			response = self._request("POST", "/v1/dlr", {"messageId": lookup_id})
			body = response.json()
		except (requests.RequestException, ValueError):
			return None

		if not body.get("status"):
			return None

		messages = body.get("messages") or []

		if not messages:
			return None

		entry = messages[0]
		delivery = entry.get("deliveryStatus") or {}
		raw_status = str(delivery.get("status") or "").strip().lower()

		if raw_status not in DLR_STATUSES:
			return None

		return DeliveryUpdate(
			tracking_id=tracking_id,
			status=DLR_STATUSES[raw_status],
			phone=entry.get("phone"),
			delivered_at=_parse_delivery_time(delivery.get("deliveryTime")),
			reason=delivery.get("reason") or delivery.get("failureReason"),
			raw=body,
		)

	def register_callback_url(self, url):
		"""POST /v2/companies/ sets the account's DLR webhook URL.

		Requires a token with the team:settings:manage scope. A send-only
		token returns 0403 — set the URL in the provider portal instead.
		"""
		try:
			response = self._request("POST", "/v2/companies/", {"callbackUrl": url})
			body = response.json()
		except (requests.RequestException, ValueError) as exc:
			frappe.throw("Could not reach the gateway: {0}".format(exc))

		if str(body.get("responseCode")) in ("0200", "0201"):
			return True

		frappe.throw(
			"Gateway refused the callback registration ({0}): {1}".format(
				body.get("responseCode"), body.get("message")
			)
		)

	# --- balance ----------------------------------------------------------

	def get_balance(self):
		response = self._request("GET", "/v1/get-balance/account-details")
		body = response.json()

		return BalanceInfo(
			units=float(body.get("balance") or 0),
			rate=body.get("rate"),
			account_name=body.get("accountName") or body.get("account_name"),
			raw=body,
		)
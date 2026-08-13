# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

"""Public entry points.

Everything that sends an SMS goes through queue_sms(). Nothing calls an
adapter directly, and no document save ever waits on an HTTP call.
"""

import json

import frappe
from frappe.utils import add_to_date, now_datetime, today

from tawalasoft_sms.providers import get_provider
from tawalasoft_sms.providers.base import (
	FINAL_STATUSES,
	NO_BALANCE,
	PENDING_CONFIRMATION,
	QUEUED,
)
from tawalasoft_sms.utils.phone import is_valid_ke_mobile, normalise

SETTINGS = "Tawalasoft SMS Settings"
MESSAGE = "Tawalasoft SMS Message"

# Statuses proving the gateway accepted the message.
ACCEPTED_STATUSES = ("Sent", "Delivered")


# --- outbound -------------------------------------------------------------


@frappe.whitelist()
def queue_sms(phone, message, reference_doctype=None, reference_name=None,
              template=None, provider=None, sender_id=None,
              notification_rule=None, allow_duplicate=False):
	"""Create a message record and hand it to the background queue.

	Returns the record name, which is also the trackingId sent to the
	gateway, so delivery reports join straight back to it.
	"""
	settings = frappe.get_cached_doc(SETTINGS)

	if not settings.enabled:
		return None

	number = normalise(phone)

	if not is_valid_ke_mobile(number):
		frappe.throw("{0} is not a valid mobile number.".format(phone))

	if is_opted_out(number):
		frappe.logger("tawalasoft_sms").info("Opted out; dropped message to %s", number)
		return None

	if settings.test_mode and settings.test_mode_phone:
		number = settings.test_mode_phone

	# The gateway refuses an identical message to the same number twice in one
	# day. Catch it here so a duplicate costs a database lookup rather than an
	# API call and a wasted record.
	if not allow_duplicate and already_sent_today(number, message):
		frappe.logger("tawalasoft_sms").info(
			"Identical message already sent today; skipped %s", number
		)
		return None

	doc = frappe.get_doc({
		"doctype": MESSAGE,
		"phone": number,
		"message": message,
		"status": QUEUED,
		"template": template,
		"provider": provider,
		"sender_id": sender_id,
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"notification_rule": notification_rule,
	}).insert(ignore_permissions=True)

	enqueue_dispatch(doc.name)

	return doc.name


def enqueue_dispatch(sms_message):
	frappe.enqueue(
		"tawalasoft_sms.api.dispatch_sms",
		queue="short",
		job_name="tsms-{0}".format(sms_message),
		sms_message=sms_message,
		enqueue_after_commit=True,
	)


def dispatch_sms(sms_message):
	"""Background worker. One record, one send attempt.

	Idempotent by design: a record the gateway has already accepted is never
	sent again, no matter how many jobs fire for it.
	"""
	doc = frappe.get_doc(MESSAGE, sms_message)

	# Evidence the gateway took it. Beats checking status alone, which would
	# let a stale queued job re-send a message already marked Sent.
	if doc.provider_message_id or doc.sent_at:
		return doc.status

	if doc.status in FINAL_STATUSES or doc.status == PENDING_CONFIRMATION:
		return doc.status

	settings = frappe.get_cached_doc(SETTINGS)
	provider = get_provider(doc.provider)

	result = provider.send(
		phone=doc.phone,
		message=doc.message,
		tracking_id=doc.name,
		sender_id=doc.sender_id,
	)

	doc.status = result.status
	doc.provider_message_id = result.provider_message_id or doc.provider_message_id
	doc.parts = result.parts or doc.parts
	doc.error_code = result.error_code
	doc.error_message = result.error_message
	doc.attempts = (doc.attempts or 0) + 1
	doc.provider_response = json.dumps(result.raw, indent=2, default=str)

	if not doc.provider:
		doc.provider = provider.doc.name

	if result.success:
		doc.sent_at = now_datetime()
		doc.next_attempt_at = None
	elif result.retryable and doc.attempts < (settings.max_attempts or 3):
		doc.next_attempt_at = add_to_date(
			now_datetime(), minutes=settings.retry_interval_minutes or 10
		)
	else:
		doc.next_attempt_at = None

	doc.save(ignore_permissions=True)
	frappe.db.commit()

	if result.status == NO_BALANCE:
		notify_low_balance(doc)

	return result.status


# --- inbound: delivery reports -------------------------------------------


@frappe.whitelist(allow_guest=True)
def delivery_report(secret=None, **kwargs):
	"""Delivery report webhook receiver.

	Callbacks are unsigned, so the URL carries a secret held in settings.
	Must answer 200 within five seconds, so this only validates and enqueues.
	"""
	expected = frappe.db.get_single_value(SETTINGS, "webhook_secret")

	if not expected or secret != expected:
		frappe.local.response["http_status_code"] = 401
		return {"ok": False}

	payload = dict(frappe.local.form_dict or {})
	payload.pop("secret", None)
	payload.pop("cmd", None)

	frappe.enqueue(
		"tawalasoft_sms.api.process_delivery_report",
		queue="short",
		payload=payload,
	)

	return {"ok": True}


def process_delivery_report(payload):
	provider = get_provider()

	for update in provider.parse_webhook(payload):
		apply_delivery_update(update)

	frappe.db.commit()


def apply_delivery_update(update):
	"""Idempotent. Webhooks are at-least-once and can arrive out of order."""
	if not frappe.db.exists(MESSAGE, update.tracking_id):
		return

	doc = frappe.get_doc(MESSAGE, update.tracking_id)

	if doc.status == update.status or doc.status in FINAL_STATUSES:
		return

	doc.status = update.status
	doc.failure_reason = update.reason

	if update.delivered_at:
		doc.delivered_at = update.delivered_at

	doc.save(ignore_permissions=True)


# --- helpers --------------------------------------------------------------


def already_sent_today(number, message):
	"""Mirror the gateway's same-day duplicate rule locally."""
	return bool(frappe.db.exists(MESSAGE, {
		"phone": number,
		"message": message,
		"status": ["in", ACCEPTED_STATUSES],
		"creation": [">=", today()],
	}))


def is_opted_out(number):
	return bool(
		frappe.db.exists("Tawalasoft SMS OptOut", {"phone": number, "active": 1})
	)


def notify_low_balance(doc):
	recipients = frappe.db.get_single_value(SETTINGS, "balance_alert_recipients")

	if not recipients:
		return

	frappe.sendmail(
		recipients=[r.strip() for r in recipients.split(",") if r.strip()],
		subject="SMS credit exhausted",
		message=(
			"Message {0} could not be sent: the gateway reported insufficient "
			"balance. Nothing was billed. Top up and the message will retry."
		).format(doc.name),
	)
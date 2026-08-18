# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

"""Scheduled jobs.

  reconcile_pending_messages - resolve sends whose outcome is unknown
  poll_sent_messages         - confirm delivery the webhook missed
  retry_failed_messages      - re-attempt only what the gateway never took
  check_balance              - watch credit and alert before it runs out
"""

import frappe
from frappe.utils import add_to_date, now_datetime, time_diff_in_hours

from tawalasoft_sms.api import apply_delivery_update, enqueue_dispatch
from tawalasoft_sms.providers import get_provider
from tawalasoft_sms.providers.base import PENDING_CONFIRMATION

SETTINGS = "Tawalasoft SMS Settings"
MESSAGE = "Tawalasoft SMS Message"

MAX_LOOKUPS = 2
RESEND_WINDOW_HOURS = 20  # inside the gateway's 24h duplicate window


def reconcile_pending_messages():
	"""Resolve messages whose send call failed mid-flight.

	These have no provider_message_id by definition — no response came back —
	so the lookup is by our record name and will usually return nothing.
	That silence is the signal: the request most likely never landed.
	"""
	settings = frappe.get_cached_doc(SETTINGS)

	if not settings.enabled:
		return

	cutoff = add_to_date(
		now_datetime(), minutes=-1 * (settings.reconcile_after_minutes or 5)
	)

	pending = frappe.get_all(
		MESSAGE,
		filters={"status": PENDING_CONFIRMATION, "modified": ["<", cutoff]},
		fields=["name", "provider", "lookup_attempts", "provider_response",
		        "attempts", "creation"],
		limit=200,
	)

	stale = []

	for row in pending:
		try:
			update = get_provider(row.provider).fetch_status(row.name)
		except Exception:
			frappe.log_error(
				title="SMS reconciliation failed",
				message="Message {0}\n\n{1}".format(row.name, frappe.get_traceback()),
			)
			continue

		if update:
			apply_delivery_update(update)
			continue

		count = (row.lookup_attempts or 0) + 1
		frappe.db.set_value(MESSAGE, row.name, "lookup_attempts", count)

		if count < MAX_LOOKUPS:
			continue

		if can_safely_resend(row, settings):
			requeue(row.name)
		else:
			stale.append(row.name)

	frappe.db.commit()

	if stale:
		notify_unresolved(stale)


def can_safely_resend(row, settings):
	"""All three conditions must hold."""

	# 1. No bytes came back, so the gateway most likely never got the request.
	if not is_blank_response(row.provider_response):
		return False

	# 2. Still inside the duplicate-protection window, which caps the cost of
	#    being wrong: a resend that did land comes back Duplicate.
	if time_diff_in_hours(now_datetime(), row.creation) > RESEND_WINDOW_HOURS:
		return False

	# 3. Not looping.
	if (row.attempts or 0) >= (settings.max_attempts or 3):
		return False

	return True


def is_blank_response(raw):
	"""True when the adapter recorded no gateway response at all."""
	return not raw or str(raw).strip() in ("", "{}", "null", "None")


def requeue(name):
	frappe.db.set_value(MESSAGE, name, {
		"status": "Queued",
		"error_message": None,
		"next_attempt_at": None,
		"lookup_attempts": 0,
	})
	frappe.db.commit()
	enqueue_dispatch(name)


def notify_unresolved(names):
	"""Alert rather than auto-resend.

	Outside the duplicate window the gateway's silence has two causes: it
	never received the request, or its lookup endpoint is unhealthy. A human
	can tell them apart from the provider portal; this job cannot.
	"""
	recipients = frappe.db.get_single_value(SETTINGS, "balance_alert_recipients")

	if not recipients:
		return

	frappe.sendmail(
		recipients=[r.strip() for r in recipients.split(",") if r.strip()],
		subject="{0} SMS messages could not be confirmed".format(len(names)),
		message=(
			"These messages failed mid-send and the gateway has no record of "
			"them after {0} lookups:<br><br>{1}<br><br>"
			"Check the provider portal. If they were not delivered, requeue "
			"with requeue_pending_confirmation(dry_run=False)."
		).format(MAX_LOOKUPS, "<br>".join(names)),
	)


def poll_sent_messages():
	"""Confirm delivery for messages the webhook has not resolved.

	Keys on provider_message_id, which is what /v1/dlr looks up. The five
	minute cutoff gives the webhook first attempt, so this only picks up what
	slipped through.
	"""
	settings = frappe.get_cached_doc(SETTINGS)

	if not settings.enabled:
		return

	cutoff = add_to_date(now_datetime(), minutes=-5)

	rows = frappe.get_all(
		MESSAGE,
		filters={
			"status": "Sent",
			"sent_at": ["<", cutoff],
			"provider_message_id": ["is", "set"],
		},
		fields=["name", "provider", "provider_message_id"],
		limit=100,
	)

	for row in rows:
		try:
			update = get_provider(row.provider).fetch_status(
				row.name, row.provider_message_id
			)

			if update:
				apply_delivery_update(update)
		except Exception:
			frappe.log_error(
				title="SMS status poll failed",
				message="Message {0}\n\n{1}".format(row.name, frappe.get_traceback()),
			)

	frappe.db.commit()


def retry_failed_messages():
	"""Re-queue messages parked for a later attempt.

	The provider_message_id filter is the safety rail: anything the gateway
	accepted is excluded outright, so this cannot cause a double-send.
	"""
	settings = frappe.get_cached_doc(SETTINGS)

	if not settings.enabled:
		return

	due = frappe.get_all(
		MESSAGE,
		filters={
			"status": ["in", ["Queued", "Insufficient Balance"]],
			"next_attempt_at": ["<=", now_datetime()],
			"provider_message_id": ["is", "not set"],
			"sent_at": ["is", "not set"],
			"attempts": ["<", settings.max_attempts or 3],
		},
		pluck="name",
		limit=200,
	)

	for name in due:
		# Clear the marker first so a slow queue cannot double-enqueue.
		frappe.db.set_value(MESSAGE, name, "next_attempt_at", None)
		enqueue_dispatch(name)

	frappe.db.commit()


def check_balance():
	"""Poll credit and alert below the threshold."""
	settings = frappe.get_cached_doc(SETTINGS)

	if not settings.enabled:
		return

	try:
		info = get_provider().get_balance()
	except Exception:
		frappe.log_error(
			title="SMS balance check failed", message=frappe.get_traceback()
		)
		return

	previous = settings.last_known_balance or 0
	threshold = settings.balance_alert_threshold or 0

	frappe.db.set_value(SETTINGS, SETTINGS, {
		"last_known_balance": int(info.units),
		"balance_checked_on": now_datetime(),
	})
	frappe.db.commit()

	# Alert on the crossing, not on every poll, or you get an email every
	# fifteen minutes until someone tops up.
	if threshold and info.units < threshold <= previous:
		send_balance_alert(settings, info)


def send_balance_alert(settings, info):
	recipients = [
		r.strip()
		for r in (settings.balance_alert_recipients or "").split(",")
		if r.strip()
	]

	if not recipients:
		return

	frappe.sendmail(
		recipients=recipients,
		subject="SMS credit low: {0} units remaining".format(int(info.units)),
		message=(
			"SMS credit has fallen below the alert threshold of {0} units.<br><br>"
			"Remaining: <b>{1}</b> units.<br><br>"
			"Top up before it reaches zero, or delivery notifications will "
			"stop going out."
		).format(int(settings.balance_alert_threshold), int(info.units)),
	)
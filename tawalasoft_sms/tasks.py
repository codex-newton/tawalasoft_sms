# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

"""Scheduled jobs.

Three concerns, deliberately separate:
  reconcile_pending_messages - resolve sends whose outcome is unknown
  retry_failed_messages      - re-attempt only what is genuinely retryable
  check_balance              - watch credit and alert before it runs out
"""

import frappe
from frappe.utils import add_to_date, now_datetime

from tawalasoft_sms.api import apply_delivery_update, enqueue_dispatch
from tawalasoft_sms.providers import get_provider
from tawalasoft_sms.providers.base import PENDING_CONFIRMATION

SETTINGS = "Tawalasoft SMS Settings"
MESSAGE = "Tawalasoft SMS Message"


def reconcile_pending_messages():
	"""Resolve messages whose send call failed mid-flight.

	These must never be resent: the message may already be with the carrier.
	We ask the gateway what happened, using our own record name as the
	tracking id.
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
		fields=["name", "provider"],
		limit=200,
	)

	for row in pending:
		try:
			provider = get_provider(row.provider)
			update = provider.fetch_status(row.name)

			if update:
				apply_delivery_update(update)
		except Exception:
			frappe.log_error(
				title="SMS reconciliation failed",
				message="Message {0}\n\n{1}".format(row.name, frappe.get_traceback()),
			)

	frappe.db.commit()


def retry_failed_messages():
	"""Re-queue messages parked for a later attempt.

	Only statuses the adapter marked retryable get a next_attempt_at, so
	this cannot resend a timeout or a rejected message.
	"""
	settings = frappe.get_cached_doc(SETTINGS)

	if not settings.enabled:
		return

	due = frappe.get_all(
		MESSAGE,
		filters={
			"next_attempt_at": ["<=", now_datetime()],
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

	# Alert on the crossing, not on every poll, or you send an email every
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
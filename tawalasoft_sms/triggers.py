# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

"""Document event handlers.

Deliberately thin. Whether to send, and what to say, lives in the rule and
template records so wording changes need no deployment.
"""

import frappe

from tawalasoft_sms.api import queue_sms
from tawalasoft_sms.utils.phone import is_valid_ke_mobile, normalise, resolve_from_document

RULE = "Tawalasoft SMS Notification Rule"


def on_submit(doc, method=None):
	run_rules(doc, "On Submit")


def on_cancel(doc, method=None):
	run_rules(doc, "On Cancel")


def after_insert(doc, method=None):
	run_rules(doc, "After Insert")


def on_update_after_submit(doc, method=None):
	run_rules(doc, "On Update After Submit")


def run_rules(doc, event):
	rules = frappe.get_all(
		RULE,
		filters={"reference_doctype": doc.doctype, "event": event, "enabled": 1},
		fields=[
			"name", "template", "condition", "sender_id", "provider",
			"recipient_type", "recipient_field", "static_recipients",
		],
		order_by="priority asc",
	)

	if not rules:
		return

	if not passes_guards(doc):
		return

	for rule in rules:
		try:
			apply_rule(doc, rule)
		except Exception:
			# A messaging failure must never block the transaction.
			frappe.log_error(
				title="SMS rule failed",
				message="Rule {0} on {1} {2}\n\n{3}".format(
					rule.name, doc.doctype, doc.name, frappe.get_traceback()
				),
			)


def apply_rule(doc, rule):
	if rule.condition and not evaluate_condition(rule.condition, doc):
		return

	message = render_template(rule.template, doc)

	if not message:
		return

	for number in resolve_recipients(doc, rule):
		queue_sms(
			phone=number,
			message=message,
			reference_doctype=doc.doctype,
			reference_name=doc.name,
			template=rule.template,
			provider=rule.provider,
			sender_id=rule.sender_id,
			notification_rule=rule.name,
		)


def passes_guards(doc):
	"""Hard guards that hold regardless of how a rule is configured."""

	# A return must never tell a customer their goods are on the way.
	if doc.get("is_return"):
		return False

	# Sales Invoice on_submit also fires for POS sales and for the
	# consolidated invoice created at POS closing. Neither should text.
	if doc.doctype == "Sales Invoice" and (doc.get("is_pos") or doc.get("is_consolidated")):
		return False

	return True


def resolve_recipients(doc, rule):
	numbers = []

	if rule.recipient_type == "Static Numbers":
		numbers = [n.strip() for n in (rule.static_recipients or "").split(",")]
	elif rule.recipient_type == "Field on Document":
		numbers = [doc.get(rule.recipient_field)]
	else:
		numbers = [resolve_from_document(doc)]

	valid = [normalise(n) for n in numbers if n and is_valid_ke_mobile(n)]

	if not valid:
		frappe.logger("tawalasoft_sms").info(
			"No valid recipient for %s %s (rule %s)", doc.doctype, doc.name, rule.name
		)

	return valid


def evaluate_condition(condition, doc):
	try:
		return bool(frappe.safe_eval(condition, None, {"doc": doc.as_dict()}))
	except Exception:
		frappe.log_error(
			title="SMS rule condition failed",
			message="Condition: {0}\nDocument: {1} {2}".format(
				condition, doc.doctype, doc.name
			),
		)
		return False


def render_template(template_name, doc):
	if not template_name:
		return None

	template = frappe.get_cached_doc("Tawalasoft SMS Template", template_name)

	if not template.enabled:
		return None

	try:
		return frappe.render_template(template.message, {"doc": doc.as_dict()})
	except Exception:
		frappe.log_error(
			title="SMS template render failed",
			message="Template: {0}\nDocument: {1} {2}".format(
				template_name, doc.doctype, doc.name
			),
		)
		return None
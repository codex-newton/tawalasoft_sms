# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from tawalasoft_sms.utils.phone import is_valid_ke_mobile, normalise


class TawalasoftSMSNotificationRule(Document):
	def validate(self):
		self.validate_template_matches_doctype()
		self.validate_condition()
		self.validate_static_recipients()

	def validate_template_matches_doctype(self):
		"""A template written for Delivery Note will reference fields that do
		not exist on Sales Invoice. Catch the mismatch here, not at send time.
		"""
		if not self.template:
			return

		template_doctype = frappe.db.get_value(
			"Tawalasoft SMS Template", self.template, "reference_doctype"
		)

		if template_doctype and template_doctype != self.reference_doctype:
			frappe.throw(
				"Template {0} is written for {1}, but this rule fires on {2}.".format(
					self.template, template_doctype, self.reference_doctype
				)
			)

	def validate_condition(self):
		"""Compile-check the expression so a typo fails on save rather than
		silently swallowing every notification later.
		"""
		if not self.condition:
			return

		try:
			compile(self.condition, "<condition>", "eval")
		except SyntaxError as exc:
			frappe.throw("Condition is not a valid Python expression: {0}".format(exc))

	def validate_static_recipients(self):
		if self.recipient_type != "Static Numbers":
			return

		cleaned = []

		for entry in (self.static_recipients or "").split(","):
			entry = entry.strip()

			if not entry:
				continue

			if not is_valid_ke_mobile(entry):
				frappe.throw("{0} is not a valid mobile number.".format(entry))

			cleaned.append(normalise(entry))

		if not cleaned:
			frappe.throw("Enter at least one recipient number.")

		self.static_recipients = ", ".join(cleaned)
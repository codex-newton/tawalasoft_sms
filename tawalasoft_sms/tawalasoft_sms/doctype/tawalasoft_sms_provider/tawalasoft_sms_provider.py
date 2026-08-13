# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class TawalasoftSMSProvider(Document):
	def validate(self):
		self.base_url = (self.base_url or "").rstrip("/")
		self.enforce_single_default()
		self.warn_on_suspicious_token()

	def enforce_single_default(self):
		if not self.is_default:
			return

		others = frappe.get_all(
			"Tawalasoft SMS Provider",
			filters={"is_default": 1, "name": ["!=", self.name]},
			pluck="name",
		)

		for other in others:
			frappe.db.set_value("Tawalasoft SMS Provider", other, "is_default", 0)

	def warn_on_suspicious_token(self):
		if self.provider_type != "Mobile Sasa":
			return

		token = self.api_token or ""

		if not token or set(token) == {"*"}:
			return

		if not token.startswith("mbs_"):
			frappe.msgprint(
				"Mobile Sasa tokens normally begin with <b>mbs_</b>. "
				"Check you pasted the API token and not the System ID.",
				indicator="orange",
				title="Check the token",
			)
# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

import secrets

import frappe
from frappe.model.document import Document
from frappe.utils import get_url

from tawalasoft_sms.utils.phone import is_valid_ke_mobile, normalise

WEBHOOK_METHOD = "tawalasoft_sms.api.delivery_report"


class TawalasoftSMSSettings(Document):
	def validate(self):
		self.ensure_webhook_secret()
		self.build_webhook_url()
		self.validate_test_mode_phone()

	def ensure_webhook_secret(self):
		"""Generate on first save. Clearing the field and saving rotates it."""
		if not self.webhook_secret:
			self.webhook_secret = secrets.token_urlsafe(32)

	def build_webhook_url(self):
		self.webhook_url = "{0}/api/method/{1}?secret={2}".format(
			get_url(), WEBHOOK_METHOD, self.webhook_secret
		)

	def validate_test_mode_phone(self):
		if not self.test_mode:
			return

		if not is_valid_ke_mobile(self.test_mode_phone):
			frappe.throw("Test Mode Phone is not a valid mobile number.")

		self.test_mode_phone = normalise(self.test_mode_phone)
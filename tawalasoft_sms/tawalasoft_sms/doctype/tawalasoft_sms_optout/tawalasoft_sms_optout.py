# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from tawalasoft_sms.utils.phone import is_valid_ke_mobile, normalise


class TawalasoftSMSOptOut(Document):
	def validate(self):
		self.normalise_phone()
		self.stamp_opt_out_time()

	def normalise_phone(self):
		"""The send path looks this number up in normalised form. If a user
		types 0712345678 and we store it verbatim, the lookup silently misses
		and the customer keeps receiving messages.
		"""
		if not is_valid_ke_mobile(self.phone):
			frappe.throw("{0} is not a valid mobile number.".format(self.phone))

		self.phone = normalise(self.phone)

	def stamp_opt_out_time(self):
		if self.active and not self.opted_out_on:
			self.opted_out_on = now_datetime()

		if not self.active:
			self.opted_out_on = None
# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from tawalasoft_sms.providers.base import FINAL_STATUSES
from tawalasoft_sms.utils.phone import is_valid_ke_mobile, normalise
from tawalasoft_sms.utils.segments import count


class TawalasoftSMSMessage(Document):
	def validate(self):
		self.normalise_phone()
		self.set_estimated_parts()
		self.protect_final_status()

	def normalise_phone(self):
		if not is_valid_ke_mobile(self.phone):
			frappe.throw("{0} is not a valid mobile number.".format(self.phone))

		self.phone = normalise(self.phone)

	def set_estimated_parts(self):
		"""Estimate before sending. The gateway's own count replaces this
		once the send is accepted.
		"""
		if not self.parts:
			_chars, parts, _encoding = count(self.message)
			self.parts = parts

	def protect_final_status(self):
		"""Delivery reports arrive at least once and can arrive out of order.
		A message that reached a final state must never be walked back.
		"""
		if self.is_new():
			return

		previous = self.get_doc_before_save()

		if not previous:
			return

		if previous.status in FINAL_STATUSES and self.status != previous.status:
			frappe.throw(
				"Message {0} is already {1} and cannot be changed to {2}.".format(
					self.name, previous.status, self.status
				)
			)

	def on_trash(self):
		"""Deleting a sent message destroys the audit trail and the cost
		record. Queued or failed entries are fine to clear.
		"""
		if self.status in ("Sent", "Delivered"):
			frappe.throw(
				"Message {0} was sent and cannot be deleted. "
				"It is part of the audit trail.".format(self.name)
			)
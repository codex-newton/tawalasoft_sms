# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from tawalasoft_sms.utils.segments import count


class TawalasoftSMSTemplate(Document):
	def validate(self):
		self.require_document_name()
		self.render_preview()

	def require_document_name(self):
		"""The gateway blocks an identical message to the same number twice in
		one day. Without the document number in the body, a customer with two
		deliveries on one day would receive only the first notification.
		"""
		if "doc.name" not in (self.message or ""):
			frappe.throw(
				"The message must include <b>{{ doc.name }}</b> so that two "
				"documents for the same customer on the same day are not "
				"blocked as duplicate messages."
			)

	def render_preview(self):
		sample = self.get_sample_context()

		try:
			rendered = frappe.render_template(self.message, {"doc": sample})
		except Exception as exc:
			frappe.throw("Template failed to render: {0}".format(exc))

		self.rendered_preview = rendered
		self.estimated_characters, self.estimated_parts, self.encoding = count(rendered)

		if self.estimated_parts > 1:
			frappe.msgprint(
				"This message renders as <b>{0} parts</b> and will be billed "
				"{0} times per recipient.".format(self.estimated_parts),
				indicator="orange",
				title="Multi-part message",
			)

	def get_sample_context(self):
		"""Render against a real document if one is chosen, otherwise against
		placeholder values so the preview still works on a new template.

		The real document is returned as a Document, not as_dict(): on a dict,
		doc.items resolves to the built-in items() method rather than the
		child table.
		"""
		if self.reference_doctype and self.sample_document:
			if frappe.db.exists(self.reference_doctype, self.sample_document):
				return frappe.get_doc(self.reference_doctype, self.sample_document)

		return frappe._dict({
			"name": "XXX-DN-2026-00000",
			"customer_name": "SAMPLE CUSTOMER LIMITED",
			"company": "Sample Company Ltd",
			"driver_name": "Sample Driver",
			"vehicle_no": "KXX 000X",
			"shipping_address_name": None,
			"total_qty": 2,
			"items": [
				frappe._dict({"item_name": "1000Ltr Tank", "qty": 1}),
				frappe._dict({"item_name": "5000Ltr Tank", "qty": 1}),
			],
			"grand_total": 0.00,
			"due_date": "2026-01-01",
			"posting_date": "2026-01-01",
		})
# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

"""Phone normalisation.

Mobile Sasa accepts 07..., 2547... and +2547... interchangeably, so this is
not strictly needed for the API call itself. It IS needed for everything
around it: opt-out matching, duplicate detection, and joining delivery
report payloads (which come back as 2547...) to our records.

Normalise once, on the way in. Store one format only.
"""

import re

DEFAULT_COUNTRY_CODE = "254"

# Kenyan mobile ranges: 07xx across all networks, plus the newer 01xx block.
KE_MOBILE = re.compile(r"^254(7\d{8}|1\d{8})$")


def clean(raw):
	"""Strip formatting noise, keeping digits and a leading plus."""
	if not raw:
		return ""

	value = str(raw).strip()
	value = re.sub(r"[\s\-\(\)\.]", "", value)
	value = re.sub(r"[^\d\+]", "", value)

	return value


def normalise(raw, country_code=None):
	"""Return a bare international number such as 254712345678, or None.

	Handles 0712345678, 712345678, +254712345678, 254712345678 and
	00254712345678.
	"""
	country_code = country_code or get_country_code()
	value = clean(raw)

	if not value:
		return None

	if value.startswith("+"):
		value = value[1:]

	if value.startswith("00"):
		value = value[2:]

	if value.startswith("0"):
		value = country_code + value[1:]
	elif len(value) == 9 and value[0] in ("7", "1"):
		# A local number whose leading zero was eaten by a spreadsheet that
		# treated the column as numeric. Very common in imported data.
		value = country_code + value
	elif not value.startswith(country_code) and len(value) <= 10:
		value = country_code + value

	return value or None


def get_country_code():
	"""Read from settings, falling back to Kenya. Wrapped because this module
	is imported by controllers that may run before the Single exists.
	"""
	try:
		import frappe

		return (
			frappe.db.get_single_value(
				"Tawalasoft SMS Settings", "default_country_code"
			)
			or DEFAULT_COUNTRY_CODE
		)
	except Exception:
		return DEFAULT_COUNTRY_CODE


def is_valid_ke_mobile(raw):
	value = normalise(raw)

	return bool(value and KE_MOBILE.match(value))


def resolve_from_document(doc):
	"""Best-effort recipient lookup for a submitted transaction.

	Order: the document's own contact fields, then its linked Contact, then
	the Customer's primary mobile. Returns a normalised number or None.
	"""
	import frappe

	candidates = [doc.get("contact_mobile"), doc.get("contact_phone")]

	if doc.get("contact_person"):
		contact = frappe.db.get_value(
			"Contact", doc.contact_person, ["mobile_no", "phone"], as_dict=True
		)

		if contact:
			candidates += [contact.mobile_no, contact.phone]

	if doc.get("customer"):
		candidates.append(frappe.db.get_value("Customer", doc.customer, "mobile_no"))

	for candidate in candidates:
		if is_valid_ke_mobile(candidate):
			return normalise(candidate)

	return None
# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

"""GSM-7 vs UCS-2 segment counting.

A single SMS carries 160 GSM-7 characters, or 70 if any character forces
Unicode. Longer texts split into parts of 153 (67 Unicode) and are billed
per part. A handful of characters cost two septets each in GSM-7.
"""

GSM7_BASIC = (
	"@\u00a3$\u00a5\u00e8\u00e9\u00f9\u00ec\u00f2\u00c7\n\u00d8\u00f8\r\u00c5\u00e5"
	"\u0394_\u03a6\u0393\u039b\u03a9\u03a0\u03a8\u03a3\u0398\u039e\u00c6\u00e6\u00df\u00c9"
	" !\"#\u00a4%&'()*+,-./0123456789:;<=>?"
	"\u00a1ABCDEFGHIJKLMNOPQRSTUVWXYZ\u00c4\u00d6\u00d1\u00dc\u00a7"
	"\u00bfabcdefghijklmnopqrstuvwxyz\u00e4\u00f6\u00f1\u00fc\u00e0"
)

# Each of these occupies two GSM-7 septets.
GSM7_EXTENDED = "^{}\\[~]|\u20ac"

GSM7_SINGLE_LIMIT = 160
GSM7_MULTI_LIMIT = 153
UCS2_SINGLE_LIMIT = 70
UCS2_MULTI_LIMIT = 67


def is_gsm7(text):
	return all(char in GSM7_BASIC or char in GSM7_EXTENDED for char in text or "")


def septet_length(text):
	"""Billable length in GSM-7, counting extended characters twice."""
	total = 0

	for char in text or "":
		total += 2 if char in GSM7_EXTENDED else 1

	return total


def count(text):
	"""Return (characters, parts, encoding) for a message body."""
	text = text or ""

	if is_gsm7(text):
		length = septet_length(text)
		single, multi, encoding = GSM7_SINGLE_LIMIT, GSM7_MULTI_LIMIT, "GSM-7"
	else:
		length = len(text)
		single, multi, encoding = UCS2_SINGLE_LIMIT, UCS2_MULTI_LIMIT, "UCS-2"

	if length == 0:
		parts = 0
	elif length <= single:
		parts = 1
	else:
		parts = -(-length // multi)

	return length, parts, encoding
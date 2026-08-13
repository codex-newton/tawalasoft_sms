# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

"""Provider registry.

Adding a gateway is: write an adapter, register it here or via the
sms_providers hook, add the option to Tawalasoft SMS Provider.provider_type.
No other code changes.
"""

import frappe

from tawalasoft_sms.providers.mobile_sasa import MobileSasaProvider

BUILTIN_PROVIDERS = {
	MobileSasaProvider.provider_type: MobileSasaProvider,
}


def get_provider_classes():
	classes = dict(BUILTIN_PROVIDERS)

	# Another app may contribute adapters:
	#   sms_providers = {"Africa's Talking": "myapp.providers.at.ATProvider"}
	for app_map in frappe.get_hooks("sms_providers") or []:
		if isinstance(app_map, dict):
			for name, dotted_path in app_map.items():
				classes[name] = frappe.get_attr(dotted_path)

	return classes


def get_provider(provider_name=None):
	"""Return an instantiated adapter for the named provider record, or for
	the default provider when none is given.
	"""
	if not provider_name:
		provider_name = frappe.db.get_value(
			"Tawalasoft SMS Provider", {"enabled": 1, "is_default": 1}, "name"
		)

	if not provider_name:
		frappe.throw("No default SMS provider is configured and enabled.")

	doc = frappe.get_cached_doc("Tawalasoft SMS Provider", provider_name)

	if not doc.enabled:
		frappe.throw("SMS provider {0} is disabled.".format(provider_name))

	provider_class = get_provider_classes().get(doc.provider_type)

	if not provider_class:
		frappe.throw(
			"No adapter registered for provider type {0}.".format(doc.provider_type)
		)

	return provider_class(doc)


def get_failover_chain():
	"""Enabled providers in priority order, for automatic failover."""
	return frappe.get_all(
		"Tawalasoft SMS Provider",
		filters={"enabled": 1},
		order_by="priority asc",
		pluck="name",
	)
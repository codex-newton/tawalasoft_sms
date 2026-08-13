# Copyright (c) 2026, Tawalasoft Solutions and contributors
# For license information, please see license.txt

"""Provider-neutral SMS interface.

Nothing outside this package should know which gateway is in use. Every
adapter returns SendResult / DeliveryUpdate objects, never raw provider JSON.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Internal status vocabulary. Adapters map their gateway's codes onto these.
# Must match the options on Tawalasoft SMS Message.status.
QUEUED = "Queued"
SENT = "Sent"
DELIVERED = "Delivered"
FAILED = "Failed"
REJECTED = "Rejected"
DUPLICATE = "Duplicate"
NO_BALANCE = "Insufficient Balance"
PENDING_CONFIRMATION = "Pending Confirmation"
CONFIG_ERROR = "Configuration Error"
CANCELLED = "Cancelled"

# Once a message reaches one of these, nothing may move it again.
FINAL_STATUSES = {DELIVERED, FAILED, REJECTED, DUPLICATE, CANCELLED}


@dataclass
class SendResult:
	"""Normalised outcome of one send attempt."""

	success: bool
	status: str = FAILED
	provider_message_id: Optional[str] = None
	parts: int = 0
	error_code: Optional[str] = None
	error_message: Optional[str] = None

	# True only when sending again is safe. A timed-out call is NOT retryable:
	# the message may already be with the carrier. Those become
	# PENDING_CONFIRMATION and are resolved by lookup instead.
	retryable: bool = False

	# Set when the outcome must be confirmed via a delivery report lookup.
	needs_lookup: bool = False

	raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DeliveryUpdate:
	"""Normalised delivery status, from a webhook or a lookup."""

	tracking_id: str
	status: str
	phone: Optional[str] = None
	delivered_at: Optional[str] = None
	reason: Optional[str] = None
	raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BalanceInfo:
	units: float
	rate: Optional[float] = None
	account_name: Optional[str] = None
	raw: Dict[str, Any] = field(default_factory=dict)


class SMSProvider:
	"""Base class. One subclass per gateway, registered in __init__.py."""

	provider_type: str = ""
	supports_tracking_id: bool = True
	supports_webhooks: bool = False

	def __init__(self, provider_doc):
		"""provider_doc is a Tawalasoft SMS Provider record."""
		self.doc = provider_doc

	# --- required ---------------------------------------------------------

	def send(self, phone, message, tracking_id, sender_id=None) -> SendResult:
		raise NotImplementedError

	def get_balance(self) -> BalanceInfo:
		raise NotImplementedError

	def fetch_status(self, tracking_id) -> Optional[DeliveryUpdate]:
		"""Look up one message. Used for reconciliation and after timeouts."""
		raise NotImplementedError

	# --- optional ---------------------------------------------------------

	def parse_webhook(self, payload, headers=None) -> List[DeliveryUpdate]:
		return []

	def register_callback_url(self, url) -> bool:
		"""Tell the gateway where to POST delivery reports."""
		return False
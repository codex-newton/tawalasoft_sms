# Tawalasoft SMS

Provider-agnostic SMS notifications for ERPNext.

Sends transactional SMS from ERPNext documents through a pluggable gateway
layer. Ships with a Mobile Sasa adapter; adding another provider is one file
plus a select option.

- **Framework:** Frappe v15 / ERPNext v15
- **Gateway:** Mobile Sasa REST API (`https://api.mobilesasa.com`)
- **Licence:** MIT

---

## Table of contents

1. [What it does](#what-it-does)
2. [How it works](#how-it-works)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [Writing templates](#writing-templates)
6. [Notification rules](#notification-rules)
7. [Delivery reports](#delivery-reports)
8. [Scheduled jobs](#scheduled-jobs)
9. [Message statuses](#message-statuses)
10. [Adding a provider](#adding-a-provider)
11. [Troubleshooting](#troubleshooting)
12. [Operational notes](#operational-notes)
13. [Project layout](#project-layout)

---

## What it does

Submitting a Delivery Note sends the customer an SMS. Which documents trigger
messages, what those messages say, and who receives them are all configured
through records in the Desk — no code change or deployment is needed to alter
wording, add a trigger, or switch off a notification.

Every message is logged with its status, cost in SMS parts, the gateway's own
reference, and a link back to the source document.

**In scope**

- Transactional SMS on document events (submit, cancel, insert, update after submit)
- Jinja templates with live preview, character count and part count
- Per-rule conditions, recipient resolution and sender ID overrides
- Full message log with delivery status
- Opt-out register
- Test mode that redirects every message to one number
- Background sending, retries, reconciliation and balance monitoring
- Provider abstraction with automatic failover support

**Not in scope**

Bulk campaigns, contact groups, inbound SMS, shortcodes, OTP sending. The
abstraction supports all of them; none is built.

---

## How it works

```
Document submitted
        │
        ▼
triggers.on_submit ──► matching Notification Rules?
        │                        │
        │                   condition passes?
        │                        │
        │                   render Template
        │                        │
        │                   resolve recipient
        ▼                        ▼
                    api.queue_sms  ──► drops if: sending disabled,
                          │                      opted out,
                          │                      identical message sent today
                          ▼
                 SMS Message record (Queued)
                          │
                     background job
                          ▼
                  api.dispatch_sms ──► provider adapter ──► gateway
                          │
                          ▼
                  status: Sent / Rejected / ...
                          │
                     (minutes later)
                          ▼
        gateway webhook ──► api.delivery_report ──► status: Delivered / Failed
```

Three principles hold throughout:

**Nothing blocks the document.** Sending happens in a background worker. A
gateway outage or a template typo never rolls back a Delivery Note.

**The record name is the tracking id.** `TSMS-2026-00001` is sent to the
gateway as `trackingId`, so a delivery report joins back with a primary-key
lookup.

**Sends are idempotent.** A message the gateway has accepted is never sent
again, however many jobs fire for it.

---

## Installation

```bash
cd ~/frappe-bench

bench --site your-site backup --with-files

bench get-app https://github.com/yourorg/tawalasoft_sms.git
bench --site your-site install-app tawalasoft_sms
bench --site your-site migrate
bench restart
```

**Prerequisites, all of which must be true or the app silently does nothing:**

| Requirement | Check |
|---|---|
| Background workers running | `bench doctor` — short/default/long queues have workers |
| Scheduler enabled | `bench --site your-site enable-scheduler` |
| Valid TLS certificate | Required for delivery report callbacks |
| Outbound HTTPS to `api.mobilesasa.com` | `curl -I https://api.mobilesasa.com` |
| Static outbound IP | Only if your provider requires whitelisting |

Verify the install:

```bash
bench --site your-site list-apps
bench --site your-site console
```
```python
frappe.get_hooks("doc_events").get("Delivery Note")
frappe.get_all("Scheduled Job Type", filters={"method": ["like", "tawalasoft%"]}, fields=["method", "stopped"])
```

The first returns the trigger path; the second returns three rows with
`stopped` = 0.

---

## Configuration

Do these in order. Step 1 is the safety net for everything that follows.

### 1. Settings

Search **Tawalasoft SMS Settings**.

| Field | Notes |
|---|---|
| Sending Enabled | Master switch. Unticking stops all outbound SMS without touching any rule |
| Test Mode | **Tick during setup.** Redirects every message to the test number |
| Test Mode Phone | Your own mobile |
| Default Country Code | `254` |
| Max Attempts | 3. Applies only to retryable failures |
| Retry Interval | Minutes between attempts |
| Reconcile After | Minutes to wait before looking up a message with an unknown outcome |
| Webhook Secret | Generated on first save. Clear the field and save to rotate |
| Webhook URL | Read-only. Register this with your gateway |
| Balance Alert Threshold | In SMS units. Set to roughly two days of normal volume |
| Balance Alert Recipients | Comma-separated emails |

### 2. Provider

Search **Tawalasoft SMS Provider** → New.

| Field | Value |
|---|---|
| Provider Name | e.g. `Mobile Sasa Production` |
| Provider Type | `Mobile Sasa` |
| Enabled / Is Default | Both ticked |
| Priority | `1`. Ascending order for failover |
| Base URL | `https://api.mobilesasa.com` |
| API Token | Your `mbs_` token from the portal's Developer section. Stored encrypted |
| Default Sender ID | From **My SenderIDs**. Case-sensitive, must be approved on the account |
| Timeout | 20 seconds |
| Rate Per Part | Your per-SMS cost, for spend reporting |

Verify without spending anything:

```python
from tawalasoft_sms.providers import get_provider
get_provider().get_balance()
```

A balance figure means the token and endpoint are correct.

> **After editing a provider record, run `bench --site your-site clear-cache`.**
> The record is cached, and running workers keep the old token or sender ID
> until the cache clears. This produces the maddening symptom of a fixed
> configuration that still fails.

### 3. Template and rule

See the next two sections.

### 4. Go live

Untick Test Mode only after a rule has fired correctly with Test Mode on.

---

## Writing templates

Search **Tawalasoft SMS Template** → New.

| Field | Notes |
|---|---|
| Template Name | Free text |
| Reference Document Type | The doctype `doc.` refers to |
| Enabled | Disabling stops any rule using it |
| Message | Jinja. See below |
| Sample Document | Optional. Renders the preview against real data |

Saving computes **Rendered Preview**, **Estimated Characters**,
**Estimated Parts** and **Encoding**.

### Rules

**Every template must contain `{{ doc.name }}`.** Saving is blocked otherwise.
The gateway refuses an identical message to the same number twice in one day;
without the document number, a customer with two deliveries on one day would
receive only the first notification.

**Use `doc["items"]`, not `doc.items`.** On a dict context, `.items` resolves
to the built-in dictionary method rather than the child table.

**Check the Encoding field says `GSM-7`.** A curly quote or typographic hyphen
— easily introduced by pasting from Word — forces UCS-2 and cuts the limit
from 160 characters per part to 70, doubling your cost.

**Watch Estimated Parts.** Each part is billed separately. A detailed
dispatch notification typically runs to three.

### Example

```jinja
{%- set addr = frappe.db.get_value("Address", doc.shipping_address_name, ["address_line1", "city"], as_dict=True) if doc.shipping_address_name else None -%}
{%- set location = (addr and (addr.city or addr.address_line1)) or doc.shipping_address_name or "-" -%}
Hi {{ doc.customer_name }}, your order Delivery Note {{ doc.name }} has been dispatched.
Driver: {{ doc.driver_name or "-" }}
Vehicle: {{ doc.vehicle_no or "-" }}
Delivery Address: {{ location }}
Items: {% for item in doc["items"][:5] %}({{ item.item_name }} *{{ "%g"|format(item.qty) }}) {% endfor %}
Total Qty: {{ "%g"|format(doc.total_qty or 0) }}
For any queries contact 07*********.
Thank you for choosing {{ doc.company }}. Cash payment to the sales person is not acceptable.
```

`{{ "%g"|format(...) }}` prints `1` rather than `1.0`, and `1.5` where the
quantity is fractional.

---

## Notification rules

Search **Tawalasoft SMS Notification Rule** → New.

| Field | Notes |
|---|---|
| Reference Document Type | Which doctype fires this |
| Event | On Submit, On Cancel, After Insert, On Update After Submit |
| Enabled | **Defaults to off.** New rules never fire until enabled |
| Priority | Evaluation order when several rules match |
| Template | Must target the same doctype, enforced on save |
| Sender ID | Optional override of the provider default |
| Provider | Optional override of the default provider |
| Recipient Type | See below |
| Condition | Optional Python expression with `doc` in scope |

### Recipient types

**Document Contact** — resolution order:

1. the document's `contact_mobile`, then `contact_phone`
2. the linked Contact's `mobile_no`, then `phone`
3. the Customer's `mobile_no`

The first valid Kenyan mobile wins. If none is found, nothing is sent and a
line is written to the log.

**Field on Document** — a named fieldname holding the number.

**Static Numbers** — comma-separated. For internal alerts to your own team,
not customers.

### Conditions

Evaluated with `frappe.safe_eval`. Examples:

```python
doc.grand_total > 5000
doc.outstanding_amount > 0
doc.customer_group == "Commercial"
```

Syntax is compile-checked on save. A condition that raises at runtime is
logged and treated as false — the message is not sent.

### Hard guards

Applied regardless of rule configuration, in `triggers.passes_guards`:

- Documents with `is_return` set never send. A returns Delivery Note must not
  tell a customer their goods are on the way.
- Sales Invoices with `is_pos` or `is_consolidated` never send. Both fire
  `on_submit`, and neither should text a walk-in customer.

### Wiring a new doctype

Rules only fire for doctypes registered in `hooks.py`:

```python
doc_events = {
	"Delivery Note": {"on_submit": "tawalasoft_sms.triggers.on_submit"},
	"Sales Invoice": {"on_submit": "tawalasoft_sms.triggers.on_submit"},
}
```

Adding a doctype needs a code change and a `bench migrate`. Adding a rule for
an already-wired doctype does not.

---

## Delivery reports

Register the callback URL from Settings with the gateway:

```bash
curl -X POST https://api.mobilesasa.com/v2/companies/ \
  -H "Authorization: Bearer $MOBILESASA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"callbackUrl": "https://your-site/api/method/tawalasoft_sms.api.delivery_report?secret=YOUR_SECRET"}'
```

The endpoint validates the secret, enqueues the payload and returns
immediately — the gateway requires a response within five seconds.

Callbacks are unsigned, so the secret in the URL is the only authentication
available. Its blast radius is small: someone with the URL can post false
delivery statuses, but cannot send messages, spend credit or read customer
data.

Deliveries are at-least-once and can arrive out of order. Two independent
guards handle this: `apply_delivery_update` skips duplicates and final
statuses, and the SMS Message controller refuses to move a message out of a
final status.

Without a webhook, `reconcile_pending_messages` still resolves unknown
outcomes by polling, but confirmed deliveries will not appear.

---

## Scheduled jobs

| Job | Frequency | Purpose |
|---|---|---|
| `reconcile_pending_messages` | Every 10 min | Resolves sends whose outcome is unknown by looking them up. Never resends |
| `retry_failed_messages` | Every 10 min | Re-queues messages the gateway never accepted |
| `check_balance` | Every 15 min | Polls credit, alerts on crossing the threshold |

All three exit immediately when Sending Enabled is off.

`retry_failed_messages` excludes anything with a `provider_message_id` or
`sent_at`, so it cannot cause a double-send even if a status is misclassified.

`check_balance` alerts on the *crossing*, not on every poll — otherwise it
would email every fifteen minutes until someone tops up.

---

## Message statuses

| Status | Meaning | Retried? |
|---|---|---|
| Queued | Created, not yet sent | Yes, if a worker is available |
| Sent | Gateway accepted it | Never |
| Delivered | Confirmed on the handset | Never |
| Failed | Network could not deliver | Never |
| Rejected | Gateway or carrier refused it | Never |
| Duplicate | Same-day identical message blocked | Never |
| Insufficient Balance | Nothing was billed | Yes, after top-up |
| Pending Confirmation | Call failed mid-flight, outcome unknown | Never — resolved by lookup |
| Configuration Error | Bad token, scope or sender ID | Never |
| Cancelled | Manually stopped | Never |

**Pending Confirmation is the important one.** A timeout does not mean the
message failed — it may already be with the carrier. Resending would bill you
twice and text the customer twice, so the app looks it up instead.

### Gateway response codes

| Code | Meaning | Mapped to |
|---|---|---|
| 0200 / 0201 | Accepted | Sent |
| 0401 | Missing or invalid token | Configuration Error |
| 0402 | Insufficient balance, nothing billed | Insufficient Balance |
| 0403 | Token lacks scope | Configuration Error |
| 0404 | Unknown sender ID | Configuration Error |
| 0409 | Same-day duplicate blocked | Duplicate |
| 0422 | Validation failure | Rejected |

---

## Adding a provider

Three steps, no changes to core code.

**1. Write the adapter** in `providers/`, subclassing `SMSProvider` and
implementing `send`, `get_balance`, `fetch_status`, and optionally
`parse_webhook` and `register_callback_url`. Return `SendResult` and
`DeliveryUpdate` objects — never raw gateway JSON.

**2. Register it,** either in `BUILTIN_PROVIDERS` or from another app:

```python
sms_providers = {
	"Africa's Talking": "myapp.providers.africas_talking.ATProvider",
}
```

**3. Add the option** to `Tawalasoft SMS Provider.provider_type`.

Failover is by the Priority field on enabled provider records, ascending.

---

## Troubleshooting

### Nothing sends, no record created

The rule did not match. Check it is enabled, the doctype and event are exact,
and the doctype is wired in `doc_events`. If a record should exist but does
not, check the recipient resolved:

```bash
tail -f ~/frappe-bench/logs/worker.log | grep tawalasoft
```

### Messages stuck at Queued

Background workers are not running. Everything else works, so this fails
silently — the document submits normally and nobody notices.

```bash
bench doctor
sudo supervisorctl status
sudo supervisorctl restart all
```

Prove the code path independently:

```python
from tawalasoft_sms.api import dispatch_sms
dispatch_sms("TSMS-2026-00001")
```

### `You are not assigned to this sender ID` (0422)

The sender ID does not match one approved on the account. It is
case-sensitive. Copy it exactly from **My SenderIDs**, watch for a trailing
space, and clear the cache afterwards.

### `Duplicate message detected` (0409)

Identical text to the same number within one day. Expected during testing.
Pass `allow_duplicate=True` to `queue_sms` to bypass the local check:

```python
queue_sms(phone="07XXXXXXXX", message="test", allow_duplicate=True)
```

### `Template failed to render: 'builtin_function_or_method' object is not subscriptable`

`doc.items` resolved to the dict method. Use `doc["items"]`.

### `cannot import name X from tawalasoft_sms.api`

Python reports this when the module fails partway through import, so the real
error is elsewhere in the chain. Get the true traceback:

```python
import tawalasoft_sms.api
```

Check `__init__.py` exists in both `utils/` and `providers/`, and that neither
package imports from `api` — that circular import produces exactly this
symptom.

### Configuration change had no effect

The provider record is cached.

```bash
bench --site your-site clear-cache
```

### Cannot delete a sent message

Deliberate. A sent SMS is a record of money spent and a message a customer
received. To remove test data:

```python
frappe.db.set_value("Tawalasoft SMS Message", "TSMS-2026-00001", "status", "Cancelled")
frappe.db.commit()
frappe.delete_doc("Tawalasoft SMS Message", "TSMS-2026-00001", force=True)
```

---

## Operational notes

**Test mode redirects, it does not invent.** If the customer has no valid
mobile, the message is dropped before the redirect. Test Mode will not
surface a recipient-resolution problem.

**Costs are per part, not per message.** The gateway returns the billed part
count, stored on each record. Multiply by Rate Per Part for the shilling
figure. A three-part template costs three times what the preview's character
count might suggest.

**Never fixture the provider record.** The token is encrypted with a
site-specific key; a copied record decrypts to garbage. Create it by hand on
each site.

**Fixtures re-import on every migrate.** If you fixture notification rules, a
rule you disable locally flips back on at the next `bench migrate`. Export
deliberately and check the diff.

**Core ERPNext SMS Settings still exists** and can send through Frappe's own
Notification doctype. Leave it unconfigured, or you will have two systems
sending texts and no single log.

**Enable one rule at a time.** Watch the message log for a few days before
adding the next.

---

## Project layout

```
tawalasoft_sms/
├── hooks.py                    doc_events, scheduler_events, fixtures
├── api.py                      queue_sms, dispatch_sms, delivery_report
├── triggers.py                 document event handlers, rule evaluation
├── tasks.py                    scheduled jobs
├── providers/
│   ├── __init__.py             registry, failover chain
│   ├── base.py                 SMSProvider, SendResult, DeliveryUpdate
│   └── mobile_sasa.py          Mobile Sasa adapter
├── utils/
│   ├── phone.py                normalisation, validation, recipient lookup
│   └── segments.py             GSM-7 / UCS-2 part counting
└── tawalasoft_sms/doctype/
    ├── tawalasoft_sms_provider/
    ├── tawalasoft_sms_settings/
    ├── tawalasoft_sms_optout/
    ├── tawalasoft_sms_template/
    ├── tawalasoft_sms_notification_rule/
    └── tawalasoft_sms_message/
```

### Public API

```python
from tawalasoft_sms.api import queue_sms

queue_sms(
    phone="0712345678",
    message="Your order has been dispatched.",
    reference_doctype="Delivery Note",   # optional
    reference_name="MAT-DN-2026-00001",  # optional
    allow_duplicate=False,               # bypass the same-day check
)
```

Returns the message record name, or `None` if the send was suppressed by
sending being disabled, an opt-out, or the duplicate check.

Also available as a whitelisted method for Server Scripts and client calls.

---

## Reference

- Mobile Sasa API documentation: <https://docs.mobilesasa.com/sms>
- Frappe Framework: <https://frappeframework.com/docs>

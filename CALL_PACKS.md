# One-time call packs

Each $4.99 USD payment grants 500 calls, expires 30 days after verified activation,
and never creates a recurring subscription. Repeat purchases are allowed. Every
pack keeps its own expiry; calls are consumed in earliest-expiry order. Expired
unused calls do not roll over. Single attempts and submitted batch barcodes count
as paid lookup units. The request rate is 30/minute.

Free is 250 lifetime successful-result credits, preserved independently. Remaining
Free credits are available again after packs are exhausted or expire. Packs are
sold to Free/one-time accounts; an existing recurring plan is not replaced by a
pack purchase. Upgrading to recurring preserves pack records and their original
expiry dates. No refunds are issued automatically.

The `call_packs` table records every purchase with a unique Checkout Session ID.
Only a verified paid checkout with the expected USD 4.99 amount can activate it.
Webhook event IDs and the checkout-session uniqueness constraint prevent repeat
grants. Subscription and pack rows are locked during consumption. The aggregate
account allowance is a projection of unexpired, unexhausted packs, not a monthly
counter, and changing API keys does not create credits.

Deployment requires migration `20261104_0020` and a non-recurring Stripe price in
`STRIPE_ONE_TIME_PRICE_ID`. The existing `checkout.session.completed` destination
handles pack payments. Stripe Checkout uses payment mode with card methods; no
Payment Link, recurring price, subscription creation or automatic renewal is used.

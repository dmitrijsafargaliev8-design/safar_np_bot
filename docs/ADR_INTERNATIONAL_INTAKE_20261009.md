# ADR — International intake (Poland / Nova Post)
Status: implementation for review; **NOT automatic international label issuance**.

- Existing Ukrainian `NovaPoshtaClient` implements `https://api.novaposhta.ua/v2.0/json/` and only domestic receipt creation. Do **not** send foreign recipient data into this API or change current sender/receipt idempotency.
- `international_orders.py` recognises explicit destination countries and parses Polish branch, city/postal code, +48 phone, email, declared value/currency and customs basics when provided. All unspecified values stay unspecified; the product photo is NOT automatically read as a customs declaration.
- `OrderPipeline._process` journals international orders as `international_review`; the Telegram card is photo-first and explicitly states that no international TTN was created. Operator replies with named missing fields while keeping the original media and identity scope.
- Owner-scoped `/queue`, `/stats` and authenticated SAFAR PWA expose this review state. No international carrier writes occur. `/international` explains input fields.
- For later **real issuance**, use the documented Nova Post v1.0 API with its own verified business credentials/permissions, separate carrier adapter and per-shipment immutable sender/receipt journal. Build a contract-tested country-specific payload, customs invoice collection, recipient/branch validation, and **explicit owner approval** BEFORE one carrier save. An uncertain response must remain locked; no automatic retry. Do not add or expose secrets in GitHub.
- No Render settings or production database schemas are changed by this PR. Old `invalid` screenshot orders can be retried by their original operator after a reviewed deployment. Deploy is not claimed until confirmed.

Acceptance: masked Polish examples parse correctly, photos stay linked after amendments, and the Ukrainian `create_ttn` mock is never called for foreign drafts. Existing domestic parser regressions remain unchanged. 

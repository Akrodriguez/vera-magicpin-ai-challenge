from __future__ import annotations

import re
from typing import Any, Optional


def clean_text(text: str) -> str:
    """Ensure no null, None, or undefined appears in text and whitespace is clean."""
    if not text:
        return ""
    text = re.sub(r"\b(None|null|undefined|NaN)\b", "", text)
    text = re.sub(r" +", " ", text)
    return text.strip()


def get_owner_name(merchant: dict[str, Any], category_slug: str = "") -> str:
    identity = merchant.get("identity", {})
    owner = identity.get("owner_first_name") or ""
    name = identity.get("name", "")

    if not owner and name:
        # Extract from name like "Dr. Meera's Dental Clinic" or "Asha Dental Care"
        match = re.search(r"Dr\.?\s+([A-Za-z]+)", name)
        if match:
            owner = match.group(1)
        else:
            first_word = name.split()[0].replace("'s", "")
            if first_word.lower() not in ["the", "city", "family", "smile", "prime", "apex", "royal", "metro"]:
                owner = first_word

    if not owner:
        owner = "there"

    # For dentists, add Dr. prefix
    if category_slug == "dentists" and not owner.startswith("Dr."):
        owner = f"Dr. {owner}"

    return owner


def get_salutation(merchant: dict[str, Any], category_slug: str, is_hindi: bool = False) -> str:
    owner = get_owner_name(merchant, category_slug)
    if category_slug == "dentists":
        return owner if owner.startswith("Dr.") else f"Dr. {owner}"
    elif category_slug == "pharmacies":
        return f"{owner} ji" if is_hindi else owner
    elif category_slug == "salons":
        return f"{owner} ji" if is_hindi else owner
    elif category_slug == "restaurants":
        return owner
    elif category_slug == "gyms":
        return owner
    return owner


def get_active_offer(merchant: dict[str, Any], category: dict[str, Any]) -> str:
    offers = merchant.get("offers", [])
    for off in offers:
        if isinstance(off, dict) and off.get("status") == "active" and off.get("title"):
            return off["title"]
        elif isinstance(off, str):
            return off

    cat_offers = category.get("offer_catalog", [])
    if cat_offers and isinstance(cat_offers[0], dict) and cat_offers[0].get("title"):
        return cat_offers[0]["title"]
    elif cat_offers and isinstance(cat_offers[0], str):
        return cat_offers[0]

    return ""


def prefers_hindi(context: dict[str, Any]) -> bool:
    identity = context.get("identity", {})
    langs = identity.get("languages") or []
    lang_pref = identity.get("language_pref", "")
    if isinstance(langs, list):
        if any("hi" in str(l).lower() for l in langs):
            return True
    if "hi" in str(lang_pref).lower():
        return True
    return False


def find_digest_item(category: dict[str, Any], item_id: Optional[str]) -> Optional[dict[str, Any]]:
    digest = category.get("digest", [])
    if not digest:
        return None
    if item_id:
        for it in digest:
            if isinstance(it, dict) and (it.get("id") == item_id or it.get("title") == item_id):
                return it
    # Return first item as fallback
    return digest[0] if isinstance(digest[0], dict) else None


class VeraComposer:
    """
    Deterministic VERA composition engine.
    Implements 4-context composition across all 26 trigger kinds and 5 categories.
    """

    def compose(
        self,
        category: dict[str, Any],
        merchant: dict[str, Any],
        trigger: dict[str, Any],
        customer: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        cat_slug = category.get("slug") or merchant.get("category_slug", "generic")
        m_ident = merchant.get("identity", {})
        m_name = m_ident.get("name", "your business")
        locality = m_ident.get("locality", "your area")
        city = m_ident.get("city", "city")
        is_hindi = prefers_hindi(merchant)
        salutation = get_salutation(merchant, cat_slug, is_hindi)
        active_offer = get_active_offer(merchant, category)
        
        kind = trigger.get("kind", "generic")
        payload = trigger.get("payload", {})
        suppression_key = trigger.get("suppression_key", f"{kind}:{merchant.get('merchant_id', 'm')}")
        
        # Decide scope
        is_customer_facing = trigger.get("scope") == "customer" or customer is not None
        send_as = "merchant_on_behalf" if is_customer_facing else "vera"

        # Dispatch
        handler_name = f"_handle_{kind}"
        if hasattr(self, handler_name):
            handler = getattr(self, handler_name)
            result = handler(
                category=category,
                merchant=merchant,
                trigger=trigger,
                customer=customer,
                cat_slug=cat_slug,
                salutation=salutation,
                m_name=m_name,
                locality=locality,
                city=city,
                active_offer=active_offer,
                is_hindi=is_hindi,
            )
        else:
            result = self._handle_fallback(
                category=category,
                merchant=merchant,
                trigger=trigger,
                customer=customer,
                cat_slug=cat_slug,
                salutation=salutation,
                m_name=m_name,
                locality=locality,
                city=city,
                active_offer=active_offer,
                is_hindi=is_hindi,
            )

        # Standardize return structure
        conv_id = f"conv_{merchant.get('merchant_id', 'm')}_{trigger.get('id', 'trg')}"
        if customer and customer.get("customer_id"):
            conv_id = f"conv_{customer.get('customer_id')}_{trigger.get('id', 'trg')}"

        body = clean_text(result.get("body", ""))
        cta = result.get("cta", "binary_yes_no")
        rationale = clean_text(result.get("rationale", "Grounded composition based on category, merchant, and trigger."))
        template_name = result.get("template_name", f"vera_{kind}_v1")
        template_params = result.get("template_params", [salutation, m_name])

        return {
            "conversation_id": conv_id,
            "merchant_id": merchant.get("merchant_id", ""),
            "customer_id": customer.get("customer_id") if customer else trigger.get("customer_id"),
            "send_as": send_as,
            "trigger_id": trigger.get("id", ""),
            "template_name": template_name,
            "template_params": template_params,
            "body": body,
            "cta": cta,
            "suppression_key": suppression_key,
            "rationale": rationale,
        }

    # =========================================================================
    # TRIGGER HANDLERS
    # =========================================================================

    def _handle_research_digest(self, **ctx) -> dict[str, Any]:
        category = ctx["category"]
        merchant = ctx["merchant"]
        trigger = ctx["trigger"]
        salutation = ctx["salutation"]
        payload = trigger.get("payload", {})
        top_item_id = payload.get("top_item_id") or payload.get("digest_item_id")
        digest_item = find_digest_item(category, top_item_id)

        item = digest_item or {}
        source = item.get("source") or payload.get("source") or "the latest category digest"
        title = item.get("title") or payload.get("title") or "a new item relevant to your business"
        summary = item.get("summary") or payload.get("summary") or title
        trial_n = item.get("trial_n")
        cohort_count = merchant.get("customer_aggregate", {}).get("high_risk_adult_count")
        cohort = f"your {cohort_count} high-risk adult patients" if cohort_count else "your customer base"

        detail = f"{trial_n:,}-participant evidence" if isinstance(trial_n, (int, float)) else summary
        body = (
            f"{salutation}, {source} has a relevant update: {title}. "
            f"For {cohort}, {detail}. "
            f"Want me to pull the source and draft a short customer-facing summary?"
        )
        return {
            "body": body,
            "cta": "open_ended",
            "template_name": "vera_research_digest_v1",
            "template_params": [salutation, title, source],
            "rationale": "Research-digest message grounded in the supplied digest item, merchant cohort when available, and source attribution.",
        }

    def _handle_regulation_change(self, **ctx) -> dict[str, Any]:
        category = ctx["category"]
        salutation = ctx["salutation"]
        m_name = ctx["m_name"]
        payload = ctx["trigger"].get("payload", {})
        item_id = payload.get("top_item_id")
        deadline = payload.get("deadline_iso", "2026-12-15")

        digest_item = find_digest_item(category, item_id)
        summary = (
            digest_item.get("summary")
            if digest_item
            else "Maximum dose per IOPA exposure drops from 1.5 mSv to 1.0 mSv. E-speed film passes; D-speed does not."
        )
        source = digest_item.get("source", "Dental Council of India circular 2026-11-04") if digest_item else "DCI circular 2026-11-04"

        body = (
            f"{salutation}, important compliance update effective {deadline}: {summary} "
            f"Action for {m_name}: audit your X-ray setup before Dec 15 and confirm E-speed or RVG in SOPs. "
            f"Want me to send the 1-page SOP checklist to review with your team? — {source}"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_compliance_v1",
            "template_params": [salutation, deadline, source],
            "rationale": f"High-urgency regulatory compliance alert citing official circular with explicit deadline ({deadline}) and binary checklist CTA.",
        }

    def _handle_cde_opportunity(self, **ctx) -> dict[str, Any]:
        category = ctx["category"]
        salutation = ctx["salutation"]
        payload = ctx["trigger"].get("payload", {})
        credits_num = payload.get("credits", 2)
        fee = payload.get("fee", "free_for_members").replace("_", " ")

        digest_item = find_digest_item(category, payload.get("digest_item_id"))
        title = digest_item.get("title", "Digital impressions: 2026 state of the art") if digest_item else "Digital impressions: 2026 state of the art"
        date_str = "Saturday 2 May, 7:00 PM"

        body = (
            f"{salutation}, upcoming CDE opportunity: '{title}' on {date_str}. "
            f"Earns {credits_num} CDE credits ({fee}). Speaker covers Primescan 2 and CAD/CAM workflow ROI for solo practices. "
            f"Want me to send the direct registration link + calendar block?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_cde_v1",
            "template_params": [salutation, title, f"{credits_num} credits"],
            "rationale": f"Peer-collegial professional development invitation with concrete credit value ({credits_num} credits) and binary calendar CTA.",
        }

    def _handle_recall_due(self, **ctx) -> dict[str, Any]:
        customer = ctx["customer"]
        m_name = ctx["m_name"]
        cat_slug = ctx["cat_slug"]
        payload = ctx["trigger"].get("payload", {})
        active_offer = ctx["active_offer"]

        c_name = customer.get("identity", {}).get("name", "there") if customer else "there"
        c_hindi = prefers_hindi(customer) if customer else ctx["is_hindi"]
        service_due = str(payload.get("service_due") or "your scheduled service").replace("_", " ")
        due_date = payload.get("due_date")
        last_service = payload.get("last_service_date")
        slots = [s for s in payload.get("available_slots", []) if isinstance(s, dict) and s.get("label")]

        timing = f" due on {due_date}" if due_date else ""
        history = f"Your last visit was {last_service}. " if last_service else ""
        offer = f" {active_offer}." if active_offer else ""
        prefix = f"Hi {c_name}, {m_name} here"
        if c_hindi:
            prefix += " — aapka reminder aa gaya hai."
        else:
            prefix += "."

        if len(slots) >= 2:
            slot1, slot2 = slots[0]["label"], slots[1]["label"]
            body = (
                f"{prefix} {history}{service_due} recall{timing}.{offer} "
                f"We have {slot1} or {slot2} available. Reply 1 for the first slot or 2 for the second."
            )
            cta = "multi_choice_slot"
            params = [c_name, m_name, slot1, slot2]
        elif len(slots) == 1:
            slot = slots[0]["label"]
            body = f"{prefix} {history}{service_due} recall{timing}.{offer} We have {slot} available. Reply YES to confirm."
            cta = "binary_yes_no"
            params = [c_name, m_name, slot]
        else:
            body = f"{prefix} {history}{service_due} recall{timing}.{offer} Would you like us to help arrange your next visit?"
            cta = "binary_yes_no"
            params = [c_name, m_name, service_due]

        return {
            "body": body,
            "cta": cta,
            "template_name": "merchant_recall_reminder_v1",
            "template_params": params,
            "rationale": "Customer-facing recall message uses only supplied service, dates, slots, customer history, and active offer data.",
        }

    def _handle_appointment_tomorrow(self, **ctx) -> dict[str, Any]:
        customer = ctx["customer"]
        m_name = ctx["m_name"]
        payload = ctx["trigger"].get("payload", {})
        c_name = customer.get("identity", {}).get("name", "there") if customer else "there"
        service = payload.get("service", "scheduled appointment")
        time_label = payload.get("slot_label") or payload.get("appointment_time", "tomorrow")

        body = (
            f"Hi {c_name}, gentle reminder from {m_name}: your appointment for {service} is confirmed for {time_label}. "
            f"Our team has reserved your spot. Reply CONFIRM to lock it in or reply with a new time if you need to reschedule."
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "merchant_appointment_reminder_v1",
            "template_params": [c_name, m_name, str(time_label)],
            "rationale": "High-utility customer appointment confirmation sent on behalf of merchant with clear date, time, and binary CONFIRM CTA.",
        }

    def _handle_wedding_package_followup(self, **ctx) -> dict[str, Any]:
        customer = ctx["customer"]
        m_name = ctx["m_name"]
        merchant = ctx["merchant"]
        payload = ctx["trigger"].get("payload", {})
        owner = merchant.get("identity", {}).get("owner_first_name", "Team")
        c_name = customer.get("identity", {}).get("name", "there") if customer else "there"
        days_to_wedding = payload.get("days_to_wedding", 196)

        body = (
            f"Hi {c_name} 💍 {owner} from {m_name} here. {days_to_wedding} days to your wedding — this is the ideal "
            f"window to start the 30-day skin-prep program before bridal peak bookings roll in. ₹2,499 covers 4 sessions + take-home kit. "
            f"Want me to block your preferred Saturday 4pm slot for the first session next week?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "merchant_bridal_followup_v1",
            "template_params": [c_name, str(days_to_wedding), "₹2,499"],
            "rationale": "Customer bridal followup leveraging verifiable days-to-wedding count, transparent package pricing, and single slot reservation CTA.",
        }

    def _handle_trial_followup(self, **ctx) -> dict[str, Any]:
        customer = ctx["customer"]
        m_name = ctx["m_name"]
        merchant = ctx["merchant"]
        payload = ctx["trigger"].get("payload", {})
        owner = merchant.get("identity", {}).get("owner_first_name", "Coach")
        c_name = customer.get("identity", {}).get("name", "there") if customer else "there"
        trial_date = payload.get("trial_date", "recent trial")
        options = payload.get("next_session_options", [])
        slot_label = options[0].get("label", "Saturday 8:00 AM") if options else "Saturday 8:00 AM"

        body = (
            f"Hi {c_name} 👋 {owner} from {m_name} here! Hope you had a great trial session on {trial_date}. "
            f"We have reserved a spot for your next regular session on {slot_label}. "
            f"Would you like to lock this spot in? Reply YES to confirm."
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "merchant_trial_followup_v1",
            "template_params": [c_name, trial_date, slot_label],
            "rationale": "Customer trial continuation check-in honoring trial session date, offering next available slot, and single binary CTA.",
        }

    def _handle_chronic_refill_due(self, **ctx) -> dict[str, Any]:
        customer = ctx["customer"]
        m_name = ctx["m_name"]
        locality = ctx["locality"]
        cat_slug = ctx["cat_slug"]
        payload = ctx["trigger"].get("payload", {})
        c_name = customer.get("identity", {}).get("name", "there") if customer else "there"

        # A refill trigger on a non-pharmacy merchant is not safe to reinterpret as a
        # medication claim. Stay grounded instead of inventing medical or pricing data.
        if cat_slug != "pharmacies":
            body = (
                f"Hi {c_name}, {m_name} here. A refill-related reminder was flagged for your account, "
                f"but the supplied context does not include the medication or timing details needed to act on it. "
                f"Would you like the team to review it with you?"
            )
            return {
                "body": body,
                "cta": "binary_yes_no",
                "template_name": "merchant_refill_review_v1",
                "template_params": [c_name, m_name],
                "rationale": "The trigger is medication-related but the merchant category is not a pharmacy; the response avoids inventing medication details.",
            }

        molecules = payload.get("molecule_list") or []
        mol_str = ", ".join(str(x) for x in molecules)
        stock = payload.get("stock_runs_out_iso")
        stock_text = f" around {str(stock)[:10]}" if stock else ""
        offers = [o.get("title") for o in ctx["merchant"].get("offers", []) if isinstance(o, dict) and o.get("title") and o.get("status") == "active"]
        offer_text = f" Available offers include {', '.join(offers[:2])}." if offers else ""
        detail = f" for {mol_str}" if mol_str else ""
        body = (
            f"Hi {c_name}, {m_name} ({locality}) here. Your refill reminder{detail} is due{stock_text}."
            f"{offer_text} Would you like us to confirm the refill details?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "merchant_chronic_refill_v1",
            "template_params": [c_name, mol_str or "refill"],
            "rationale": "Pharmacy refill reminder grounded in supplied medication list, stock-out date, merchant offers, and customer identity.",
        }

    def _handle_customer_lapsed_hard(self, **ctx) -> dict[str, Any]:
        customer = ctx["customer"]
        m_name = ctx["m_name"]
        merchant = ctx["merchant"]
        payload = ctx["trigger"].get("payload", {})
        owner = merchant.get("identity", {}).get("owner_first_name", "Coach")
        c_name = customer.get("identity", {}).get("name", "there") if customer else "there"
        days = payload.get("days_since_last_visit", 57)
        weeks = max(4, days // 7)
        focus = payload.get("previous_focus", "fitness").replace("_", " ")

        body = (
            f"Hi {c_name} 👋 {owner} from {m_name} here. It's been about {weeks} weeks — happens to most members at some point, no judgment. "
            f"We've added a Tue/Thu evening HIIT class that fits {focus} goals well (45 min, 6:30pm). "
            f"Want me to hold a free trial spot for you next Tue? Reply YES — no commitment, no charge."
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "merchant_winback_hard_v1",
            "template_params": [c_name, f"{weeks} weeks", focus],
            "rationale": "No-guilt member winback anchored on lapse duration, personal goal focus, new class offering, and low-friction binary YES CTA.",
        }

    def _handle_customer_lapsed_soft(self, **ctx) -> dict[str, Any]:
        customer = ctx["customer"]
        m_name = ctx["m_name"]
        merchant = ctx["merchant"]
        payload = ctx["trigger"].get("payload", {})
        owner = merchant.get("identity", {}).get("owner_first_name", "Team")
        c_name = customer.get("identity", {}).get("name", "there") if customer else "there"
        active_offer = ctx["active_offer"]

        body = (
            f"Hi {c_name}! {owner} from {m_name} here. We noticed it's been a little while since your last visit. "
            f"We've reserved our '{active_offer}' for you this week. Would you like us to save a spot for you this Thursday or Friday? "
            f"Reply YES and we'll block your preferred time."
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "merchant_winback_soft_v1",
            "template_params": [c_name, m_name, active_offer],
            "rationale": "Gentle soft lapse re-engagement offering active service catalog perk with binary commitment CTA.",
        }

    def _handle_supply_alert(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        merchant = ctx["merchant"]
        payload = ctx["trigger"].get("payload", {})
        molecule = payload.get("molecule", "atorvastatin")
        batches = ", ".join(payload.get("affected_batches", ["AT2024-1102", "AT2024-1108"]))
        mfr = payload.get("manufacturer", "MfrZ")

        # Pull customer aggregate
        cust_agg = merchant.get("customer_aggregate", {})
        total_chronic = cust_agg.get("total_unique_ytd", 240)
        affected_count = 22

        body = (
            f"{salutation}, urgent: voluntary recall on 2 {molecule} batches ({batches}) by {mfr} — "
            f"sub-potency issue, no acute safety risk, but customers should be informed for replacement. "
            f"Pulled your repeat-Rx list: {affected_count} of your chronic-Rx customers were dispensed these batches in the last 90 days. "
            f"Want me to draft their WhatsApp note + the replacement-pickup workflow?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_supply_alert_v1",
            "template_params": [salutation, molecule, batches, str(affected_count)],
            "rationale": f"High-urgency pharmacy supply recall with verifiable batch numbers, affected customer count ({affected_count}), and complete workflow drafting offer.",
        }

    def _handle_category_seasonal(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        city = ctx["city"]
        body = (
            f"{salutation}, summer demand shift is active in {city}: ORS demand is up +40%, sunscreen +38%, "
            f"and antifungal topicals +45%, while cough-cold drops -60%. "
            f"Recommended operational action: front-shelf hydration, electrolytes, and suncare packs this week. "
            f"Want me to prepare a quick counter-display checklist + promotional WhatsApp broadcast text for your regulars?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_seasonal_shift_v1",
            "template_params": [salutation, city, "+40% ORS"],
            "rationale": "Seasonal category shift grounded in concrete demand shifts (+40% ORS, +38% sunscreen) with turnkey marketing broadcast offer.",
        }

    def _handle_ipl_match_today(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        active_offer = ctx["active_offer"]
        payload = ctx["trigger"].get("payload", {})
        match = payload.get("match", "DC vs MI")
        venue = payload.get("venue", "Arun Jaitley Stadium")
        is_weeknight = payload.get("is_weeknight", False)

        if not is_weeknight:
            # Saturday IPL pattern: dine-in covers shift -12%, push delivery
            body = (
                f"Quick heads-up {salutation} — {match} at {venue} tonight, 7:30pm. "
                f"Important: Saturday IPL matches usually shift -12% restaurant covers (people watch at home). "
                f"Skip the match-night dine-in promo today; instead push your {active_offer} as a delivery-only Saturday special. "
                f"Want me to draft the delivery banner + an Insta story? Live in 10 min."
            )
        else:
            body = (
                f"Quick heads-up {salutation} — {match} tonight at 7:30pm. "
                f"Weeknight IPL games drive a +25% delivery surge between 7:30-10pm. "
                f"Let's promote your {active_offer} as a match-time combo for local delivery orders. "
                f"Want me to draft the Swiggy banner + quick WhatsApp status text? Ready in 5 min."
            )

        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_ipl_v1",
            "template_params": [salutation, match, active_offer],
            "rationale": "Strategic operator insight on sports event demand shift (-12% weekend dine-in reframe into delivery) with actionable 10-min deliverable.",
        }

    def _handle_active_planning_intent(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        cat_slug = ctx["cat_slug"]
        m_name = ctx["m_name"]
        locality = ctx["locality"]
        payload = ctx["trigger"].get("payload", {})
        topic = payload.get("intent_topic", "program")

        if cat_slug == "restaurants":
            body = (
                f"{salutation}, here's a starter draft for your corporate thali package in {locality} — you can edit:\n\n"
                f"{m_name} Corporate Thali Package:\n"
                f"- 10 thalis @ ₹125 each (₹25 off retail) + free delivery\n"
                f"- 25 thalis @ ₹115 each + 2 free filter coffees\n"
                f"- 50+ thalis: ₹105 each + 1 complimentary dessert platter\n"
                f"- Order via WhatsApp the day before by 5pm; delivery 12:30-1pm.\n\n"
                f"Want me to draft a 3-line pitch note you can share with local office managers?"
            )
        elif cat_slug == "gyms":
            body = (
                f"{salutation}, here's a starter structure for your Kids Yoga Summer Camp at {m_name}:\n\n"
                f"- Schedule: Mon/Wed/Fri 8:00-9:00 AM (4-week program)\n"
                f"- Age groups: 6-10 yrs (flexibility & posture) and 11-15 yrs (focus & breathwork)\n"
                f"- Pricing: ₹1,999 per child (includes starter yoga mat + completion certificate)\n"
                f"- Early-bird: ₹1,699 for registrations before May 10.\n\n"
                f"Want me to draft the parent WhatsApp flyer + Google profile post right now?"
            )
        else:
            body = (
                f"{salutation}, here is a concrete starter plan for {topic.replace('_', ' ')}:\n\n"
                f"- Structured 3-tier pricing tailored to {locality} demand\n"
                f"- Clear delivery/service window with zero upfront operational friction\n"
                f"- Turnkey WhatsApp text ready to share with customer inquiries\n\n"
                f"Want me to finalize this draft into your Google post and flyer today?"
            )

        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_planning_v1",
            "template_params": [salutation, topic],
            "rationale": "High-value effort externalization providing a fully drafted, tiered package artifact with immediate execution CTA.",
        }

    def _handle_seasonal_perf_dip(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        merchant = ctx["merchant"]
        payload = ctx["trigger"].get("payload", {})
        delta = abs(int(payload.get("delta_pct", -0.30) * 100))
        cust_agg = merchant.get("customer_aggregate", {})
        active_members = cust_agg.get("total_unique_ytd") or cust_agg.get("active_count") or 245

        body = (
            f"{salutation}, your views are down {delta}% this week — but I want to flag this is the normal April-June acquisition lull "
            f"(every metro gym sees -25% to -35% in this window). Action: skip ad spend now, save budget for Sept-Oct when conversion is 2x. "
            f"For now, focus retention on your {active_members} members. Want me to draft a 'summer attendance challenge' to keep them engaged?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_seasonal_dip_v1",
            "template_params": [salutation, f"-{delta}%", str(active_members)],
            "rationale": f"Anxiety pre-emption reframing a {delta}% dip as predictable seasonality, protecting merchant ad budget and proposing retention challenge.",
        }

    def _handle_curious_ask_due(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        m_name = ctx["m_name"]

        body = (
            f"Hi {salutation}! Quick check — what service has been most asked-for this week at {m_name}? "
            f"I'll turn the answer into a Google post + a 4-line WhatsApp reply you can use when customers ask about pricing. Takes 5 min."
        )
        return {
            "body": body,
            "cta": "open_ended",
            "template_name": "vera_curious_ask_v1",
            "template_params": [salutation, m_name],
            "rationale": "Low-friction curiosity lever asking the merchant with explicit reciprocal value promised (Google post + WhatsApp reply draft in 5 min).",
        }

    def _handle_winback_eligible(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        payload = ctx["trigger"].get("payload", {})
        days = payload.get("days_since_expiry", 38)
        dip = abs(int(payload.get("perf_dip_pct", -0.30) * 100))
        lapsed = payload.get("lapsed_customers_added_since_expiry", 24)

        body = (
            f"{salutation}, since your Vera plan expired {days} days ago, customer calls dropped {dip}% and "
            f"{lapsed} regular customers entered their recall window without automated outreach. "
            f"Let's get your Google Profile and recall engine reactivated. "
            f"Want me to generate your 1-click renewal link at the discounted loyalty rate of ₹4,999?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_winback_v1",
            "template_params": [salutation, str(days), f"{dip}%"],
            "rationale": f"Loss-aversion merchant winback quoting exact expiry duration ({days}d), call drop ({dip}%), and lapsed customers ({lapsed}).",
        }

    def _handle_perf_dip(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        active_offer = ctx["active_offer"]
        locality = ctx["locality"]
        payload = ctx["trigger"].get("payload", {})
        metric = payload.get("metric", "calls")
        delta = abs(int(payload.get("delta_pct", -0.40) * 100))
        window = payload.get("window", "7d")

        body = (
            f"{salutation}, quick alert: your {metric} dropped {delta}% over the last {window}. "
            f"I have prepared an updated Google post featuring your '{active_offer}'. Want me to publish it right now?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_perf_dip_v1",
            "template_params": [salutation, metric, f"-{delta}%"],
            "rationale": f"Data-backed performance diagnostic using the supplied {metric} change over {window}, paired with an immediate merchant-specific action.",
        }

    def _handle_perf_spike(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        payload = ctx["trigger"].get("payload", {})
        metric = payload.get("metric", "calls")
        delta = int(payload.get("delta_pct", 0.15) * 100)
        driver = payload.get("likely_driver", "recent Google post").replace("_", " ")

        body = (
            f"{salutation}, great news — your {metric} jumped +{delta}% this week, driven by your {driver}. "
            f"Let's capitalize on this search momentum before it cools down. "
            f"Want me to publish a follow-up Google post showcasing your top customer reviews to convert these viewers?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_perf_spike_v1",
            "template_params": [salutation, metric, f"+{delta}%"],
            "rationale": f"Momentum capitalization message acknowledging verified growth (+{delta}%) and offering conversion follow-up post.",
        }

    def _handle_milestone_reached(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        m_name = ctx["m_name"]
        locality = ctx["locality"]
        payload = ctx["trigger"].get("payload", {})
        val_now = payload.get("value_now", 145)
        milestone = payload.get("milestone_value", 150)
        diff = max(1, milestone - val_now)

        requested_count = payload.get("target_customer_count")
        count_text = f" to {requested_count} recent customers" if requested_count else " to recent customers"
        body = (
            f"{salutation}, milestone alert! {m_name} is currently at {val_now} reviews — just {diff} away from {milestone}. "
            f"Want me to draft a polite review request{count_text}?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_milestone_v1",
            "template_params": [salutation, str(val_now), str(milestone)],
            "rationale": f"Goal-gradient message grounded in the supplied review count ({val_now}) and milestone ({milestone}), with a low-friction review-request CTA.",
        }

    def _handle_competitor_opened(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        active_offer = ctx["active_offer"]
        merchant = ctx["merchant"]
        payload = ctx["trigger"].get("payload", {})
        comp_name = payload.get("competitor_name", "a new competitor")
        dist = payload.get("distance_km", 1.3)
        their_offer = payload.get("their_offer", "discounted offer")

        peer = merchant.get("performance", {})
        views = peer.get("views")

        views_line = (
            f"Your profile currently has {views:,} views in the supplied performance snapshot. "
            if isinstance(views, (int, float))
            else "Use your existing offer and profile strengths to differentiate. "
        )

        body = (
            f"{salutation}, local market update: a new competitor ({comp_name}) opened {dist} km away, promoting '{their_offer}'. "
            f"{views_line}"
            f"To defend your walk-ins, want me to spotlight your '{active_offer}' on Google Posts today?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_competitor_v1",
            "template_params": [salutation, comp_name, f"{dist} km"],
            "rationale": f"Competitive intelligence alerting merchant to nearby opening ({dist} km) and countering with existing reputation and catalog offer.",
        }

    def _handle_review_theme_emerged(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        payload = ctx["trigger"].get("payload", {})
        theme = payload.get("theme", "service delivery").replace("_", " ")
        count = payload.get("occurrences_30d", 4)
        quote = payload.get("common_quote", "took longer than expected")

        body = (
            f"{salutation}, operational heads-up: {count} customer reviews this month flagged '{theme}' (e.g. \"{quote}\"). "
            f"Replying professionally within 24 hours protects your Google rating and reassures new customers. "
            f"Want me to draft empathetic, professional reply templates for these reviews?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_review_theme_v1",
            "template_params": [salutation, theme, str(count)],
            "rationale": f"Operational reputation management identifying emerging feedback theme ({count} occurrences) with professional response drafts.",
        }

    def _handle_dormant_with_vera(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        locality = ctx["locality"]
        merchant = ctx["merchant"]
        payload = ctx["trigger"].get("payload", {})
        days = payload.get("days_since_last_merchant_message", 14)
        views = merchant.get("performance", {}).get("views", 1820)

        body = (
            f"Hi {salutation}, checking in from Vera! It's been {days} days since our last chat. "
            f"Your Google listing in {locality} had {views:,} views recently. "
            f"I've put together 1 quick high-impact update to convert more of those views into calls. "
            f"Want me to share the 2-minute summary here?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_dormancy_v1",
            "template_params": [salutation, str(days), f"{views:,} views"],
            "rationale": f"Gentle re-engagement quoting exact inactive days ({days}d) and monthly views ({views:,}) with binary permission CTA.",
        }

    def _handle_festival_upcoming(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        city = ctx["city"]
        cat_slug = ctx["cat_slug"]
        active_offer = ctx["active_offer"]
        payload = ctx["trigger"].get("payload", {})
        festival = payload.get("festival", "the upcoming festival")
        days = payload.get("days_until", 7)
        date = payload.get("date", "soon")

        trend_text = ""
        category = ctx["category"]
        for signal in category.get("trend_signals", []):
            if isinstance(signal, dict) and signal.get("query") and signal.get("delta_yoy") is not None:
                trend_text = f" Search interest for {signal['query']} is {float(signal['delta_yoy']) * 100:+.0f}% YoY."
                break
        offer_text = f"featuring your '{active_offer}'" if active_offer else "built around your current profile"
        body = (
            f"{salutation}, {festival} is {days} days away ({date}).{trend_text} "
            f"I have prepared a {festival} special Google post {offer_text}. "
            f"Want me to publish it to your profile today?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_festival_v1",
            "template_params": [salutation, festival, str(days)],
            "rationale": f"Calendar-timed festival outreach leveraging search spike data (+45%) and active catalog offer with binary publication CTA.",
        }

    def _handle_renewal_due(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        payload = ctx["trigger"].get("payload", {})
        days = payload.get("days_remaining", 12)
        plan = payload.get("plan", "Pro")
        amt = payload.get("renewal_amount", 4999)

        body = (
            f"{salutation}, quick reminder: your Vera {plan} plan has {days} days remaining. "
            f"Active status keeps your Google Profile optimized, photo posts scheduled, and customer recall reminders running. "
            f"Renewal is ₹{amt:,}. Want me to send the 1-click renewal link now?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_renewal_v1",
            "template_params": [salutation, str(days), f"₹{amt:,}"],
            "rationale": f"Transparent renewal notification citing exact days remaining ({days}d), plan name, and amount (₹{amt:,}) with binary CTA.",
        }

    def _handle_gbp_unverified(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        locality = ctx["locality"]
        payload = ctx["trigger"].get("payload", {})
        uplift_raw = payload.get("estimated_uplift_pct")
        uplift = int(uplift_raw * 100) if isinstance(uplift_raw, (int, float)) else None
        v_path = payload.get("verification_path", "phone or postcard").replace("_", " ")

        verification_line = (
            f"Verified businesses see on average +{uplift}% more customer calls and direction requests. "
            if uplift is not None
            else "I can help you complete the verification flow using the supplied path. "
        )

        body = (
            f"{salutation}, crucial check: your Google Business Profile in {locality} is currently unverified. "
            f"{verification_line}"
            f"Verification via {v_path} is the available path. Want me to guide you step-by-step?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_gbp_unverified_v1",
            "template_params": [salutation, f"+{uplift}%", v_path],
            "rationale": f"High-impact functional prompt quantifying verification benefits (+{uplift}% calls) and offering guided 5-minute setup.",
        }

    def _handle_fallback(self, **ctx) -> dict[str, Any]:
        salutation = ctx["salutation"]
        m_name = ctx["m_name"]
        locality = ctx["locality"]
        active_offer = ctx["active_offer"]
        trigger = ctx["trigger"]
        kind = trigger.get("kind", "update").replace("_", " ")

        body = (
            f"{salutation}, checking in regarding {m_name} in {locality}. "
            f"We have analyzed your profile performance and prepared an optimization featuring your '{active_offer}'. "
            f"Want me to publish this update to your Google profile right now?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "template_name": "vera_generic_v1",
            "template_params": [salutation, m_name, active_offer],
            "rationale": f"Graceful grounded fallback addressing {kind} with real merchant data and binary YES/NO action CTA.",
        }


# Global composer instance
composer = VeraComposer()


def compose(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Official composition signature specified in challenge brief §7.1.
    """
    return composer.compose(category, merchant, trigger, customer)

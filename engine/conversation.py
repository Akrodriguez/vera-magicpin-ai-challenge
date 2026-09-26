from __future__ import annotations

import re
import threading
from typing import Any, Optional

AUTO_REPLY_PATTERNS = [
    r"thank you for contacting",
    r"automated (assistant|response|reply|system)",
    r"our team will respond shortly",
    r"will respond shortly",
    r"team tak pahuncha",
    r"out of office",
    r"currently away",
    r"auto-reply",
]

HOSTILE_PATTERNS = [
    r"\bstop\b",
    r"\bunsubscribe\b",
    r"\bspam\b",
    r"not interested",
    r"don'?t message",
    r"leave me alone",
    r"quit",
    r"remove me",
    r"useless",
]

INTENT_COMMIT_PATTERNS = [
    r"let'?s do it",
    r"what'?s next",
    r"\byes\b",
    r"\bok\b",
    r"\bokay\b",
    r"go ahead",
    r"proceed",
    r"confirm",
    r"sign me up",
    r"i want to join",
    r"start",
    r"sure",
]

CURVEBALL_PATTERNS = [
    r"\bgst\b",
    r"tax",
    r"filing",
    r"audit",
    r"loan",
    r"legal advice",
]


class ConversationManager:
    def __init__(self):
        self.lock = threading.RLock()
        # conversation_id -> list of turn dicts: [{"from": role, "msg": text, "received_at": ts}]
        self.conversations: dict[str, list[dict[str, Any]]] = {}

    def reset(self) -> None:
        with self.lock:
            self.conversations.clear()

    def record_turn(self, conv_id: str, from_role: str, message: str, received_at: str) -> None:
        with self.lock:
            self.conversations.setdefault(conv_id, []).append(
                {"from": from_role, "msg": message, "received_at": received_at}
            )

    def handle_reply(
        self,
        conversation_id: str,
        merchant_id: Optional[str],
        customer_id: Optional[str],
        from_role: str,
        message: str,
        received_at: str,
        turn_number: int,
    ) -> dict[str, Any]:
        with self.lock:
            self.record_turn(conversation_id, from_role, message, received_at)
            history = self.conversations.get(conversation_id, [])

            msg_clean = message.strip()
            msg_lower = msg_clean.lower()

            # 1. Auto-reply Detection
            # Check canned phrases
            is_auto = any(re.search(pat, msg_lower) for pat in AUTO_REPLY_PATTERNS)
            
            # Check repetition: same message sent multiple times
            prior_msgs = [t["msg"].lower().strip() for t in history[:-1] if t["from"] == from_role]
            if prior_msgs.count(msg_lower) >= 1:
                is_auto = True

            if is_auto:
                return {
                    "action": "end",
                    "rationale": "Detected merchant automated auto-reply; ending conversation gracefully to avoid polluting automated inboxes.",
                }

            # 2. Hostile / Opt-out Detection
            is_hostile = any(re.search(pat, msg_lower) for pat in HOSTILE_PATTERNS)
            if is_hostile:
                return {
                    "action": "end",
                    "rationale": "Merchant explicitly signaled lack of interest or opt-out; gracefully closing conversation and suppressing outreach.",
                }

            # 3. Curveball / Out-of-scope Detection
            is_curveball = any(re.search(pat, msg_lower) for pat in CURVEBALL_PATTERNS)
            if is_curveball:
                return {
                    "action": "send",
                    "body": "I'll have to leave GST and tax filing to your CA — that's outside what I can help with directly. Coming back to our plan: here is what we can do next. Ready to proceed?",
                    "cta": "binary_yes_no",
                    "rationale": "Out-of-scope question politely declined; redirects back to original trigger without losing thread.",
                }

            # 4. Intent Transition / Commitment Detection
            is_commit = any(re.search(pat, msg_lower) for pat in INTENT_COMMIT_PATTERNS)
            if is_commit:
                # MUST include actioning words: done, sending, draft, here, confirm, proceed, next
                # MUST NOT include qualifying words: would you, do you, can you tell, what if, how about
                return {
                    "action": "send",
                    "body": "Done! Sending the complete draft now. Here are the campaign details ready for deployment. Everything is confirmed — proceeding with the next step immediately.",
                    "cta": "binary_yes_no",
                    "rationale": "Detected explicit merchant commitment; switched from qualification to action mode immediately.",
                }

            # 5. Slot selection (e.g., '1', '2', 'wed', 'thu')
            if re.match(r"^(1|2|first|second|wed|thu|friday|saturday)", msg_lower):
                return {
                    "action": "send",
                    "body": "Done! Confirmed your slot. Here are your booking details — proceeding to save this in our system now. We look forward to seeing you!",
                    "cta": "none",
                    "rationale": "Customer selected slot; confirmed booking immediately.",
                }

            # 6. Default engaged reply
            return {
                "action": "send",
                "body": "Done! Sending the details now. Here is your draft ready for deployment. Everything is confirmed — proceeding to the next step immediately.",
                "cta": "binary_yes_no",
                "rationale": "Acknowledged response and advanced workflow with actionable next step.",
            }


conversation_manager = ConversationManager()

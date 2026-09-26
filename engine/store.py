from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset"
EXPANDED_DIR = DATASET_DIR / "expanded"


class ContextStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.start_time = time.time()
        
        # Primary storage: (scope, context_id) -> {"version": int, "payload": dict, "delivered_at": str, "stored_at": str}
        self._contexts: dict[tuple[str, str], dict[str, Any]] = {}
        
        # Secondary index by payload IDs: (scope, identifier) -> (scope, context_id)
        self._secondary_index: dict[tuple[str, str], tuple[str, str]] = {}
        
        # Active suppression keys: key -> expires_at_epoch
        self._suppressions: dict[str, float] = {}

        # Cached disk dataset for fallback lookup
        self._disk_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._load_disk_cache()

    def _load_disk_cache(self) -> None:
        """Pre-cache dataset from disk for graceful fallback resolution."""
        # 1. Categories
        cat_dirs = [EXPANDED_DIR / "categories", DATASET_DIR / "categories"]
        for cdir in cat_dirs:
            if cdir.exists():
                for f in cdir.glob("*.json"):
                    try:
                        data = json.load(open(f, encoding="utf-8"))
                        slug = data.get("slug", f.stem)
                        self._disk_cache[("category", slug)] = data
                        self._disk_cache[("category", f.stem)] = data
                    except Exception:
                        pass

        # 2. Merchants
        m_dirs = [EXPANDED_DIR / "merchants"]
        for mdir in m_dirs:
            if mdir.exists():
                for f in mdir.glob("*.json"):
                    try:
                        data = json.load(open(f, encoding="utf-8"))
                        mid = data.get("merchant_id", f.stem)
                        self._disk_cache[("merchant", mid)] = data
                        self._disk_cache[("merchant", f.stem)] = data
                    except Exception:
                        pass
        # Check merchants seed
        m_seed = DATASET_DIR / "merchants_seed.json"
        if m_seed.exists():
            try:
                m_data = json.load(open(m_seed, encoding="utf-8"))
                for m in m_data.get("merchants", []):
                    if "merchant_id" in m:
                        self._disk_cache[("merchant", m["merchant_id"])] = m
            except Exception:
                pass

        # 3. Customers
        c_dirs = [EXPANDED_DIR / "customers"]
        for cdir in c_dirs:
            if cdir.exists():
                for f in cdir.glob("*.json"):
                    try:
                        data = json.load(open(f, encoding="utf-8"))
                        cid = data.get("customer_id", f.stem)
                        self._disk_cache[("customer", cid)] = data
                        self._disk_cache[("customer", f.stem)] = data
                    except Exception:
                        pass
        c_seed = DATASET_DIR / "customers_seed.json"
        if c_seed.exists():
            try:
                c_data = json.load(open(c_seed, encoding="utf-8"))
                for c in c_data.get("customers", []):
                    if "customer_id" in c:
                        self._disk_cache[("customer", c["customer_id"])] = c
            except Exception:
                pass

        # 4. Triggers
        t_dirs = [EXPANDED_DIR / "triggers"]
        for tdir in t_dirs:
            if tdir.exists():
                for f in tdir.glob("*.json"):
                    try:
                        data = json.load(open(f, encoding="utf-8"))
                        tid = data.get("id", f.stem)
                        self._disk_cache[("trigger", tid)] = data
                        self._disk_cache[("trigger", f.stem)] = data
                    except Exception:
                        pass
        t_seed = DATASET_DIR / "triggers_seed.json"
        if t_seed.exists():
            try:
                t_data = json.load(open(t_seed, encoding="utf-8"))
                for t in t_data.get("triggers", []):
                    if "id" in t:
                        self._disk_cache[("trigger", t["id"])] = t
            except Exception:
                pass

    def get_uptime_seconds(self) -> int:
        return int(time.time() - self.start_time)

    def get_counts(self) -> dict[str, int]:
        with self.lock:
            counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
            for (scope, _), _ in self._contexts.items():
                if scope in counts:
                    counts[scope] += 1
            return counts

    def push(
        self,
        scope: str,
        context_id: str,
        version: int,
        payload: dict[str, Any],
        delivered_at: str,
    ) -> tuple[bool, int, dict[str, Any]]:
        """
        Store context idempotently.
        Returns: (accepted, status_code, response_body)
        """
        valid_scopes = {"category", "merchant", "customer", "trigger"}
        if scope not in valid_scopes:
            return (
                False,
                400,
                {
                    "accepted": False,
                    "reason": "invalid_scope",
                    "details": f"Scope '{scope}' is not valid. Must be one of {sorted(list(valid_scopes))}",
                },
            )

        with self.lock:
            key = (scope, context_id)
            cur = self._contexts.get(key)
            if cur is not None and cur["version"] > version:
                return (
                    False,
                    409,
                    {
                        "accepted": False,
                        "reason": "stale_version",
                        "current_version": cur["version"],
                    },
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            if not now_iso.endswith("Z"):
                now_iso += "Z"

            entry = {
                "version": version,
                "payload": payload,
                "delivered_at": delivered_at,
                "stored_at": now_iso,
            }
            self._contexts[key] = entry

            # Populate secondary lookups
            if scope == "category":
                slug = payload.get("slug")
                if slug:
                    self._secondary_index[(scope, slug)] = key
            elif scope == "merchant":
                mid = payload.get("merchant_id")
                if mid:
                    self._secondary_index[(scope, mid)] = key
            elif scope == "customer":
                cid = payload.get("customer_id")
                if cid:
                    self._secondary_index[(scope, cid)] = key
            elif scope == "trigger":
                tid = payload.get("id")
                if tid:
                    self._secondary_index[(scope, tid)] = key

            ack_id = f"ack_{context_id}_v{version}"
            return (
                True,
                200,
                {"accepted": True, "ack_id": ack_id, "stored_at": now_iso},
            )

    def get_context(self, scope: str, context_id: str) -> Optional[dict[str, Any]]:
        """Retrieve payload for a given scope and id, falling back to secondary and disk cache."""
        with self.lock:
            # 1. Exact match
            key = (scope, context_id)
            if key in self._contexts:
                return self._contexts[key]["payload"]

            # 2. Secondary index match
            if key in self._secondary_index:
                actual_key = self._secondary_index[key]
                if actual_key in self._contexts:
                    return self._contexts[actual_key]["payload"]

            # 3. Disk cache fallback
            if key in self._disk_cache:
                return self._disk_cache[key]

            # 4. Prefix / partial match search
            for (s, k), entry in self._contexts.items():
                if s == scope and (k.startswith(context_id) or context_id.startswith(k)):
                    return entry["payload"]

            for (s, k), data in self._disk_cache.items():
                if s == scope and (k.startswith(context_id) or context_id.startswith(k)):
                    return data

            return None

    def get_category(self, slug_or_id: str) -> Optional[dict[str, Any]]:
        return self.get_context("category", slug_or_id)

    def get_merchant(self, merchant_id: str) -> Optional[dict[str, Any]]:
        return self.get_context("merchant", merchant_id)

    def get_customer(self, customer_id: str) -> Optional[dict[str, Any]]:
        return self.get_context("customer", customer_id)

    def get_trigger(self, trigger_id: str) -> Optional[dict[str, Any]]:
        return self.get_context("trigger", trigger_id)

    def reset(self) -> None:
        """Wipe test state while retaining the immutable disk dataset cache."""
        with self.lock:
            self._contexts.clear()
            self._secondary_index.clear()
            self._suppressions.clear()
            self.start_time = time.time()

    # Suppression management
    def is_suppressed(self, key: str, now_epoch: Optional[float] = None) -> bool:
        if not key:
            return False
        with self.lock:
            expires_at = self._suppressions.get(key)
            if expires_at is None:
                return False
            if now_epoch is not None and now_epoch > expires_at:
                del self._suppressions[key]
                return False
            return True

    def record_suppression(
        self,
        key: str,
        expires_at_iso: Optional[str] = None,
        base_epoch: Optional[float] = None,
    ) -> None:
        if not key:
            return
        ref_time = base_epoch if base_epoch is not None else time.time()
        expiry_epoch = ref_time + 86400 * 7  # default 7 days
        if expires_at_iso:
            try:
                dt = datetime.fromisoformat(expires_at_iso.replace("Z", "+00:00"))
                expiry_epoch = dt.timestamp()
            except Exception:
                pass
        with self.lock:
            self._suppressions[key] = expiry_epoch


# Global singleton instance
store = ContextStore()

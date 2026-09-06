"""Generate the four demo-scenario fixtures (docs/06-demo-scenarios.md).

The GSTIN is computed with rules.gstin_checksum() — never hand-written, or R04
would fire in every scenario and mask the findings each one is meant to show.

    python samples/make_fixtures.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.rules import gstin_checksum  # noqa: E402

OUT = Path(__file__).parent
ENTITY = "Sundaram Industrial Supplies LLP"

GSTIN_KA = "29ABCFS1234K1Z" + gstin_checksum("29ABCFS1234K1Z")
PAN = "ABCFS1234K"          # 4th character F = Firm/LLP, consistent with LLP

# The five documents the engine accepts. The same PDFs are printed by
# make_pdfs.py, and every value on them matches the submission below.
ALL_DOCS = {
    "pan_card": "pan_card.pdf",
    "gst_certificate": "gst_certificate.pdf",
    "incorporation_certificate": "incorporation_certificate.pdf",
    "bank_proof": "bank_proof.pdf",
    "address_proof": "address_proof.pdf",
}


def submission(**overrides) -> dict:
    s = {
        "legal_entity_name": ENTITY,
        "entity_type": "LLP",
        "country_of_incorporation": "IN",
        "registered_address": "42 Industrial Layout, Koramangala, Bengaluru",
        "registered_address_state": "Karnataka",
        "contact_name": "Priya Raghavan",
        "contact_email": "priya@sundaramsupplies.in",
        "contact_phone": "+91 80 4123 7788",
        "tax_id_type": "GSTIN",
        "gstin": GSTIN_KA,
        "pan": PAN,
        "account_holder_name": ENTITY,
        "account_number": "50200071234567",
        "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank",
    }
    s.update(overrides)
    return s


SCENARIOS = {
    "ec1_happy": {
        "label": "EC-1 · Happy path",
        "expected_status": "APPROVED",
        "expected_rules": [],
        "documents": ALL_DOCS,
        "submission": submission(),
    },
    "ec2_incomplete": {
        "label": "EC-2 · Incomplete submission",
        "expected_status": "PENDING",
        "expected_rules": ["R01", "R02"],
        # Two of the five documents were never attached, and the form is missing
        # a field. Everything that *was* sent is valid — this is the recoverable
        # case, the only one that produces a vendor-facing email.
        "documents": {k: ALL_DOCS[k] for k in
                      ("pan_card", "gst_certificate", "bank_proof")},
        "submission": submission(contact_phone=""),
    },
    "ec3_bank_mismatch": {
        "label": "EC-3 · Bank beneficiary mismatch",
        "expected_status": "REJECTED",
        "expected_rules": ["R09"],
        "documents": dict(ALL_DOCS, bank_proof="bank_proof_mismatch.pdf"),
        "submission": submission(account_holder_name="S. Ramesh Kumar"),
    },
    "ec4_crossfield": {
        "label": "EC-4 · Cross-field identity contradiction",
        "expected_status": "REJECTED",
        # R06/R07/R08 come out of the 15-character GSTIN alone. R15/R16/R18 are
        # the same three contradictions seen a second time, from the documents:
        # the vendor's own PAN card and GST certificate carry the real PAN, and
        # the electricity bill is in the real state.
        "expected_rules": ["R06", "R07", "R08", "R15", "R16", "R18"],
        "documents": ALL_DOCS,
        "submission": submission(
            pan="ABCFS1234Z",                    # differs from the GSTIN-embedded PAN
            entity_type="Proprietorship",        # contradicts PAN 4th char 'F'
            registered_address_state="Maharashtra"),  # contradicts state code 29
    },
}


def main():
    for name, scenario in SCENARIOS.items():
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(scenario, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.name:26} {scenario['expected_status']:9} "
              f"{scenario['expected_rules'] or '(no findings)'}")
    print(f"\nGSTIN {GSTIN_KA}  (checksum '{GSTIN_KA[-1]}' computed, not typed)")


if __name__ == "__main__":
    main()

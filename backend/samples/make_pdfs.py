"""Generate the sample vendor documents used by the demo scenarios.

Run once; the PDFs are committed. Only reportlab is needed to regenerate them,
which is why it is not an application dependency.

    python samples/make_pdfs.py
"""

import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.rules import gstin_checksum  # noqa: E402

OUT = Path(__file__).parent / "pdfs"
ENTITY = "Sundaram Industrial Supplies LLP"
PAN = "ABCFS1234K"          # 4th character F = Firm/LLP, consistent with LLP
# Never hand-write the 15th character — it is a mod-36 checksum (R04).
GSTIN = "29ABCFS1234K1Z" + gstin_checksum("29ABCFS1234K1Z")


def _page(path: Path, title: str, issuer: str, rows, footer=None, omit=()):
    OUT.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    y = h - 30 * mm

    c.setFont("Helvetica-Bold", 13)
    c.drawString(25 * mm, y, issuer)
    y -= 7 * mm
    c.setFont("Helvetica", 9)
    c.drawString(25 * mm, y, title)
    y -= 4 * mm
    c.line(25 * mm, y, w - 25 * mm, y)
    y -= 12 * mm

    for label, value in rows:
        c.setFont("Helvetica", 10)
        c.drawString(25 * mm, y, label)
        c.setFont("Helvetica-Bold", 10)
        # An omitted row keeps its label and leaves the value area blank, the way
        # a smudged or truncated scan does. The extractor must return null.
        c.drawString(85 * mm, y, "" if label in omit else value)
        y -= 8 * mm

    if footer:
        y -= 6 * mm
        c.setFont("Helvetica-Oblique", 8)
        for line in footer:
            c.drawString(25 * mm, y, line)
            y -= 5 * mm

    c.showPage()
    c.save()
    print("wrote", path.relative_to(Path(__file__).parent.parent))


def main():
    # Every heading names the document the way its own issuer would, because
    # R13 matches DOCUMENT_TYPE_KEYWORDS against what the paper calls itself.
    _page(OUT / "pan_card.pdf",
          "PERMANENT ACCOUNT NUMBER CARD",
          "Income Tax Department — Government of India",
          [("Permanent Account Number", PAN),
           ("Name", ENTITY),
           ("Date of Incorporation", "11/04/2019")],
          footer=["Issued under the Income-tax Act, 1961."])

    _page(OUT / "gst_certificate.pdf",
          "FORM GST REG-06 — GST REGISTRATION CERTIFICATE",
          "Government of India — Goods and Services Tax",
          [("Registration Number (GSTIN)", GSTIN),
           ("Legal Name", ENTITY),
           ("Trade Name", "Sundaram Supplies"),
           ("Constitution of Business", "Limited Liability Partnership"),
           ("State", "Karnataka"),
           ("Date of Liability", "01/07/2019")],
          footer=["This is a system generated certificate."])

    _page(OUT / "incorporation_certificate.pdf",
          "CERTIFICATE OF INCORPORATION",
          "Ministry of Corporate Affairs — Government of India",
          [("Name of Limited Liability Partnership", ENTITY),
           ("LLP Identification Number", "AAB-1234"),
           ("Date of Incorporation", "11 April 2019"),
           ("Registered Office State", "Karnataka")],
          footer=["Issued under the Limited Liability Partnership Act, 2008.",
                  "This is a computer generated certificate."])

    bank_rows = [("Account Holder Name", ENTITY),
                 ("Account Number", "50200071234567"),
                 ("IFSC Code", "HDFC0001234"),
                 ("Branch", "Koramangala, Bengaluru"),
                 ("Account Type", "Current Account")]

    _page(OUT / "bank_proof.pdf", "BANK ACCOUNT CONFIRMATION LETTER",
          "HDFC Bank Limited", bank_rows,
          footer=["We confirm the above account is held with our branch and is active."])

    # EC-3: the account belongs to an individual, not the vendor entity.
    _page(OUT / "bank_proof_mismatch.pdf", "BANK ACCOUNT CONFIRMATION LETTER",
          "HDFC Bank Limited",
          [("Account Holder Name", "S. Ramesh Kumar")] + bank_rows[1:],
          footer=["We confirm the above account is held with our branch and is active."])

    # Safety fixture: the IFSC line is blank on the document. The extractor must
    # return null rather than borrow the value from anywhere else.
    _page(OUT / "bank_proof_illegible.pdf", "BANK ACCOUNT CONFIRMATION LETTER",
          "HDFC Bank Limited", bank_rows, omit=("IFSC Code",),
          footer=["Portions of this scan are not legible."])

    _page(OUT / "address_proof.pdf",
          "ELECTRICITY BILL — TAX INVOICE",
          "Bangalore Electricity Supply Company Limited (BESCOM)",
          [("Consumer Name", ENTITY),
           ("Service Address", "42 Industrial Layout, Koramangala, Bengaluru"),
           ("PIN Code", "560095"),
           ("State", "Karnataka"),
           ("Billing Period", "01 July 2026 to 31 July 2026"),
           ("Amount Payable", "INR 18,420.00")],
          footer=["Please quote the consumer number for all correspondence."])


if __name__ == "__main__":
    main()

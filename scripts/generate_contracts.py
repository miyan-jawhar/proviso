"""
Proviso — Terms. Spend. Proof.
Synthetic Vendor Contract PDF Generator
========================================
Generates 50 realistic vendor contracts as PDF files.
- 35 normal contracts (standard terms)
- 5 contracts MISSING GDPR clause (trap for Legal Agent)
- 5 contracts with EXTREME penalties (30-40% late fees)
- 5 contracts with LOW monthly caps (overpayment detection)
"""

import os, random, json
from datetime import datetime, timedelta
from pathlib import Path
from faker import Faker
from fpdf import FPDF

fake = Faker()
Faker.seed(42)
random.seed(42)

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "contracts"
METADATA_FILE = Path(__file__).parent.parent / "data" / "contracts_metadata.json"

COUNTRIES = [
    "United States", "United Kingdom", "Germany", "India", "Singapore",
    "Australia", "Canada", "Netherlands", "Japan", "Brazil",
    "France", "Switzerland", "South Korea", "Ireland", "Sweden"
]
SERVICES = [
    "Cloud Infrastructure Services", "Managed IT Support",
    "Cybersecurity Consulting", "Data Analytics Platform",
    "Enterprise Resource Planning (ERP)", "Supply Chain Management",
    "Digital Transformation Advisory", "AI/ML Model Development",
    "Regulatory Compliance Auditing", "Workforce Management Solutions",
    "Financial Risk Analytics", "Blockchain Integration Services",
    "IoT Platform Management", "DevOps Pipeline Automation",
    "Customer Experience Platform", "HR Technology Solutions",
    "Business Process Outsourcing", "Network Infrastructure Management",
    "Software Quality Assurance", "Enterprise Data Warehousing"
]
GOVERNING_LAWS = [
    "the State of New York, United States",
    "England and Wales, United Kingdom",
    "the Federal Republic of Germany",
    "the Republic of India",
    "the Republic of Singapore",
]


def generate_vendor_pool(n=50):
    vendors, used = [], set()
    for i in range(1, n + 1):
        while True:
            name = fake.company()
            if name not in used:
                used.add(name)
                break
        vendors.append({"vendor_id": i, "vendor_name": name,
                        "country": random.choice(COUNTRIES),
                        "service": random.choice(SERVICES)})
    return vendors


def make_contract_data(vendor, ctype):
    eff = fake.date_between(start_date="-3y", end_date="-6m")
    dur = random.choice([12, 24, 36, 48, 60])
    exp = eff + timedelta(days=dur * 30)
    cap = random.randint(200_000, 500_000) if ctype == "low_cap" else random.randint(500_000, 5_000_000)
    fee = random.randint(30, 40) if ctype == "high_penalty" else random.randint(2, 5)
    gdpr = ctype != "missing_gdpr"
    risk = (random.choice(["HIGH", "CRITICAL"]) if ctype in ("high_penalty", "missing_gdpr")
            else ("MEDIUM" if ctype == "low_cap" else random.choice(["LOW", "MEDIUM"])))
    return {
        "vendor": vendor, "contract_type": ctype,
        "contract_number": f"CNT-{fake.unique.random_int(min=10000, max=99999)}",
        "effective_date": eff, "expiry_date": exp, "duration_months": dur,
        "monthly_cap": cap, "late_fee_pct": fee, "include_gdpr": gdpr,
        "governing_law": random.choice(GOVERNING_LAWS), "risk_tier": risk,
        "payment_terms_days": random.choice([30, 45, 60, 90]),
        "notice_period_days": random.choice([30, 60, 90]),
        "auto_renewal": random.choice([True, False]),
    }


class ContractPDF(FPDF):
    def __init__(self, cd):
        super().__init__()
        self.cd = cd
        self.set_auto_page_break(auto=True, margin=25)

    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, f"Contract No: {self.cd['contract_number']}", align="R")
        self.ln(4)
        self.set_draw_color(0, 102, 204)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(6)

    def footer(self):
        self.set_y(-20)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"CONFIDENTIAL - {self.cd['vendor']['vendor_name']} | Page {self.page_no()}/{{nb}}", align="C")

    def stitle(self, t):
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(0, 51, 102)
        self.ln(4)
        self.cell(0, 8, t, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(0, 102, 204)
        self.line(10, self.get_y(), 120, self.get_y())
        self.ln(4)

    def body(self, t):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 6, t)
        self.ln(2)

    def clause(self, n, t):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(30, 30, 30)
        self.cell(15, 6, n)
        self.set_font("Helvetica", "", 10)
        x, y = self.get_x(), self.get_y()
        self.set_xy(x, y)
        self.multi_cell(0, 6, t)
        self.ln(2)

    def build(self):
        c, v = self.cd, self.cd["vendor"]
        self.alias_nb_pages()
        self.add_page()

        # Title
        self.set_font("Helvetica", "B", 18)
        self.set_text_color(0, 51, 102)
        self.cell(0, 12, "MASTER SERVICE AGREEMENT", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)
        self.set_font("Helvetica", "", 11)
        self.set_text_color(80, 80, 80)
        self.cell(0, 8, 'Between: Global Enterprise Holdings Ltd. ("Client")', align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 8, f'And: {v["vendor_name"]} ("Vendor")', align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(6)

        # Section 1: Parties
        self.stitle("SECTION 1 - PARTIES AND RECITALS")
        addr = fake.address().replace("\n", ", ")
        self.body(
            f'This Master Service Agreement is entered into as of '
            f'{c["effective_date"].strftime("%B %d, %Y")} by and between Global Enterprise Holdings Ltd., '
            f'a corporation organized under the laws of the United Kingdom, with principal offices at '
            f'25 Canary Wharf, London, E14 5AB ("Client"), and {v["vendor_name"]}, '
            f'organized under the laws of {v["country"]}, with offices at {addr} ("Vendor").')
        self.body(
            f'WHEREAS, the Vendor specializes in {v["service"]} and the Client '
            f'wishes to engage the Vendor to provide such services under the terms herein.')

        # Section 2: Scope
        self.stitle("SECTION 2 - SCOPE OF SERVICES")
        self.clause("2.1", f'The Vendor shall provide {v["service"]} as described in the SOW attached as Exhibit A.')
        self.clause("2.2", "All services shall be delivered in accordance with industry standards and SLAs in Exhibit B.")
        self.clause("2.3", f'The Vendor shall assign qualified personnel with relevant certifications and minimum 5 years experience in {v["service"]}.')

        # Section 3: Term
        self.stitle("SECTION 3 - TERM AND DURATION")
        self.clause("3.1",
            f'This Agreement becomes effective on {c["effective_date"].strftime("%B %d, %Y")} '
            f'and remains in force for {c["duration_months"]} months, expiring on {c["expiry_date"].strftime("%B %d, %Y")}.')
        rtxt = (f'This Agreement shall automatically renew for successive 12-month periods unless either party provides '
                f'written notice of non-renewal at least {c["notice_period_days"]} days prior to expiration.'
                if c["auto_renewal"] else
                f'This Agreement shall not automatically renew. Extension requires written agreement at least '
                f'{c["notice_period_days"]} days prior to expiration.')
        self.clause("3.2", rtxt)

        # Section 4: Payment
        self.add_page()
        self.stitle("SECTION 4 - COMPENSATION AND PAYMENT TERMS")
        cap_str = f'{c["monthly_cap"]:,}'
        self.clause("4.1",
            f'The Client shall compensate the Vendor at rates in the applicable SOW, subject to a '
            f'maximum monthly billing cap of INR {cap_str}.')
        self.clause("4.2",
            f'Invoices shall be submitted within 5 business days following each calendar month end. '
            f'Payment shall be made within {c["payment_terms_days"]} calendar days of valid invoice receipt.')
        penalty_note = " THIS CONSTITUTES AN ENHANCED PENALTY PROVISION." if c["late_fee_pct"] > 10 else ""
        self.clause("4.3",
            f'A late payment fee of {c["late_fee_pct"]}% per month shall be applied to all outstanding '
            f'invoices not paid within the stipulated payment period.{penalty_note}')

        # Section 5: Termination
        self.stitle("SECTION 5 - TERMINATION")
        self.clause("5.1", f'Either party may terminate for convenience by providing {c["notice_period_days"]} days written notice.')
        self.clause("5.2", 'Either party may terminate immediately upon material breach if the other party fails to cure within 30 days of notice.')
        self.clause("5.3", 'Upon termination, the Vendor is entitled to compensation for services rendered up to the effective termination date.')

        # Section 6: Confidentiality + GDPR
        self.stitle("SECTION 6 - CONFIDENTIALITY AND DATA PROTECTION")
        self.clause("6.1", 'Both parties agree to maintain confidentiality of all proprietary information. This obligation survives for 5 years following termination.')

        if c["include_gdpr"]:
            self.clause("6.2",
                'The Vendor guarantees full compliance with the General Data Protection Regulation (GDPR) '
                'under Article 28, acting as a Data Processor on behalf of the Client (Data Controller). '
                'The Vendor shall implement appropriate technical and organizational measures to ensure '
                'security of personal data, including encryption at rest and in transit, access controls, '
                'and regular security assessments.')
            self.clause("6.3",
                'In the event of a data breach affecting personal data, the Vendor shall notify the Client '
                'within 72 hours, providing a detailed incident report including breach nature and scope, '
                'categories of data affected, and remediation measures undertaken.')

        # Section 7: Liability
        self.add_page()
        self.stitle("SECTION 7 - LIABILITY AND INDEMNIFICATION")
        self.clause("7.1", "The Vendor's total aggregate liability shall not exceed the total fees paid during the 12-month period preceding the claim event.")
        self.clause("7.2", "The Vendor shall indemnify, defend, and hold harmless the Client from third-party claims arising from negligence, willful misconduct, or breach.")

        # Section 8: Governing Law
        self.stitle("SECTION 8 - GOVERNING LAW AND DISPUTE RESOLUTION")
        self.clause("8.1", f'This Agreement shall be governed by the laws of {c["governing_law"]}.')
        self.clause("8.2", "Disputes shall be settled through binding arbitration administered by the ICC.")

        # Signatures
        self.ln(15)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(0, 51, 102)
        self.cell(0, 8, "IN WITNESS WHEREOF", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)
        self.body("The parties hereto have executed this Agreement as of the date first written above.")
        self.ln(10)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(30, 30, 30)
        self.cell(90, 6, "For: Global Enterprise Holdings Ltd.")
        self.cell(90, 6, f'For: {v["vendor_name"]}', new_x="LMARGIN", new_y="NEXT")
        self.ln(15)
        self.set_font("Helvetica", "", 10)
        self.cell(90, 6, "________________________")
        self.cell(90, 6, "________________________", new_x="LMARGIN", new_y="NEXT")
        self.cell(90, 6, f"Name: {fake.name()}")
        self.cell(90, 6, f"Name: {fake.name()}", new_x="LMARGIN", new_y="NEXT")
        self.cell(90, 6, "Title: Chief Procurement Officer")
        self.cell(90, 6, f'Title: {random.choice(["CEO", "Managing Director", "VP Operations"])}', new_x="LMARGIN", new_y="NEXT")
        date_str = c["effective_date"].strftime("%B %d, %Y")
        self.cell(90, 6, f"Date: {date_str}")
        self.cell(90, 6, f"Date: {date_str}", new_x="LMARGIN", new_y="NEXT")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    vendors = generate_vendor_pool(50)

    types = ["normal"] * 35 + ["missing_gdpr"] * 5 + ["high_penalty"] * 5 + ["low_cap"] * 5
    random.shuffle(types)

    metadata = []
    for vendor, ctype in zip(vendors, types):
        cd = make_contract_data(vendor, ctype)
        pdf = ContractPDF(cd)
        pdf.build()
        safe = vendor["vendor_name"].replace(" ", "_").replace(",", "").replace(".", "")
        fname = f'contract_{vendor["vendor_id"]:03d}_{safe}.pdf'
        pdf.output(str(OUTPUT_DIR / fname))
        metadata.append({
            "vendor_id": vendor["vendor_id"], "vendor_name": vendor["vendor_name"],
            "country": vendor["country"], "service": vendor["service"],
            "contract_number": cd["contract_number"], "contract_type": ctype,
            "effective_date": cd["effective_date"].isoformat(),
            "expiry_date": cd["expiry_date"].isoformat(),
            "monthly_cap": cd["monthly_cap"], "late_fee_pct": cd["late_fee_pct"],
            "include_gdpr": cd["include_gdpr"], "risk_tier": cd["risk_tier"],
            "payment_terms_days": cd["payment_terms_days"], "filename": fname,
        })
        marker = "!" if ctype != "normal" else "+"
        print(f"  [{marker}] {fname} ({ctype})")

    METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=2)

    counts = {}
    for r in metadata:
        counts[r["contract_type"]] = counts.get(r["contract_type"], 0) + 1
    print(f"\n{'='*60}")
    print(f"  Generated {len(metadata)} contracts in data/contracts/")
    print(f"  Metadata saved to data/contracts_metadata.json")
    print(f"  Breakdown: {counts}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

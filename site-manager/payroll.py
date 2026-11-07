"""Payroll rules and month-end files for Site Manager.

Every statutory number here is a setting (see DEFAULT_SETTINGS), because rates and wage ceilings change.
Check them against current EPFO / ESIC notifications and have an accountant review the first payrolls.
"""
import calendar
import csv
import io
import math
from datetime import date, datetime

DEFAULT_SETTINGS = {
    "company_name": "My Company",
    "company_address": "",
    "month_days": 26,           # paid days in a month, for monthly-rated workers and daily rate of monthly staff
    "shift_hours": 8,           # normal hours a day; beyond this is overtime
    "half_day_hours": 4,        # less than this counts as half a day
    "ot_multiplier": 2,         # overtime paid at this times the normal hourly rate
    "pf_rate_employee": 12.0,   # % of PF wages
    "pf_rate_employer": 12.0,   # % of PF wages (EPS part below, rest to EPF)
    "eps_rate": 8.33,           # % of EPS wages, part of the employer share
    "pf_wage_ceiling": 15000,   # PF wages are capped at this (0 = no cap); check the current EPFO ceiling
    "eps_wage_ceiling": 15000,  # EPS/EDLI wages cap
    "esic_rate_employee": 0.75,
    "esic_rate_employer": 3.25,
    "esic_wage_ceiling": 21000, # ESIC applies when monthly wages (without overtime) are at or below this
    "gst_rate": 18.0,           # on client bills for manpower supply
    "checkin_gap_minutes": 30,  # a second scan within this time is ignored (not taken as check-out)
    "slip_language": "en",      # en or hi
}


def money(x):
    """Whole rupees, halves rounded up (Python's round() would send 0.5 to the even number)."""
    return int(math.floor(x + 0.5))


def month_bounds(month):
    """'2026-10' -> (date(2026,10,1), date(2026,10,31), days_in_month)"""
    y, m = (int(p) for p in month.split("-"))
    days = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, days), days


def day_hours(rec):
    """Hours worked from an attendance record's in/out times (None when there's no check-out)."""
    if rec.get("ot_override") is not None:
        return None
    if not rec.get("in_time") or not rec.get("out_time"):
        return None
    t1 = datetime.fromisoformat(rec["in_time"])
    t2 = datetime.fromisoformat(rec["out_time"])
    return max(0.0, (t2 - t1).total_seconds() / 3600)


def day_credit(rec, s):
    """(present_days, ot_hours) one attendance day contributes: manual status wins, else from hours."""
    if rec.get("review") == "rejected":
        return 0.0, 0.0
    status = rec.get("status") or "P"
    if status == "A":
        return 0.0, 0.0
    hours = day_hours(rec)
    if rec.get("ot_override") is not None:
        ot = float(rec["ot_override"])
    elif hours is not None:
        ot = math.floor(max(0.0, hours - s["shift_hours"]) * 2) / 2   # whole half-hours only
    else:
        ot = 0.0
    if status == "H":
        return 0.5, ot
    if status == "P" and rec.get("status_manual"):
        return 1.0, ot
    if hours is not None and hours < s["half_day_hours"]:
        return 0.5, 0.0
    return 1.0, ot


def compute_worker(worker, records, advances, s, month):
    """One worker's payroll line for a month. records: their attendance rows (flagged ones excluded
    unless approved); advances: amounts to recover this month."""
    _, _, dim = month_bounds(month)
    days = 0.0
    ot_hours = 0.0
    for r in records:
        d, o = day_credit(r, s)
        days += d
        ot_hours += o
    rate = float(worker["rate"] or 0)
    if worker.get("wage_type") == "monthly":
        daily = rate / s["month_days"]
        basic = min(rate, daily * days)
        monthly_wage = rate
    else:
        daily = rate
        basic = rate * days
        monthly_wage = rate * s["month_days"]
    ot_pay = ot_hours * (daily / s["shift_hours"]) * s["ot_multiplier"]
    gross = basic + ot_pay

    pf_ee = eps = er_epf = 0
    pf_wages = eps_wages = edli_wages = 0
    if worker.get("pf_member"):
        cap = s["pf_wage_ceiling"]
        pf_wages = money(min(basic, cap) if cap else basic)
        eps_wages = money(min(basic, s["eps_wage_ceiling"]))
        edli_wages = eps_wages
        pf_ee = money(pf_wages * s["pf_rate_employee"] / 100)
        eps = money(eps_wages * s["eps_rate"] / 100)
        er_epf = money(pf_wages * s["pf_rate_employer"] / 100) - eps
    esic_ee = esic_er = 0
    esic_applies = bool(worker.get("esic_member")) and monthly_wage <= s["esic_wage_ceiling"]
    if esic_applies and gross > 0:
        esic_ee = math.ceil(gross * s["esic_rate_employee"] / 100)   # ESIC rounds up to the next rupee
        esic_er = math.ceil(gross * s["esic_rate_employer"] / 100)
    adv_due = sum(advances)
    adv = min(adv_due, max(0, money(gross) - pf_ee - esic_ee))
    net = money(gross) - pf_ee - esic_ee - adv
    return {
        "worker_id": worker["id"], "code": worker["code"], "name": worker["name"],
        "designation": worker.get("designation") or "", "uan": worker.get("uan") or "",
        "esic_no": worker.get("esic_no") or "", "bank_account": worker.get("bank_account") or "",
        "ifsc": worker.get("ifsc") or "", "phone": worker.get("phone") or "",
        "wage_type": worker.get("wage_type") or "daily", "rate": rate,
        "days": days, "ot_hours": ot_hours, "basic": money(basic), "ot_pay": money(ot_pay), "gross": money(gross),
        "pf_wages": pf_wages, "eps_wages": eps_wages, "edli_wages": edli_wages,
        "pf_employee": pf_ee, "eps_employer": eps, "epf_employer": er_epf,
        "esic_employee": esic_ee, "esic_employer": esic_er,
        "advance": adv, "advance_carried": adv_due - adv, "net": net,
        "ncp_days": max(0, round(dim - days)) if worker.get("pf_member") else 0,
    }


# ------------------------------------------------------------------ month-end files
def bank_csv(lines, month, narration=None):
    """Generic NEFT bulk-payment sheet. Banks each have their own template: map these columns to it."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Beneficiary Name", "Beneficiary Account Number", "IFSC", "Amount", "Payment Mode", "Narration", "Worker Code"])
    label = narration or f"Salary {datetime.strptime(month, '%Y-%m').strftime('%b %Y')}"
    for l in lines:
        if l["net"] > 0 and l["bank_account"] and l["ifsc"]:
            w.writerow([l["name"], l["bank_account"], l["ifsc"].upper(), l["net"], "NEFT", label, l["code"]])
    return out.getvalue()


def ecr_text(lines):
    """EPFO ECR text: one member per line, fields joined by #~# in the order the ECR upload expects:
    UAN, name, gross wages, EPF wages, EPS wages, EDLI wages, EE share, EPS contribution, ER EPF share,
    NCP days, refund of advances. Validate the file on the EPFO portal before submitting."""
    rows = []
    for l in lines:
        if not l["uan"] or not l["pf_wages"]:
            continue
        rows.append("#~#".join(str(x) for x in [
            l["uan"], l["name"].upper(), l["gross"], l["pf_wages"], l["eps_wages"], l["edli_wages"],
            l["pf_employee"], l["eps_employer"], l["epf_employer"], l["ncp_days"], 0]))
    return "\n".join(rows) + ("\n" if rows else "")


FORM17_HEADER = ["Sl No", "Name of workman", "Serial No in register of workmen", "Designation / nature of work",
                 "No. of days worked", "Units of work done", "Daily rate of wages / piece rate",
                 "Basic wages", "Dearness allowance", "Overtime", "Other cash payments", "Total",
                 "PF deduction", "ESIC deduction", "Advances", "Total deductions", "Net amount paid",
                 "Signature / thumb impression", "Initials of contractor"]


def form17_rows(lines):
    """Register of wages (Form XVII under the Contract Labour Rules), one row per workman."""
    rows = []
    for i, l in enumerate(lines, 1):
        ded = l["pf_employee"] + l["esic_employee"] + l["advance"]
        rows.append([i, l["name"], l["code"], l["designation"], l["days"], "",
                     f"{l['rate']:g} / {'month' if l['wage_type'] == 'monthly' else 'day'}",
                     l["basic"], 0, l["ot_pay"], 0, l["gross"], l["pf_employee"], l["esic_employee"],
                     l["advance"], ded, l["net"], "", ""])
    return rows


def form17_csv(lines):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(FORM17_HEADER)
    w.writerows(form17_rows(lines))
    return out.getvalue()


def slip_text(l, month, company, lang="en"):
    """Short salary slip for WhatsApp."""
    mon = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    if lang == "hi":
        parts = [f"*{company}*", f"वेतन पर्ची · {mon}", f"{l['name']} ({l['code']})",
                 f"दिन: {l['days']:g} · ओवरटाइम: {l['ot_hours']:g} घंटे",
                 f"कुल वेतन: ₹{l['gross']:,}"]
        if l["pf_employee"]:
            parts.append(f"PF कटौती: ₹{l['pf_employee']:,}")
        if l["esic_employee"]:
            parts.append(f"ESIC कटौती: ₹{l['esic_employee']:,}")
        if l["advance"]:
            parts.append(f"एडवांस वसूली: ₹{l['advance']:,}")
        parts.append(f"*शुद्ध वेतन: ₹{l['net']:,}*")
    else:
        parts = [f"*{company}*", f"Salary slip · {mon}", f"{l['name']} ({l['code']})",
                 f"Days: {l['days']:g} · OT: {l['ot_hours']:g} hrs", f"Gross: ₹{l['gross']:,}"]
        if l["pf_employee"]:
            parts.append(f"PF: -₹{l['pf_employee']:,}")
        if l["esic_employee"]:
            parts.append(f"ESIC: -₹{l['esic_employee']:,}")
        if l["advance"]:
            parts.append(f"Advance recovered: -₹{l['advance']:,}")
        parts.append(f"*Net pay: ₹{l['net']:,}*")
    return "\n".join(parts)

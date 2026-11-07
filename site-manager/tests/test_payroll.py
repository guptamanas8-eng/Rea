"""Payroll rules, checked against hand-worked examples."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import payroll  # noqa: E402

S = dict(payroll.DEFAULT_SETTINGS)


def day(d, hours=None, **kw):
    rec = {"day": f"2026-10-{d:02d}", "in_time": f"2026-10-{d:02d}T08:00:00+05:30", "out_time": None, "status": "P", "review": "ok"}
    if hours is not None:
        h, m = int(8 + hours), int((hours % 1) * 60)
        rec["out_time"] = f"2026-10-{d:02d}T{h:02d}:{m:02d}:00+05:30"
    rec.update(kw)
    return rec


def worker(**kw):
    w = {"id": 1, "code": "W0001", "name": "Ramesh Kumar", "wage_type": "daily", "rate": 650, "pf_member": 1, "esic_member": 1,
         "uan": "100012345678", "bank_account": "12345678901", "ifsc": "SBIN0001234", "phone": "9876543210", "designation": "Fitter"}
    w.update(kw)
    return w


class Rules(unittest.TestCase):
    def test_daily_worker_full_month(self):
        # 26 days: 24 of 8 h, 2 of 14 h (6 h OT each) = 12 h OT
        recs = [day(d, 8) for d in range(1, 25)] + [day(25, 14), day(26, 14)]
        l = payroll.compute_worker(worker(), recs, [], S, "2026-10")
        self.assertEqual((l["days"], l["ot_hours"]), (26, 12))
        self.assertEqual(l["basic"], 16900)              # 650 x 26
        self.assertEqual(l["ot_pay"], 1950)              # 12 h x 650/8 x 2
        self.assertEqual(l["gross"], 18850)
        self.assertEqual(l["pf_wages"], 15000)           # capped
        self.assertEqual(l["pf_employee"], 1800)         # 12 %
        self.assertEqual(l["eps_employer"], 1250)        # 8.33 % of 15,000 = 1,249.50 -> 1,250
        self.assertEqual(l["epf_employer"], 550)         # 1,800 - 1,250
        self.assertEqual(l["esic_employee"], 142)        # 0.75 % of 18,850 = 141.38, rounded up
        self.assertEqual(l["esic_employer"], 613)        # 3.25 % = 612.63, rounded up
        self.assertEqual(l["net"], 18850 - 1800 - 142)
        self.assertEqual(l["ncp_days"], 31 - 26)

    def test_half_days_and_manual_status(self):
        recs = [day(1, 3), day(2, 8, status="H", status_manual=1), day(3, None, status="P", status_manual=1), day(4, 8, status="A", status_manual=1)]
        l = payroll.compute_worker(worker(pf_member=0, esic_member=0), recs, [], S, "2026-10")
        self.assertEqual(l["days"], 0.5 + 0.5 + 1 + 0)
        self.assertEqual(l["gross"], 1300)

    def test_rejected_days_dont_count(self):
        l = payroll.compute_worker(worker(), [day(1, 8), day(2, 8, review="rejected")], [], S, "2026-10")
        self.assertEqual(l["days"], 1)

    def test_ot_override_and_half_hour_steps(self):
        l = payroll.compute_worker(worker(), [day(1, 10.75), day(2, 8, ot_override=3)], [], S, "2026-10")
        self.assertEqual(l["ot_hours"], 2.5 + 3)        # 2 h 45 m counts as 2.5

    def test_monthly_salary_above_esic_ceiling(self):
        recs = [day(d, 8) for d in range(1, 27)]
        l = payroll.compute_worker(worker(wage_type="monthly", rate=30000), recs, [], S, "2026-10")
        self.assertEqual(l["basic"], 30000)
        self.assertEqual(l["esic_employee"], 0)          # 30,000 > 21,000
        self.assertEqual(l["pf_employee"], 1800)         # 12 % of the 15,000 ceiling

    def test_monthly_salary_part_month(self):
        l = payroll.compute_worker(worker(wage_type="monthly", rate=26000, pf_member=0, esic_member=0), [day(d, 8) for d in range(1, 14)], [], S, "2026-10")
        self.assertEqual(l["basic"], 13000)              # 26,000 / 26 x 13

    def test_advance_capped_at_pay(self):
        l = payroll.compute_worker(worker(pf_member=0, esic_member=0), [day(1, 8)], [500, 400], S, "2026-10")
        self.assertEqual((l["advance"], l["advance_carried"], l["net"]), (650, 250, 0))

    def test_no_pf_ceiling_setting(self):
        s = dict(S, pf_wage_ceiling=0)
        l = payroll.compute_worker(worker(wage_type="monthly", rate=30000, esic_member=0), [day(d, 8) for d in range(1, 27)], [], s, "2026-10")
        self.assertEqual(l["pf_employee"], 3600)
        self.assertEqual(l["eps_employer"], 1250)        # EPS still on the 15,000 EPS ceiling


class Files(unittest.TestCase):
    def setUp(self):
        self.lines = [payroll.compute_worker(worker(), [day(d, 8) for d in range(1, 27)], [], S, "2026-10"),
                      payroll.compute_worker(worker(id=2, code="W0002", name="Sunita Devi", uan="", bank_account="", pf_member=0), [day(1, 8)], [], S, "2026-10")]

    def test_bank_file_skips_missing_accounts(self):
        csv_text = payroll.bank_csv(self.lines, "2026-10")
        rows = csv_text.strip().splitlines()
        self.assertEqual(len(rows), 2)
        self.assertIn("Ramesh Kumar,12345678901,SBIN0001234,", rows[1])
        self.assertIn("Salary Oct 2026", rows[1])

    def test_ecr_format(self):
        ecr = payroll.ecr_text(self.lines).strip().split("\n")
        self.assertEqual(len(ecr), 1)                    # only the PF member with a UAN
        f = ecr[0].split("#~#")
        self.assertEqual(len(f), 11)
        self.assertEqual(f[:2], ["100012345678", "RAMESH KUMAR"])
        self.assertEqual(f[3:9], ["15000", "15000", "15000", "1800", "1250", "550"])
        self.assertEqual(f[9], "5")                      # NCP days: 31 - 26

    def test_form17_and_slip(self):
        rows = payroll.form17_rows(self.lines)
        self.assertEqual(rows[0][0], 1)
        self.assertEqual(len(payroll.FORM17_HEADER), len(rows[0]))
        self.assertIn("Net pay: ₹14,973", payroll.slip_text(self.lines[0], "2026-10", "Demo Infra"))
        self.assertIn("शुद्ध वेतन", payroll.slip_text(self.lines[0], "2026-10", "Demo Infra", "hi"))


if __name__ == "__main__":
    unittest.main()

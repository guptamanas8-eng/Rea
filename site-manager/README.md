# Site Manager

Attendance and payroll for contractors running workers across several sites.

- **Gate attendance without hardware:** each site gets a printable QR poster. Workers scan it on their own phone,
  enter their code (or phone number) and a 4-digit PIN, and take a selfie stamped with site, time and GPS. Check-ins
  from outside the site's geo-fence, without GPS, or without a selfie are recorded but **flagged** for review.
  Workers without smartphones are marked by a supervisor. The check-in page is in English and Hindi.
- **Live dashboard:** who is present and on site now, per site; flagged check-ins with selfies to approve or reject.
- **Monthly register:** present / half day / absent / flagged per worker per day, editable with an audit trail.
- **Payroll in one click** from approved attendance: daily or monthly wages, overtime, PF (EPF/EPS split), ESIC
  and advances. Finalize to lock a month.
- **Month-end files:** bank NEFT sheet (CSV), EPFO ECR text file, Form XVII register of wages (CSV and print),
  printable salary slips, and a WhatsApp button per worker that opens the slip ready to send (English or Hindi).
- **Site P&L and client bills:** mandays, labour cost (wages plus employer PF/ESIC, split by days at each site),
  bill at your rate per manday/OT hour, margin, and a printable bill with GST.
- **Logins:** admins see everything; supervisors see only their sites and no pay or bank details.

## Run it

Needs only Python 3.9 or newer, no packages:

```bash
cd site-manager
python3 app.py                          # http://localhost:8080
python3 app.py --port 9000 --data /srv/site-manager-data
```

Open the address, create your company and admin login, then: **Sites** → add a site (stand at the gate and use
"📍 Use my current location") → **QR poster** → print it for the gate. **Workers** → add them (or import a CSV
saved from Excel; the template is in the import box) and give each a 4-digit PIN.

All data lives in the `--data` folder (a SQLite database plus selfies). Back that folder up daily.

## Going live (HTTPS is required)

Phones only share GPS location with secure (`https://`) pages, so the check-in page must be served over HTTPS
from an address workers' phones can reach over mobile data. For example, on any small Linux server with a domain:

```bash
# Caddy gets and renews the HTTPS certificate automatically
caddy reverse-proxy --from attendance.yourcompany.in --to localhost:8080
SITE_MANAGER_HTTPS=1 python3 app.py --host 127.0.0.1 --port 8080 --data /srv/site-manager-data
```

`SITE_MANAGER_HTTPS=1` (or an `X-Forwarded-Proto: https` header from the proxy) marks login cookies as secure.
Print site posters **from the live address**, because the QR code holds the address the page was opened from.

## Check before you rely on it

- **Statutory numbers are settings,** not hard-coded: PF/EPS/ESIC rates and wage ceilings, paid days, shift hours
  and overtime rate. Check them against current EPFO and ESIC notifications, and have your accountant review the
  first payrolls.
- **ECR:** validate the generated file on the EPFO portal before submitting it.
- **Bank file:** banks each have their own bulk-upload template; map the columns (name, account, IFSC, amount,
  narration) to yours.
- **Privacy:** worker records include phone, bank account, UAN and selfies; only the last 4 Aadhaar digits are
  stored. Keep the server and its backups private, and use strong admin passwords.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Covers the payroll rules against hand-worked examples, the bank/ECR/Form XVII formats, and the full API flow
(setup, sites, workers, CSV import, geo-fenced QR check-in, PIN lockout, supervisor limits, payroll, files, reports).

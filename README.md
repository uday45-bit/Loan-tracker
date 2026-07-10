# Loanwise – Loan / EMI Tracker

A Streamlit app that tracks real debt obligations (bank loans, credit cards,
chit funds, gold loans) using each loan's **actual current outstanding
balance** as an anchor point, then projects forward with proper amortization
to answer:

- What's the outstanding principal today, combined across all loans?
- What's the total amount payable (principal + interest) by the time every
  loan closes?
- How much is due in a specific month?
- How much interest accrues between any two dates?

## Features

- **Dashboard** — Principal Outstanding (today), Total Payable (incl.
  interest, to closing), active loan count, and average monthly obligation
  (non-monthly installments like quarterly/bullet payments are normalized to
  a monthly-equivalent figure for this metric). Plus upcoming installments
  and an outstanding-by-lender chart.
- **Loans** — add a loan manually or bulk-upload via CSV. Each loan card
  shows **Outstanding Today** and **Total Payable (to close)** as separate
  figures, its full remaining amortization schedule, and a
  **"Mark installment paid"** button that advances the loan's anchor date
  and balance. Loans are listed in order of their opening date.
- **Month Lookup** — pick any year/month and see exactly which installments
  (principal, interest, total) fall due across every loan that month.
- **Interest Calculator** — pick a from/to date range and see total interest
  payable across all loans in that window, broken down per loan.

### Payment frequencies supported
- **Monthly** (`frequency = 1`) — standard EMI loans/cards.
- **Quarterly** (`frequency = 3`) — e.g. chit funds.
- **Annual** (`frequency = 12`).
- **Bullet** (`frequency = -1`) — interest-only until closing, then a single
  payment of principal + accrued interest (e.g. gold loans).

### How the math works
Each loan stores an **anchor date** (usually your last payment date) and the
**outstanding balance as of that date** — both taken directly from your bank
statement, not recalculated from scratch. From that anchor, the app
amortizes forward period-by-period using the loan's rate and EMI:

```
interest_this_period = balance × (annual_rate/100) × (frequency_months/12)
principal_this_period = EMI − interest_this_period
balance -= principal_this_period
```

If the schedule would leave a small residual balance at the final period
(common with fixed EMIs on odd tenures, or non-standard products like chit
funds), that residual is folded into the last installment as a balloon
payment so every loan fully closes out by its closing date. This was
verified against your uploaded spreadsheet — the projected "Outstanding
Today" ties out to your file's total (₹15,96,974 vs. ₹15,96,973.73) to the
rupee.

## `my_loans.csv`

Pre-filled with your 9 real loans, ready to bulk-import via the Loans page:

| Lender | Account | Outstanding | EMI | Frequency |
|---|---|---:|---:|---|
| IDFC FIRST Bank | Cred Personal Loan | ₹1,16,629 | ₹11,280 | Monthly |
| ICICI Bank | Auto Loan (Personal) | ₹2,52,847 | ₹7,615 | Monthly |
| Kotak Mahindra Bank | Personal Loan on Credit Card | ₹89,966 | ₹5,076.18 | Monthly |
| Kotak Mahindra Bank | MicroFinance Personal Loan | ₹95,730 | ₹3,473 | Monthly |
| ICICI Bank | Gold Loan | ₹2,20,000 | — | Bullet (closes 18 Sep 2026) |
| ICICI Bank | Car Loan | ₹1,52,811 | ₹6,123 | Monthly |
| RBL Bank | Instant Cash Loan | ₹45,719 | ₹5,358.75 | Monthly |
| RBL Bank | Corporate Credit Card | ₹2,73,272 | ₹11,428 | Monthly |
| Local Chit Fund (Chitty) | Quarterly Installment | ₹3,50,000 | ₹16,666 | Quarterly (15 Dec / Mar / Jun cycle) |

**Note:** the RBL Corporate Credit Card row had no last-payment date in your
original file, so it's anchored to today's date rather than its opening
date — edit `anchor_date` in the CSV (or the loan's entry in-app) if you know
the actual last payment date, for a more precise schedule.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Push to GitHub

```bash
git init
git add .
git commit -m "Loanwise loan tracker"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

## Deploy on Streamlit Community Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. Click **"New app"**, pick your repo/branch, set the main file to `app.py`.
3. Click **Deploy**.

> **Data persistence:** Streamlit Community Cloud's filesystem is ephemeral
> — the SQLite file resets on redeploys/restarts. Fine for personal use;
> for permanent storage, swap the SQLite calls for a hosted DB.

## Project structure

```
.
├── app.py             # Streamlit app (Dashboard, Loans, Month Lookup, Interest Calculator)
├── my_loans.csv        # Your 9 real loans — upload via the Loans page
├── requirements.txt    # Python dependencies
└── README.md
```

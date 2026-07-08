# Loanwise – Loan / EMI Tracker

A Streamlit clone of the Loanwise loan-portfolio dashboard, with an added
**EMI payment tracking** feature: mark individual EMIs as paid, undo a
mark, or catch up in one click on every EMI that's fallen due up to today.

## Features

- **Dashboard** – total outstanding, active loan count, total monthly EMI,
  upcoming EMIs (with an inline "Mark Paid" button), loan distribution
  pie chart by loan type.
- **Loans** – add a loan (name, type, lender, principal, rate, tenure,
  start date, notes) with EMI auto-calculated from principal/rate/tenure.
  Each loan card shows outstanding balance, EMI, repaid %, and a full
  month-by-month payment schedule you can check off.
- **Mark EMIs Paid / Undo** – record payments as they happen, or use
  **"Mark all due EMIs as Paid (till today)"** to catch up on past months
  in one click. Each entry is tagged on-time / late based on due date.
- **EMI Calculator** – standalone calculator with amortization schedule.

Data is stored locally in a SQLite file (`loanwise.db`) that's created
automatically on first run.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open the URL Streamlit prints (usually `http://localhost:8501`).

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
2. Click **"New app"**, pick your repo/branch, and set the main file to `app.py`.
3. Click **Deploy**. Your app will be live at a `*.streamlit.app` URL within a minute.

> **Note on data persistence:** Streamlit Community Cloud's filesystem is
> ephemeral — the SQLite file resets whenever the app restarts/redeploys
> (e.g. after inactivity or a new push). Fine for personal/demo use; for
> permanent storage, swap the SQLite calls for a hosted DB (e.g. Supabase,
> Turso, or a Google Sheet via `gspread`) — the CRUD functions in `app.py`
> are isolated at the top of the file, so this is a contained change.

## Project structure

```
.
├── app.py             # Streamlit app (Dashboard, Loans, EMI Calculator)
├── requirements.txt    # Python dependencies
└── README.md
```

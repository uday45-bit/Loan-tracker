"""
Loanwise – Loan / EMI Tracker
Tracks real debt obligations (bank loans, credit cards, chit funds, gold loans)
using their ACTUAL current outstanding balance as the anchor point, then
projects forward with proper amortization to answer:
  - What's the outstanding principal today, combined across all loans?
  - What's the total amount payable (incl. interest) till every loan closes?
  - How much is due in a specific month?
  - How much interest accrues between any two dates?

Handles three payment frequencies:
  - Monthly (most loans/cards)
  - Quarterly (e.g. chit funds)
  - Bullet (e.g. gold loans — interest-only, single principal+interest payment at closing)
"""

import math
import sqlite3
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

import pandas as pd
import plotly.express as px
import streamlit as st

DB_PATH = "loanwise.db"

FREQ_LABELS = {1: "Monthly", 3: "Quarterly", 12: "Annual", -1: "Bullet (single payment at closing)"}
FREQ_OPTIONS = list(FREQ_LABELS.keys())


def today() -> date:
    return date.today()


# --------------------------------------------------------------------------- #
# Date parsing (day-first / Indian convention)
# --------------------------------------------------------------------------- #

def parse_date_str(value) -> date:
    """
    Parse a date using DAY-FIRST convention (Indian format), robust to
    '-', '/', or '.' separators, e.g. '12.05.2026' -> 12 May 2026 (not Dec 5).
    ISO (YYYY-MM-DD) is tried first since it's unambiguous.
    """
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    s = str(value).strip()
    normalized = s.replace(".", "-").replace("/", "-")
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date()
    except ValueError:
        pass
    for fmt in ("%d-%m-%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
    return pd.to_datetime(s, dayfirst=True).date()


def fmt_date(value) -> str:
    return parse_date_str(value).strftime("%d %b %Y")


def inr(x) -> str:
    return f"₹{x:,.0f}"


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lender TEXT NOT NULL,
            account_type TEXT,
            loan_amount REAL NOT NULL,
            opened_date TEXT NOT NULL,
            tenure_months INTEGER NOT NULL,
            tenure_remaining INTEGER NOT NULL,
            closing_date TEXT NOT NULL,
            outstanding_balance REAL NOT NULL,
            emi REAL NOT NULL,
            rate REAL NOT NULL,
            frequency INTEGER NOT NULL DEFAULT 1,
            anchor_date TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def add_loan(lender, account_type, loan_amount, opened_date, tenure_months, tenure_remaining,
             closing_date, outstanding_balance, emi, rate, frequency, anchor_date, notes=""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO loans (lender, account_type, loan_amount, opened_date, tenure_months, tenure_remaining, "
        "closing_date, outstanding_balance, emi, rate, frequency, anchor_date, notes, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (lender, account_type, loan_amount, opened_date.isoformat(), int(tenure_months), int(tenure_remaining),
         closing_date.isoformat(), outstanding_balance, emi, rate, int(frequency), anchor_date.isoformat(),
         notes, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def update_loan_progress(loan_id, new_anchor_date, new_outstanding, new_tenure_remaining):
    conn = get_conn()
    conn.execute(
        "UPDATE loans SET anchor_date=?, outstanding_balance=?, tenure_remaining=? WHERE id=?",
        (new_anchor_date.isoformat(), new_outstanding, int(new_tenure_remaining), int(loan_id)),
    )
    conn.commit()
    conn.close()


def delete_loan(loan_id):
    conn = get_conn()
    conn.execute("DELETE FROM loans WHERE id=?", (int(loan_id),))
    conn.commit()
    conn.close()


def get_loans() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM loans ORDER BY opened_date ASC", conn)
    conn.close()
    return df


# --------------------------------------------------------------------------- #
# Amortization schedule engine
# --------------------------------------------------------------------------- #

def build_schedule(loan_row) -> list:
    """
    Projects the remaining payment schedule forward from the loan's anchor point
    (last known payment date + outstanding balance) to closing.
    Returns list of dicts: due (date), principal, interest, total.
    """
    freq = int(loan_row["frequency"])
    rate = float(loan_row["rate"]) / 100  # stored as percent, e.g. 12.5
    anchor_date = parse_date_str(loan_row["anchor_date"])
    balance = float(loan_row["outstanding_balance"])
    closing = parse_date_str(loan_row["closing_date"])
    emi = float(loan_row["emi"])

    schedule = []

    if freq == -1:  # Bullet: single interest+principal payment at closing
        days = max((closing - anchor_date).days, 0)
        interest = balance * rate * (days / 365)
        schedule.append({
            "due": closing, "principal": round(balance, 2),
            "interest": round(interest, 2), "total": round(balance + interest, 2),
        })
        return schedule

    freq_months = freq
    periodic_rate = rate * (freq_months / 12)
    tenure_remaining = int(loan_row["tenure_remaining"])
    n_periods = max(1, math.ceil(tenure_remaining / freq_months))

    bal = balance
    for i in range(1, n_periods + 1):
        due = anchor_date + relativedelta(months=freq_months * i)
        interest = bal * periodic_rate
        principal = emi - interest
        if principal > bal or principal < 0:
            principal = bal
        bal = max(bal - principal, 0)
        if i == n_periods and bal > 0.5:
            # Balloon: settle any residual at the final scheduled installment
            principal += bal
            bal = 0
        schedule.append({
            "due": due, "principal": round(principal, 2),
            "interest": round(interest, 2), "total": round(principal + interest, 2),
        })
        if bal <= 0.5:
            break
    return schedule


def loan_metrics(loan_row, as_of: date = None) -> dict:
    as_of = as_of or today()
    schedule = build_schedule(loan_row)
    outstanding = float(loan_row["outstanding_balance"])
    for s in schedule:
        if s["due"] <= as_of:
            outstanding -= s["principal"]
    outstanding = max(round(outstanding, 2), 0)
    total_payable = round(sum(s["total"] for s in schedule), 2)
    total_interest = round(sum(s["interest"] for s in schedule), 2)
    upcoming = [s for s in schedule if s["due"] > as_of]
    next_due = upcoming[0] if upcoming else None
    monthly_equivalent = float(loan_row["emi"]) / (int(loan_row["frequency"]) if loan_row["frequency"] != -1 else 12)
    return dict(
        schedule=schedule, outstanding=outstanding, total_payable=total_payable,
        total_interest=total_interest, next_due=next_due, monthly_equivalent=monthly_equivalent,
    )


def month_due(loans_df: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    rows = []
    for _, loan in loans_df.iterrows():
        for s in build_schedule(loan):
            if s["due"].year == year and s["due"].month == month:
                rows.append({
                    "Lender": loan["lender"], "Account": loan["account_type"],
                    "Due Date": fmt_date(s["due"]), "Principal": s["principal"],
                    "Interest": s["interest"], "Total Due": s["total"],
                })
    return pd.DataFrame(rows)


def interest_in_range(loans_df: pd.DataFrame, from_date: date, to_date: date) -> pd.DataFrame:
    rows = []
    for _, loan in loans_df.iterrows():
        loan_interest = 0.0
        count = 0
        for s in build_schedule(loan):
            if from_date <= s["due"] <= to_date:
                loan_interest += s["interest"]
                count += 1
        if loan_interest > 0:
            rows.append({
                "Lender": loan["lender"], "Account": loan["account_type"],
                "Installments in Range": count, "Interest Payable": round(loan_interest, 2),
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #

def page_dashboard():
    st.title("Your Loan Portfolio")
    st.caption("Real outstanding balances, projected forward with amortization.")

    loans = get_loans()
    if loans.empty:
        st.info("No loans yet — add your first one from the **Loans** page.")
        return

    total_outstanding = total_payable = total_monthly = 0
    dist_rows, upcoming_rows = [], []

    for _, loan in loans.iterrows():
        m = loan_metrics(loan)
        total_outstanding += m["outstanding"]
        total_payable += m["total_payable"]
        total_monthly += m["monthly_equivalent"]
        dist_rows.append({"Lender": loan["lender"], "Outstanding": m["outstanding"]})
        if m["next_due"] is not None:
            upcoming_rows.append({
                "Loan": f"{loan['lender']} — {loan['account_type']}",
                "Due": m["next_due"]["due"], "Amount": m["next_due"]["total"],
            })

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Principal Outstanding (Today)", inr(total_outstanding))
    c2.metric("Total Payable (incl. interest, to closing)", inr(total_payable))
    c3.metric("Active Loans", len(loans))
    c4.metric("Avg. Monthly Obligation", inr(total_monthly))

    st.subheader("Upcoming Installments")
    if upcoming_rows:
        up_df = pd.DataFrame(upcoming_rows).sort_values("Due")
        up_df["Due"] = up_df["Due"].apply(fmt_date)
        up_df["Amount"] = up_df["Amount"].apply(inr)
        st.dataframe(up_df, use_container_width=True, hide_index=True)
    else:
        st.write("No upcoming installments.")

    st.subheader("Outstanding by Lender")
    dist_df = pd.DataFrame(dist_rows).groupby("Lender", as_index=False).sum()
    fig = px.pie(dist_df, names="Lender", values="Outstanding", hole=0.45)
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10))
    st.plotly_chart(fig, use_container_width=True)


def page_loans():
    st.title("All Loans")
    st.caption("Add, edit and monitor your loans — sorted by opening date.")

    with st.expander("➕ Add New Loan"):
        with st.form("add_loan_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            lender = c1.text_input("Lender / Institution *", placeholder="e.g. HDFC Bank")
            account_type = c2.text_input("Account Type *", placeholder="e.g. Personal Loan")
            c3, c4 = st.columns(2)
            loan_amount = c3.number_input("Original Loan Amount (₹) *", min_value=0.0, step=1000.0, value=100000.0)
            rate = c4.number_input("Interest Rate (% p.a.) *", min_value=0.0, step=0.01, value=12.0)
            c5, c6 = st.columns(2)
            opened_date = c5.date_input("Opened Date *", value=date.today())
            closing_date = c6.date_input("Closing Date *", value=date.today() + relativedelta(years=5))
            c7, c8 = st.columns(2)
            tenure_months = c7.number_input("Total Tenure (months) *", min_value=1, step=1, value=60)
            tenure_remaining = c8.number_input("Tenure Remaining (months) *", min_value=0, step=1, value=60)
            c9, c10 = st.columns(2)
            outstanding_balance = c9.number_input("Current Outstanding Balance (₹) *", min_value=0.0, step=1000.0, value=100000.0)
            emi = c10.number_input("EMI / Installment (₹) *", min_value=0.0, step=100.0, value=3000.0)
            c11, c12 = st.columns(2)
            frequency = c11.selectbox("Payment Frequency *", FREQ_OPTIONS, format_func=lambda x: FREQ_LABELS[x])
            anchor_date = c12.date_input("As-of Date for Outstanding Balance *", value=date.today(),
                                          help="The date the outstanding balance above is accurate as of — usually your last payment date.")
            notes = st.text_area("Notes", placeholder="Optional notes")
            submitted = st.form_submit_button("Add Loan", use_container_width=True)
            if submitted:
                if not lender.strip():
                    st.error("Lender is required.")
                else:
                    add_loan(lender.strip(), account_type.strip(), loan_amount, opened_date, int(tenure_months),
                              int(tenure_remaining), closing_date, outstanding_balance, emi, rate, frequency,
                              anchor_date, notes.strip())
                    st.success(f"'{lender}' added.")
                    st.rerun()

    with st.expander("📤 Bulk Upload Loans (CSV)"):
        st.caption(
            "Columns: **lender, account_type, loan_amount, opened_date, tenure_months, tenure_remaining, "
            "closing_date, outstanding_balance, emi, rate, frequency, anchor_date, notes**. "
            "Dates accept DD.MM.YYYY / DD-MM-YYYY / DD/MM/YYYY (day-first) or YYYY-MM-DD. "
            "`rate` as a plain percent number (e.g. 12.5, not 0.125). "
            "`frequency`: 1=Monthly, 3=Quarterly, 12=Annual, -1=Bullet (single payment at closing)."
        )
        template_df = pd.DataFrame([{
            "lender": "HDFC Bank", "account_type": "Personal Loan", "loan_amount": 500000,
            "opened_date": "01-01-2025", "tenure_months": 60, "tenure_remaining": 48,
            "closing_date": "01-01-2030", "outstanding_balance": 420000, "emi": 11000,
            "rate": 9.5, "frequency": 1, "anchor_date": "01-07-2026", "notes": "",
        }])
        st.download_button("Download CSV template", template_df.to_csv(index=False).encode(),
                            file_name="loanwise_template.csv", mime="text/csv")

        uploaded = st.file_uploader("Choose CSV file", type=["csv"], key="bulk_upload")
        if uploaded is not None:
            try:
                df = pd.read_csv(uploaded)
                required = {"lender", "loan_amount", "opened_date", "tenure_months", "tenure_remaining",
                            "closing_date", "outstanding_balance", "emi", "rate", "frequency", "anchor_date"}
                missing = required - set(df.columns)
                if missing:
                    st.error(f"Missing required column(s): {', '.join(sorted(missing))}")
                else:
                    st.dataframe(df, use_container_width=True, hide_index=True)
                    if st.button("Import all rows as loans", type="primary"):
                        added, errors = 0, []
                        for i, row in df.iterrows():
                            try:
                                add_loan(
                                    str(row["lender"]).strip(), str(row.get("account_type", "") or "").strip(),
                                    float(row["loan_amount"]), parse_date_str(row["opened_date"]),
                                    int(row["tenure_months"]), int(row["tenure_remaining"]),
                                    parse_date_str(row["closing_date"]), float(row["outstanding_balance"]),
                                    float(row["emi"]), float(row["rate"]), int(row["frequency"]),
                                    parse_date_str(row["anchor_date"]), str(row.get("notes", "") or "").strip(),
                                )
                                added += 1
                            except Exception as e:
                                errors.append(f"Row {i + 1}: {e}")
                        if added:
                            st.success(f"Imported {added} loan(s).")
                        if errors:
                            st.warning("Some rows failed:\n" + "\n".join(errors))
                        if added:
                            st.rerun()
            except Exception as e:
                st.error(f"Could not read CSV: {e}")

    loans = get_loans()
    if loans.empty:
        st.info("No loans yet.")
        return

    for _, loan in loans.iterrows():
        m = loan_metrics(loan)
        with st.container(border=True):
            top = st.columns([3, 2, 2, 2, 1])
            top[0].markdown(f"**{loan['lender']}**  \n:gray[{loan['account_type'] or '—'}]")
            top[1].metric("Outstanding Today", inr(m["outstanding"]))
            top[2].metric("Total Payable (to close)", inr(m["total_payable"]))
            freq_short = FREQ_LABELS[int(loan['frequency'])].split()[0]
            top[3].metric("EMI", f"{inr(loan['emi'])} / {freq_short}")
            if top[4].button("🗑️", key=f"del_{loan['id']}", help="Delete loan"):
                delete_loan(loan["id"])
                st.rerun()

            st.caption(
                f"{loan['rate']}% p.a. · Opened {fmt_date(loan['opened_date'])} · "
                f"Closes {fmt_date(loan['closing_date'])} · {loan['tenure_remaining']} months remaining · "
                f"Balance as of {fmt_date(loan['anchor_date'])}"
            )

            bcol1, bcol2 = st.columns(2)
            if m["next_due"] is not None:
                nd = m["next_due"]
                if bcol1.button(f"✅ Mark {fmt_date(nd['due'])} installment paid ({inr(nd['total'])})", key=f"pay_{loan['id']}"):
                    new_bal = m["outstanding"] - nd["principal"]
                    step = int(loan["frequency"]) if loan["frequency"] != -1 else int(loan["tenure_remaining"])
                    new_tenure_remaining = max(int(loan["tenure_remaining"]) - step, 0)
                    update_loan_progress(loan["id"], nd["due"], max(new_bal, 0), new_tenure_remaining)
                    st.rerun()

            with bcol2.expander("Full Schedule"):
                sched_df = pd.DataFrame(m["schedule"])
                sched_df["Due Date"] = sched_df["due"].apply(fmt_date)
                sched_df["Principal"] = sched_df["principal"].apply(inr)
                sched_df["Interest"] = sched_df["interest"].apply(inr)
                sched_df["Total"] = sched_df["total"].apply(inr)
                st.dataframe(sched_df[["Due Date", "Principal", "Interest", "Total"]],
                             use_container_width=True, hide_index=True)


def page_month_lookup():
    st.title("Month Lookup")
    st.caption("See exactly what's due across all loans in a given month.")

    loans = get_loans()
    if loans.empty:
        st.info("No loans yet.")
        return

    c1, c2 = st.columns(2)
    year = c1.number_input("Year", min_value=2020, max_value=2050, value=today().year, step=1)
    month = c2.selectbox("Month", list(range(1, 13)), index=today().month - 1,
                          format_func=lambda m: date(2000, m, 1).strftime("%B"))

    df = month_due(loans, int(year), int(month))
    if df.empty:
        st.write("No installments due this month.")
        return

    display_df = df.copy()
    for col in ["Principal", "Interest", "Total Due"]:
        display_df[col] = display_df[col].apply(inr)
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Due This Month", inr(df["Total Due"].sum()))
    c2.metric("Principal Portion", inr(df["Principal"].sum()))
    c3.metric("Interest Portion", inr(df["Interest"].sum()))


def page_interest_calculator():
    st.title("Interest Payable — Date Range")
    st.caption("Total interest accruing across all loans between two dates.")

    loans = get_loans()
    if loans.empty:
        st.info("No loans yet.")
        return

    c1, c2 = st.columns(2)
    from_date = c1.date_input("From", value=today())
    to_date = c2.date_input("To", value=today() + relativedelta(years=1))

    if from_date > to_date:
        st.error("'From' date must be before 'To' date.")
        return

    df = interest_in_range(loans, from_date, to_date)
    if df.empty:
        st.write("No interest payments fall in this range.")
        return

    display_df = df.copy()
    display_df["Interest Payable"] = display_df["Interest Payable"].apply(inr)
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    st.metric(f"Total Interest Payable ({fmt_date(from_date)} → {fmt_date(to_date)})", inr(df["Interest Payable"].sum()))


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    st.set_page_config(page_title="Loanwise", page_icon="💰", layout="wide")
    init_db()

    st.sidebar.title("💰 Loanwise")
    st.sidebar.caption("Loan Tracker")
    page = st.sidebar.radio(
        "Navigate",
        ["📊 Dashboard", "📁 Loans", "📅 Month Lookup", "📈 Interest Calculator"],
        label_visibility="collapsed",
    )

    if page == "📊 Dashboard":
        page_dashboard()
    elif page == "📁 Loans":
        page_loans()
    elif page == "📅 Month Lookup":
        page_month_lookup()
    else:
        page_interest_calculator()


if __name__ == "__main__":
    main()

"""
Loanwise – Loan / EMI Tracker
A Streamlit clone of the Loanwise loan-portfolio dashboard, extended with
EMI payment tracking (mark-as-paid, catch-up-till-today, on-time/late tags).
"""

import sqlite3
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

import pandas as pd
import plotly.express as px
import streamlit as st

DB_PATH = "loanwise.db"

LOAN_TYPES = ["Personal Loan", "Car Loan", "Credit Card", "Home Loan", "Education Loan", "Gold Loan", "Chitty", "Other"]

# --------------------------------------------------------------------------- #
# Database helpers
# --------------------------------------------------------------------------- #

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            loan_type TEXT NOT NULL,
            lender TEXT,
            principal REAL NOT NULL,
            rate REAL NOT NULL,
            tenure INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            notes TEXT,
            emi_override REAL,
            created_at TEXT NOT NULL
        )
    """)
    # Backfill for DBs created before emi_override existed
    cols = [r[1] for r in conn.execute("PRAGMA table_info(loans)").fetchall()]
    if "emi_override" not in cols:
        conn.execute("ALTER TABLE loans ADD COLUMN emi_override REAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loan_id INTEGER NOT NULL,
            month_index INTEGER NOT NULL,
            due_date TEXT NOT NULL,
            amount REAL NOT NULL,
            paid INTEGER NOT NULL DEFAULT 0,
            paid_date TEXT,
            FOREIGN KEY (loan_id) REFERENCES loans(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- #
# EMI math
# --------------------------------------------------------------------------- #

def calculate_emi(principal: float, annual_rate: float, tenure_months: int) -> float:
    if tenure_months <= 0:
        return 0.0
    r = annual_rate / 12 / 100
    if r == 0:
        return principal / tenure_months
    emi = principal * r * (1 + r) ** tenure_months / ((1 + r) ** tenure_months - 1)
    return round(emi, 2)


def remaining_balance(principal: float, annual_rate: float, tenure_months: int, payments_made: int) -> float:
    """Outstanding principal after `payments_made` EMIs on a reducing-balance loan."""
    r = annual_rate / 12 / 100
    n = tenure_months
    p = min(payments_made, n)
    if r == 0:
        return max(principal - principal / n * p, 0)
    balance = principal * ((1 + r) ** n - (1 + r) ** p) / ((1 + r) ** n - 1)
    return max(round(balance, 2), 0)


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #

def add_loan(name, loan_type, lender, principal, rate, tenure, start_date, notes, emi_override=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO loans (name, loan_type, lender, principal, rate, tenure, start_date, notes, emi_override, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (name, loan_type, lender, principal, rate, tenure, start_date.isoformat(), notes, emi_override, datetime.now().isoformat()),
    )
    loan_id = cur.lastrowid

    emi = emi_override if emi_override else calculate_emi(principal, rate, tenure)
    rows = []
    for i in range(1, tenure + 1):
        due = start_date + relativedelta(months=i - 1)
        rows.append((loan_id, i, due.isoformat(), emi, 0, None))
    cur.executemany(
        "INSERT INTO payments (loan_id, month_index, due_date, amount, paid, paid_date) VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def delete_loan(loan_id):
    loan_id = int(loan_id)
    conn = get_conn()
    conn.execute("DELETE FROM payments WHERE loan_id=?", (loan_id,))
    conn.execute("DELETE FROM loans WHERE id=?", (loan_id,))
    conn.commit()
    conn.close()


def get_loans() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM loans ORDER BY created_at DESC", conn)
    conn.close()
    return df


def get_payments(loan_id) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT * FROM payments WHERE loan_id=? ORDER BY month_index", conn, params=(int(loan_id),)
    )
    conn.close()
    return df


def mark_paid(payment_id, paid_date: date):
    conn = get_conn()
    conn.execute("UPDATE payments SET paid=1, paid_date=? WHERE id=?", (paid_date.isoformat(), int(payment_id)))
    conn.commit()
    conn.close()


def unmark_paid(payment_id):
    conn = get_conn()
    conn.execute("UPDATE payments SET paid=0, paid_date=NULL WHERE id=?", (int(payment_id),))
    conn.commit()
    conn.close()


def mark_all_due_till_today(loan_id, today: date):
    """Catch-up button: marks every unpaid EMI whose due date has passed as paid, dated on its due date (on-time)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, due_date FROM payments WHERE loan_id=? AND paid=0 AND due_date<=?",
        (int(loan_id), today.isoformat()),
    )
    rows = cur.fetchall()
    for pid, due_date in rows:
        cur.execute("UPDATE payments SET paid=1, paid_date=? WHERE id=?", (due_date, pid))
    conn.commit()
    conn.close()
    return len(rows)


# --------------------------------------------------------------------------- #
# UI helpers
# --------------------------------------------------------------------------- #

def loan_summary(loan_row) -> dict:
    payments = get_payments(loan_row["id"])
    paid_count = int(payments["paid"].sum())
    tenure = int(loan_row["tenure"])
    emi = payments["amount"].iloc[0] if not payments.empty else calculate_emi(loan_row["principal"], loan_row["rate"], tenure)
    outstanding = remaining_balance(loan_row["principal"], loan_row["rate"], tenure, paid_count)
    repaid_pct = round(paid_count / tenure * 100, 1) if tenure else 0
    unpaid = payments[payments["paid"] == 0]
    next_due = unpaid.iloc[0] if not unpaid.empty else None
    return dict(
        emi=emi, outstanding=outstanding, repaid_pct=repaid_pct,
        paid_count=paid_count, next_due=next_due, payments=payments,
    )


def inr(x) -> str:
    return f"₹{x:,.0f}"


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #

def page_dashboard():
    st.title("Your Loan Portfolio")
    st.caption("Track outstanding balances, EMIs, and upcoming payments in one place.")

    loans = get_loans()
    if loans.empty:
        st.info("No loans yet — add your first one from the **Loans** page.")
        return

    total_outstanding = 0
    total_emi = 0
    dist_rows = []
    upcoming_rows = []

    for _, loan in loans.iterrows():
        s = loan_summary(loan)
        total_outstanding += s["outstanding"]
        total_emi += s["emi"]
        dist_rows.append({"Type": loan["loan_type"], "Outstanding": s["outstanding"]})
        if s["next_due"] is not None:
            upcoming_rows.append({
                "Loan": loan["name"], "Lender": loan["lender"] or "—",
                "Due": s["next_due"]["due_date"], "Amount": s["emi"],
                "loan_id": loan["id"], "payment_id": s["next_due"]["id"],
            })

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Outstanding", inr(total_outstanding))
    c2.metric("Active Loans", len(loans))
    c3.metric("Total Monthly EMI", inr(total_emi))

    st.subheader("Upcoming EMIs")
    if upcoming_rows:
        upcoming_df = pd.DataFrame(upcoming_rows).sort_values("Due")
        for _, row in upcoming_df.iterrows():
            cols = st.columns([3, 2, 2, 2, 2])
            cols[0].write(f"**{row['Loan']}**")
            cols[1].write(row["Lender"])
            cols[2].write(row["Due"])
            cols[3].write(inr(row["Amount"]))
            if cols[4].button("Mark Paid", key=f"dash_pay_{row['payment_id']}"):
                mark_paid(row["payment_id"], date.today())
                st.rerun()
    else:
        st.write("All EMIs paid up. 🎉")

    st.subheader("Loan Distribution")
    dist_df = pd.DataFrame(dist_rows).groupby("Type", as_index=False).sum()
    fig = px.pie(dist_df, names="Type", values="Outstanding", hole=0.45)
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10))
    st.plotly_chart(fig, use_container_width=True)


def page_loans():
    st.title("All Loans")
    st.caption("Add, edit and monitor your loans.")

    with st.expander("➕ Add New Loan"):
        with st.form("add_loan_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            name = c1.text_input("Loan Name *", placeholder="e.g. Honda City Loan")
            loan_type = c2.selectbox("Loan Type *", LOAN_TYPES)
            lender = st.text_input("Lender / Bank", placeholder="e.g. HDFC Bank")
            c3, c4 = st.columns(2)
            principal = c3.number_input("Principal / Current Outstanding (₹) *", min_value=0.0, step=1000.0, value=500000.0)
            rate = c4.number_input("Interest Rate (% p.a.) *", min_value=0.0, step=0.1, value=9.5)
            c5, c6 = st.columns(2)
            tenure = c5.number_input("Tenure (months) *", min_value=1, step=1, value=60)
            start = c6.date_input("Start Date *", value=date.today())
            emi_override = st.number_input(
                "Known EMI (₹) — optional, overrides auto-calculated EMI", min_value=0.0, step=100.0, value=0.0,
                help="Leave at 0 to auto-calculate EMI from principal, rate & tenure. "
                     "Set this if you already know your actual EMI (e.g. mid-way through a loan).",
            )
            notes = st.text_area("Notes", placeholder="Optional notes")
            submitted = st.form_submit_button("Add Loan", use_container_width=True)
            if submitted:
                if not name.strip():
                    st.error("Loan name is required.")
                else:
                    add_loan(
                        name.strip(), loan_type, lender.strip(), principal, rate, int(tenure), start, notes.strip(),
                        emi_override=emi_override if emi_override > 0 else None,
                    )
                    st.success(f"'{name}' added.")
                    st.rerun()

    with st.expander("📤 Bulk Upload Loans (CSV)"):
        st.caption(
            "Upload a CSV with columns: **name, loan_type, lender, principal, rate, tenure, start_date, notes, emi_override**. "
            "`start_date` must be YYYY-MM-DD. `loan_type` should be one of: " + ", ".join(LOAN_TYPES) + ". "
            "`emi_override` is optional — leave blank/0 to auto-calculate EMI; set it to use a known real EMI instead "
            "(useful when `principal` is actually your *current outstanding balance* mid-way through a loan)."
        )
        template_df = pd.DataFrame([{
            "name": "Honda City Loan", "loan_type": "Car Loan", "lender": "HDFC Bank",
            "principal": 500000, "rate": 9.5, "tenure": 60, "start_date": "2026-01-01", "notes": "", "emi_override": "",
        }])
        st.download_button(
            "Download CSV template", template_df.to_csv(index=False).encode(),
            file_name="loanwise_template.csv", mime="text/csv",
        )

        uploaded = st.file_uploader("Choose CSV file", type=["csv"], key="bulk_upload")
        if uploaded is not None:
            try:
                df = pd.read_csv(uploaded)
                required = {"name", "loan_type", "principal", "rate", "tenure", "start_date"}
                missing = required - set(df.columns)
                if missing:
                    st.error(f"Missing required column(s): {', '.join(sorted(missing))}")
                else:
                    st.dataframe(df, use_container_width=True, hide_index=True)
                    if st.button("Import all rows as loans", type="primary"):
                        added, errors = 0, []
                        for i, row in df.iterrows():
                            try:
                                name = str(row["name"]).strip()
                                loan_type = str(row["loan_type"]).strip()
                                if loan_type not in LOAN_TYPES:
                                    loan_type = "Other"
                                lender = str(row.get("lender", "") or "").strip()
                                principal = float(row["principal"])
                                rate = float(row["rate"])
                                tenure = int(row["tenure"])
                                start = pd.to_datetime(row["start_date"]).date()
                                notes = str(row.get("notes", "") or "").strip()
                                emi_val = row.get("emi_override", None)
                                emi_override = float(emi_val) if pd.notna(emi_val) and str(emi_val).strip() not in ("", "0") else None
                                if not name:
                                    raise ValueError("empty name")
                                add_loan(name, loan_type, lender, principal, rate, tenure, start, notes, emi_override=emi_override)
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
        s = loan_summary(loan)
        with st.container(border=True):
            top = st.columns([4, 2, 2, 1])
            top[0].markdown(f"**{loan['name']}**  \n:gray[{loan['loan_type']} · {loan['lender'] or '—'}]")
            top[1].metric("Outstanding", inr(s["outstanding"]))
            top[2].metric("Monthly EMI", inr(s["emi"]))
            if top[3].button("🗑️", key=f"del_{loan['id']}", help="Delete loan"):
                delete_loan(loan["id"])
                st.rerun()

            st.progress(min(s["repaid_pct"] / 100, 1.0), text=f"Repaid {s['repaid_pct']}% ({s['paid_count']}/{loan['tenure']} EMIs)")
            st.caption(f"{loan['rate']}% · {loan['tenure']}m · from {loan['start_date']}")

            bcol1, bcol2 = st.columns(2)
            if bcol1.button("✅ Mark all due EMIs as Paid (till today)", key=f"catchup_{loan['id']}"):
                n = mark_all_due_till_today(loan["id"], date.today())
                st.success(f"Marked {n} EMI(s) as paid.")
                st.rerun()

            with bcol2.expander("Details"):
                payments = s["payments"].copy()
                payments["Status"] = payments.apply(
                    lambda r: "✅ Paid" + (" (on time)" if r["paid"] and r["paid_date"] <= r["due_date"] else " (late)" if r["paid"] else "")
                    if r["paid"] else ("⏳ Due" if r["due_date"] > date.today().isoformat() else "🔴 Overdue"),
                    axis=1,
                )
                for _, p in payments.iterrows():
                    pc = st.columns([1, 2, 2, 2, 2])
                    pc[0].write(f"#{p['month_index']}")
                    pc[1].write(p["due_date"])
                    pc[2].write(inr(p["amount"]))
                    pc[3].write(p["Status"])
                    if p["paid"]:
                        if pc[4].button("Undo", key=f"undo_{p['id']}"):
                            unmark_paid(p["id"])
                            st.rerun()
                    else:
                        if pc[4].button("Mark Paid", key=f"pay_{p['id']}"):
                            mark_paid(p["id"], date.today())
                            st.rerun()


def page_calculator():
    st.title("EMI Calculator")
    c1, c2, c3 = st.columns(3)
    principal = c1.number_input("Principal Amount (₹)", min_value=0.0, step=1000.0, value=500000.0)
    rate = c2.number_input("Interest Rate (% p.a.)", min_value=0.0, step=0.1, value=9.5)
    tenure = c3.number_input("Tenure (months)", min_value=1, step=1, value=60)

    emi = calculate_emi(principal, rate, int(tenure))
    total_payment = emi * tenure
    total_interest = total_payment - principal

    m1, m2, m3 = st.columns(3)
    m1.metric("Monthly EMI", inr(emi))
    m2.metric("Total Interest", inr(total_interest))
    m3.metric("Total Payment", inr(total_payment))

    with st.expander("Amortization Schedule"):
        rows = []
        balance = principal
        r = rate / 12 / 100
        for i in range(1, int(tenure) + 1):
            interest = balance * r
            principal_component = emi - interest
            balance = max(balance - principal_component, 0)
            rows.append({"Month": i, "EMI": round(emi, 2), "Principal": round(principal_component, 2),
                         "Interest": round(interest, 2), "Balance": round(balance, 2)})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    st.set_page_config(page_title="Loanwise", page_icon="💰", layout="wide")
    init_db()

    st.sidebar.title("💰 Loanwise")
    st.sidebar.caption("Loan Tracker")
    page = st.sidebar.radio("Navigate", ["📊 Dashboard", "📁 Loans", "🧮 EMI Calculator"], label_visibility="collapsed")

    if page == "📊 Dashboard":
        page_dashboard()
    elif page == "📁 Loans":
        page_loans()
    else:
        page_calculator()


if __name__ == "__main__":
    main()

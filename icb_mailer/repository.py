"""All SQL used by the service.

The statements are the ones from the legacy module (same tables, columns,
ordering and formatting) so the emails and PDFs carry identical data. The only
change is that account numbers are passed as bind variables instead of being
glued into the SQL text.

Rows are returned as tuples because the HTML templates address columns by
position (profiles[1], transaction[3] ...), exactly as before.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence

import oracledb

Row = tuple[Any, ...]

# ---------------------------------------------------------------- portfolio --

SQL_PORTFOLIO_PROFILES = """
    select i.accnt_no,
           ac_name1 || decode(ac_name2, null, '', ' (JOINT)'),
           to_char(sysdate, 'dd/mm/rrrr'),
           email_no,
           initcap(b.br_name)
      from invest.branch b, invest.inv_profile i, invest.accnt_mobile_info a
     where i.br_cd = b.br_cd
       and i.accnt_no = a.accnt_no
       and email_no is not null
     order by i.accnt_no"""

SQL_PORTFOLIO_HOLDINGS = """
    select COMPANY_NAME, nvl(TYPE, ' '), NO_OF_SHARES, AVG_COST_PRICE, MARKET_PRICE,
           PURCHASE_COST, MARKET_VALUE, DIFFERENCE
      from invest.EMAIL_PV_STAT_DATA
     where accnt_no = :acc
     order by company_name"""

# Raw numbers (HTML body) and the to_char() text (PDF) in one round trip.
SQL_PORTFOLIO_FOOTER = """
    select MAX_LOAN_LIMIT, CURRENT_BALANCE, PORTFOLIO_VALUE_MARKET, PORTFOLIO_VALUE_CONTROL,
           PURCHASE_POWER, MARGIN, SORTFALL,
           to_char(MAX_LOAN_LIMIT, '999,999,990.90'),
           to_char(CURRENT_BALANCE, '999,999,990.90'),
           to_char(PORTFOLIO_VALUE_MARKET, '999,999,990.90'),
           to_char(PORTFOLIO_VALUE_CONTROL, '999,999,990.90'),
           to_char(PURCHASE_POWER, '999,999,990.90'),
           to_char(MARGIN, '999,999,990.90'),
           to_char(SORTFALL, '999,999,990.90')
      from invest.EMAIL_PV_STAT_FOOTER
     where accnt_no = :acc"""

SQL_PORTFOLIO_SUMS = """
    select sum(nvl(NO_OF_SHARES, 0)), sum(nvl(PURCHASE_COST, 0)),
           sum(nvl(MARKET_VALUE, 0)), sum(nvl(DIFFERENCE, 0))
      from invest.EMAIL_PV_STAT_DATA
     where accnt_no = :acc"""

# ------------------------------------------------------------ sale/purchase --

SQL_SP_RECIPIENTS = """
    select ACCNT_NO, email_no
      from invest.accnt_mobile_info
     where email_no is not null
       and to_number(accnt_no) in (select distinct accnt_no from invest.V_SALE_BUY_CONFIRMATION)
     order by accnt_no"""

SQL_SP_TRANSACTIONS = """
    select ACCNT_NO, COMP_NM, SP_DATE, TR_CD, RATE, QUANTITY
      from invest.V_SALE_BUY_CONFIRMATION
     where to_number(accnt_no) = :acc
     order by comp_nm"""

FOOTER_COLUMNS = 7
EMPTY_FOOTER: Row = (None,) * FOOTER_COLUMNS


class Repository(Protocol):
    """What the jobs need from the database (lets tests plug in fake data)."""

    def portfolio_profiles(self) -> list[Row]: ...
    def portfolio_holdings(self, account: Any) -> list[Row]: ...
    def portfolio_footer(self, account: Any) -> tuple[Row, Row]: ...
    def portfolio_sums(self, account: Any) -> Row: ...
    def sale_purchase_recipients(self) -> list[Row]: ...
    def sale_purchase_transactions(self, account: Any) -> list[Row]: ...


def as_account_number(value: Any) -> Any:
    """The legacy SQL compared account numbers as numeric literals; keep that."""
    text = str(value).strip()
    return int(text) if text.isdigit() else value


class OracleRepository:
    def __init__(self, connection: oracledb.Connection):
        self._conn = connection

    def _all(self, sql: str, **binds: Any) -> list[Row]:
        with self._conn.cursor() as cur:
            cur.arraysize = 500
            cur.execute(sql, binds)
            return [tuple(r) for r in cur.fetchall()]

    def portfolio_profiles(self) -> list[Row]:
        return self._all(SQL_PORTFOLIO_PROFILES)

    def portfolio_holdings(self, account: Any) -> list[Row]:
        return self._all(SQL_PORTFOLIO_HOLDINGS, acc=as_account_number(account))

    def portfolio_footer(self, account: Any) -> tuple[Row, Row]:
        """Return (raw values for the HTML body, to_char text for the PDF)."""
        rows = self._all(SQL_PORTFOLIO_FOOTER, acc=as_account_number(account))
        if not rows:
            return EMPTY_FOOTER, EMPTY_FOOTER
        row = rows[0]
        return row[:FOOTER_COLUMNS], row[FOOTER_COLUMNS:]

    def portfolio_sums(self, account: Any) -> Row:
        rows = self._all(SQL_PORTFOLIO_SUMS, acc=as_account_number(account))
        return rows[0] if rows else (0, 0, 0, 0)

    def sale_purchase_recipients(self) -> list[Row]:
        return self._all(SQL_SP_RECIPIENTS)

    def sale_purchase_transactions(self, account: Any) -> list[Row]:
        return self._all(SQL_SP_TRANSACTIONS, acc=as_account_number(account))


def count_rows(connection: oracledb.Connection, tables: Sequence[str]) -> dict[str, int | str]:
    """Used by the `check` command: row counts of the source objects."""
    result: dict[str, int | str] = {}
    with connection.cursor() as cur:
        for table in tables:
            try:
                cur.execute(f"select count(*) from {table}")
                result[table] = cur.fetchone()[0]
            except oracledb.Error as exc:
                result[table] = f"ERROR {exc}".splitlines()[0]
    return result

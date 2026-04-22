"""Source-level guard: the admin_orders completion handler MUST mirror
`result.error_message` (or the top-level request.error_message) into the
top-level `error_message` column.

Pre-fix behavior: the 0.4.7 daemon's head+tail 4KB nix banner was written
to `admin_orders.result->>'error_message'` but the column stayed NULL.
The substrate runbook's sample SQL (`SELECT error_message FROM admin_orders
WHERE order_type='nixos_rebuild'`) returned blanks for every failure.

This test protects the mirror: if someone removes the COALESCE update,
runbooks + log shippers go blind again.
"""

from pathlib import Path

SITES_PY = (
    Path(__file__).resolve().parent.parent / "sites.py"
)


def test_complete_order_mirrors_error_message_to_column():
    src = SITES_PY.read_text()

    # The handler must compute err_msg_for_column before the UPDATE so the
    # request.error_message path AND the result.error_message lift path
    # both populate the column.
    assert "err_msg_for_column = request.error_message" in src, (
        "complete_order() must compute err_msg_for_column from "
        "request.error_message (with fallback to result_data)."
    )
    assert 'result_data.get("error_message")' in src, (
        "complete_order() must fall back to result_data.error_message "
        "when request.error_message is absent."
    )

    # The UPDATE must include the COALESCE clause so prior non-null values
    # are never clobbered by a subsequent null-report.
    assert "error_message = COALESCE($5, error_message)" in src, (
        "UPDATE admin_orders must set error_message = COALESCE($5, "
        "error_message). Protects runbook SQL + log shippers keyed on the "
        "top-level column."
    )

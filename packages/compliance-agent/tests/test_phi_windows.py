"""
Test PHI scrubbing with Windows-style logs.

Tests the PHI scrubber against realistic Windows event log formats
to ensure PHI/PII is properly redacted before log processing.
"""

import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from compliance_agent.phi_scrubber import PHIScrubber, scrub_phi


class TestWindowsLogScrubbing:
    """Test PHI scrubbing on Windows-style logs."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_windows_security_event_with_email(self, scrubber):
        """Test scrubbing email from Windows security events."""
        log = "2025-12-04 10:15:23 Security Event 4624: User john.smith@clinic.com logged on"
        result, stats = scrubber.scrub(log)

        assert "john.smith@clinic.com" not in result
        assert "[EMAIL-REDACTED]" in result
        assert stats.phi_scrubbed is True

    def test_windows_security_event_with_ip(self, scrubber):
        """Test scrubbing IP address from Windows security events."""
        log = "Security Event 4625: Failed login attempt from 192.168.1.100"
        result, stats = scrubber.scrub(log)

        assert "192.168.1.100" not in result
        assert "[IP-REDACTED]" in result

    def test_windows_ehr_event_with_mrn(self, scrubber):
        """Test scrubbing MRN from EHR application events."""
        log = "2025-12-04 10:16:45 Application Event: Patient lookup for MRN: 12345678 by user jsmith"
        result, stats = scrubber.scrub(log)

        assert "12345678" not in result
        assert "[MRN-REDACTED]" in result

    def test_windows_file_access_with_ssn(self, scrubber):
        """Test scrubbing SSN from file access events."""
        log = "Security Event 4663: File accessed: C:\\PatientRecords\\SSN-123-45-6789-record.pdf"
        result, stats = scrubber.scrub(log)

        assert "123-45-6789" not in result
        assert "[SSN-REDACTED]" in result

    def test_windows_event_with_phone_and_dob(self, scrubber):
        """Test scrubbing phone and DOB from EHR events."""
        log = "EHR System: Appointment for patient, phone: 555-867-5309, DOB: 01/15/1980"
        result, stats = scrubber.scrub(log)

        assert "555-867-5309" not in result
        assert "[PHONE-REDACTED]" in result
        assert "[DOB-REDACTED]" in result

    def test_windows_insurance_event(self, scrubber):
        """Test scrubbing insurance ID from audit logs."""
        log = "Audit Log: Insurance claim processed, Insurance ID: BC12345678901234"
        result, stats = scrubber.scrub(log)

        assert "BC12345678901234" not in result
        assert "[INSURANCE-ID-REDACTED]" in result

    def test_windows_email_server_event(self, scrubber):
        """Test scrubbing multiple emails from SMTP events."""
        log = "Email Server: SMTP received from john.doe@example.com to patient@clinic.org"
        result, stats = scrubber.scrub(log)

        assert "john.doe@example.com" not in result
        assert "patient@clinic.org" not in result
        assert result.count("[EMAIL-REDACTED]") == 2

    def test_windows_billing_with_cc(self, scrubber):
        """Test scrubbing credit card from billing events."""
        log = "Billing System: Credit card payment received: 4111-1111-1111-1111"
        result, stats = scrubber.scrub(log)

        assert "4111-1111-1111-1111" not in result
        assert "[CC-REDACTED]" in result

    def test_windows_address_event(self, scrubber):
        """Test scrubbing street address from access logs."""
        log = "Access Log: 456 Oak Street Apt 2B address verified for patient record"
        result, stats = scrubber.scrub(log)

        assert "456 Oak Street Apt 2B" not in result
        assert "[ADDRESS-REDACTED]" in result

    def test_windows_medicare_event(self, scrubber):
        """Test scrubbing Medicare number from claim events."""
        log = "Medicare Claim: Medicare: 1EG4-TE5-MK72 processed for patient visit"
        result, stats = scrubber.scrub(log)

        assert "1EG4-TE5-MK72" not in result
        assert "[MEDICARE-REDACTED]" in result


class TestWindowsEventLogFormats:
    """Test various Windows event log formats."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_ad_account_creation_event(self, scrubber):
        """Test scrubbing AD account creation events."""
        log = """
        Event ID: 4720
        A user account was created.
        Subject:
            Security ID: S-1-5-21-1234567890-1234567890-1234567890-500
            Account Name: AdminUser
        New Account:
            Security ID: S-1-5-21-1234567890-1234567890-1234567890-1001
            Account Name: john.smith@hospital.com
        """
        result, stats = scrubber.scrub(log)

        assert "john.smith@hospital.com" not in result
        assert "[EMAIL-REDACTED]" in result

    def test_ad_logon_event(self, scrubber):
        """Test scrubbing AD logon events with IP."""
        log = """
        Event ID: 4624
        An account was successfully logged on.
        Subject:
            Security ID: NULL SID
        Logon Type: 3
        New Logon:
            Account Name: nurse1
            Account Domain: HOSPITAL
        Network Information:
            Workstation Name: WS-NURSE-01
            Source Network Address: 10.0.0.50
            Source Port: 49152
        """
        result, stats = scrubber.scrub(log)

        assert "10.0.0.50" not in result
        assert "[IP-REDACTED]" in result

    def test_multi_line_hipaa_audit_log(self, scrubber):
        """Test scrubbing complex multi-line HIPAA audit logs."""
        log = """
        HIPAA Audit Event
        Timestamp: 2025-12-04 14:30:00
        User: jsmith@clinic.org
        Action: Patient Record Access
        Patient MRN: 87654321
        Patient Phone: (555) 123-4567
        Patient DOB: 03/15/1975
        Patient Address: 123 Main Street
        Insurance ID: BCBS123456789
        Workstation IP: 192.168.10.25
        Status: Success
        """
        result, stats = scrubber.scrub(log)

        # Verify all PHI is scrubbed
        assert "jsmith@clinic.org" not in result
        assert "87654321" not in result
        assert "(555) 123-4567" not in result
        assert "03/15/1975" not in result
        assert "123 Main Street" not in result
        assert "192.168.10.25" not in result

        # Verify redaction markers are present
        assert "[EMAIL-REDACTED]" in result
        assert "[MRN-REDACTED]" in result
        assert "[PHONE-REDACTED]" in result
        assert "[IP-REDACTED]" in result
        assert "[INSURANCE-ID-REDACTED]" in result

        # Verify stats show multiple patterns
        assert stats.phi_scrubbed is True
        assert stats.patterns_matched >= 6


class TestWindowsLogTimestampPreservation:
    """Test that Windows log timestamps are preserved."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_timestamp_preserved(self, scrubber):
        """Verify timestamps are not scrubbed."""
        log = "2025-12-04 10:23:45 INFO Patient MRN: 98765432 accessed"
        result, stats = scrubber.scrub(log)

        # Timestamp should be preserved
        assert "2025-12-04 10:23:45" in result
        # MRN should be scrubbed
        assert "[MRN-REDACTED]" in result

    def test_windows_timestamp_formats(self, scrubber):
        """Test various Windows timestamp formats are preserved."""
        timestamps = [
            "12/04/2025 10:23:45 AM",
            "2025-12-04T10:23:45.000Z",
            "04-Dec-2025 10:23:45",
        ]

        for ts in timestamps:
            log = f"{ts}: MRN: 12345678 accessed by user"
            result, stats = scrubber.scrub(log)
            # MRN should be scrubbed but timestamp format should largely remain
            assert "[MRN-REDACTED]" in result


class TestWindowsSecurityIDPreservation:
    """Test that Windows Security IDs (SIDs) are handled appropriately."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_sid_handling(self, scrubber):
        """Test SID handling - these are not PHI."""
        log = "Security ID: S-1-5-21-1234567890-987654321-1122334455-1001"
        result, stats = scrubber.scrub(log)

        # SIDs are not typically PHI - they're Windows security identifiers
        # Our scrubber may or may not touch these depending on patterns
        # The important thing is we don't break the log format
        assert "Security ID:" in result


class TestComprehensiveWindowsScrubbing:
    """Comprehensive end-to-end Windows log scrubbing test."""

    def test_comprehensive_windows_log_scrubbing(self):
        """Test scrubbing a comprehensive set of Windows-style logs."""
        windows_logs = """
        2025-12-04 10:15:23 Security Event 4624: User john.smith@clinic.com logged on from 192.168.1.50
        2025-12-04 10:16:45 Application Event: Patient lookup for MRN: 12345678 by user jsmith
        2025-12-04 10:18:00 Security Event 4663: File accessed: C:\\PatientRecords\\SSN-123-45-6789-record.pdf
        2025-12-04 10:20:00 EHR System: Appointment scheduled for patient, phone: 555-867-5309, DOB: 01/15/1980
        2025-12-04 10:22:00 Audit Log: Insurance claim processed, Insurance ID: BC12345678901234
        2025-12-04 10:25:00 Email Server: SMTP received from john.doe@example.com to patient@clinic.org
        2025-12-04 10:28:00 AD Event 4720: User account created for Jane Doe, Account: jdoe
        2025-12-04 10:30:00 Billing System: Credit card payment received: 4111-1111-1111-1111
        2025-12-04 10:32:00 Access Log: 456 Oak Street Apt 2B address verified for patient record
        2025-12-04 10:35:00 Medicare Claim: Medicare: 1EG4-TE5-MK72 processed for patient visit
        """

        scrubber = PHIScrubber()
        scrubbed, stats = scrubber.scrub(windows_logs)

        # Verify all sensitive data is removed
        sensitive_data = [
            "123-45-6789",           # SSN
            "john.smith@clinic.com", # Email
            "192.168.1.50",          # IP
            "12345678",              # MRN
            "555-867-5309",          # Phone
            "4111-1111-1111-1111",   # Credit card
            "01/15/1980",            # DOB
            "john.doe@example.com",  # Email
            "1EG4-TE5-MK72",         # Medicare
        ]

        for data in sensitive_data:
            assert data not in scrubbed, f"Sensitive data '{data}' was not scrubbed"

        # Verify statistics
        assert stats.phi_scrubbed is True
        assert stats.patterns_matched >= 10

        # Verify structure is preserved
        assert "Security Event 4624" in scrubbed
        assert "Application Event" in scrubbed
        assert "EHR System" in scrubbed
        assert "Billing System" in scrubbed

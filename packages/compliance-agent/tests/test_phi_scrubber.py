"""
Tests for PHI/PII Pattern Scrubber.

Tests various PHI/PII patterns to ensure proper redaction.
"""

import pytest
import tempfile
from pathlib import Path

# Add src to path
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from compliance_agent.phi_scrubber import PHIScrubber, scrub_phi, ScrubResult


class TestPHIScrubberBasic:
    """Basic PHI scrubber tests."""

    @pytest.fixture
    def scrubber(self):
        """Create a default scrubber."""
        return PHIScrubber()

    def test_empty_string(self, scrubber):
        """Test scrubbing empty string."""
        result, stats = scrubber.scrub("")
        assert result == ""
        assert stats.phi_scrubbed is False
        assert stats.patterns_matched == 0

    def test_no_phi_text(self, scrubber):
        """Test text with no PHI."""
        text = "This is a normal log message with no sensitive data."
        result, stats = scrubber.scrub(text)
        assert result == text
        assert stats.phi_scrubbed is False
        assert stats.patterns_matched == 0


class TestSSNScrubbing:
    """Test SSN pattern scrubbing."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_ssn_dashes(self, scrubber):
        """Test SSN with dashes."""
        text = "Patient SSN: 123-45-6789"
        result, stats = scrubber.scrub(text)
        assert "[SSN-REDACTED]" in result
        assert "123-45-6789" not in result
        assert stats.phi_scrubbed is True
        assert stats.patterns_by_type.get('ssn', 0) > 0

    def test_ssn_no_dashes(self, scrubber):
        """Test SSN without dashes."""
        text = "SSN is 123456789"
        result, stats = scrubber.scrub(text)
        assert "[SSN-REDACTED]" in result
        assert "123456789" not in result

    def test_ssn_spaces(self, scrubber):
        """Test SSN with spaces."""
        text = "Social: 123 45 6789"
        result, stats = scrubber.scrub(text)
        assert "[SSN-REDACTED]" in result


class TestMRNScrubbing:
    """Test MRN pattern scrubbing."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_mrn_with_colon(self, scrubber):
        """Test MRN with colon separator."""
        text = "MRN: 12345678"
        result, stats = scrubber.scrub(text)
        assert "[MRN-REDACTED]" in result
        assert "12345678" not in result

    def test_mrn_without_separator(self, scrubber):
        """Test MRN without separator."""
        text = "MRN12345678"
        result, stats = scrubber.scrub(text)
        assert "[MRN-REDACTED]" in result

    def test_mrn_lowercase(self, scrubber):
        """Test MRN lowercase."""
        text = "mrn: 87654321"
        result, stats = scrubber.scrub(text)
        assert "[MRN-REDACTED]" in result


class TestPhoneScrubbing:
    """Test phone number scrubbing."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_phone_dashes(self, scrubber):
        """Test phone with dashes."""
        text = "Call 555-123-4567"
        result, stats = scrubber.scrub(text)
        assert "[PHONE-REDACTED]" in result
        assert "555-123-4567" not in result

    def test_phone_parens(self, scrubber):
        """Test phone with parentheses."""
        text = "Phone: (555) 123-4567"
        result, stats = scrubber.scrub(text)
        assert "[PHONE-REDACTED]" in result

    def test_phone_dots(self, scrubber):
        """Test phone with dots."""
        text = "Contact: 555.123.4567"
        result, stats = scrubber.scrub(text)
        assert "[PHONE-REDACTED]" in result


class TestEmailScrubbing:
    """Test email scrubbing."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_simple_email(self, scrubber):
        """Test simple email address."""
        text = "Email: john.doe@example.com"
        result, stats = scrubber.scrub(text)
        assert "[EMAIL-REDACTED]" in result
        assert "john.doe@example.com" not in result

    def test_email_with_plus(self, scrubber):
        """Test email with plus sign."""
        text = "Contact: user+tag@example.org"
        result, stats = scrubber.scrub(text)
        assert "[EMAIL-REDACTED]" in result


class TestCreditCardScrubbing:
    """Test credit card scrubbing."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_cc_dashes(self, scrubber):
        """Test credit card with dashes."""
        text = "Card: 4111-1111-1111-1111"
        result, stats = scrubber.scrub(text)
        assert "[CC-REDACTED]" in result
        assert "4111-1111-1111-1111" not in result

    def test_cc_spaces(self, scrubber):
        """Test credit card with spaces."""
        text = "Payment: 4111 1111 1111 1111"
        result, stats = scrubber.scrub(text)
        assert "[CC-REDACTED]" in result


class TestDOBScrubbing:
    """Test date of birth scrubbing."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_dob_with_label(self, scrubber):
        """Test DOB with label."""
        text = "DOB: 12/25/1990"
        result, stats = scrubber.scrub(text)
        assert "[DOB-REDACTED]" in result
        assert "12/25/1990" not in result

    def test_birth_date(self, scrubber):
        """Test birth date variant."""
        text = "Birth Date: 01-15-1985"
        result, stats = scrubber.scrub(text)
        assert "[DOB-REDACTED]" in result


class TestAddressScrubbing:
    """Test address scrubbing."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_street_address(self, scrubber):
        """Test street address."""
        text = "Lives at 123 Main Street"
        result, stats = scrubber.scrub(text)
        assert "[ADDRESS-REDACTED]" in result
        assert "123 Main Street" not in result

    def test_address_with_apt(self, scrubber):
        """Test address with apartment."""
        text = "Address: 456 Oak Avenue Apt 2B"
        result, stats = scrubber.scrub(text)
        assert "[ADDRESS-REDACTED]" in result


class TestIPAddressScrubbing:
    """Test IP address scrubbing."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_ipv4_address(self, scrubber):
        """Test IPv4 address."""
        text = "Connected from 192.168.1.100"
        result, stats = scrubber.scrub(text)
        assert "[IP-REDACTED]" in result
        assert "192.168.1.100" not in result


class TestInsuranceScrubbing:
    """Test insurance ID scrubbing."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_insurance_id(self, scrubber):
        """Test insurance ID."""
        text = "Insurance ID: ABC123456789"
        result, stats = scrubber.scrub(text)
        assert "[INSURANCE-ID-REDACTED]" in result

    def test_medicare_number(self, scrubber):
        """Test Medicare number."""
        text = "Medicare: 1EG4-TE5-MK72"
        result, stats = scrubber.scrub(text)
        assert "[MEDICARE-REDACTED]" in result


class TestMultiplePatterns:
    """Test scrubbing multiple patterns in same text."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_multiple_patterns(self, scrubber):
        """Test multiple PHI patterns in same text."""
        text = """
        Patient Record:
        Name: John Doe
        SSN: 123-45-6789
        MRN: 12345678
        Phone: 555-123-4567
        Email: john.doe@clinic.com
        DOB: 01/15/1980
        Address: 123 Main Street
        """
        result, stats = scrubber.scrub(text)

        # All patterns should be redacted
        assert "[SSN-REDACTED]" in result
        assert "[MRN-REDACTED]" in result
        assert "[PHONE-REDACTED]" in result
        assert "[EMAIL-REDACTED]" in result
        assert "[DOB-REDACTED]" in result
        assert "[ADDRESS-REDACTED]" in result

        # Original values should be gone
        assert "123-45-6789" not in result
        assert "12345678" not in result
        assert "555-123-4567" not in result
        assert "john.doe@clinic.com" not in result

        # Stats should reflect multiple matches
        assert stats.phi_scrubbed is True
        assert stats.patterns_matched >= 6

    def test_realistic_log_entry(self, scrubber):
        """Test realistic log entry with PHI."""
        text = "2025-12-04 10:23:45 INFO Patient MRN: 98765432 accessed record from 192.168.1.50, phone 555-987-6543"
        result, stats = scrubber.scrub(text)

        # PHI should be redacted
        assert "[MRN-REDACTED]" in result
        assert "[IP-REDACTED]" in result
        assert "[PHONE-REDACTED]" in result

        # Timestamp should be preserved
        assert "2025-12-04 10:23:45" in result


class TestHashRedaction:
    """Test hash-based redaction for correlation."""

    def test_hash_redaction(self):
        """Test hash suffix is added when enabled."""
        scrubber = PHIScrubber(hash_redacted=True)
        text = "SSN: 123-45-6789"
        result, stats = scrubber.scrub(text)

        # Should have hash suffix
        assert "[SSN-REDACTED-" in result
        assert result.count("]") == 1  # Only one closing bracket

    def test_consistent_hash(self):
        """Test same value produces same hash."""
        scrubber = PHIScrubber(hash_redacted=True)

        text1 = "SSN: 123-45-6789"
        text2 = "SSN: 123-45-6789"

        result1, _ = scrubber.scrub(text1)
        result2, _ = scrubber.scrub(text2)

        # Same input should produce same output
        assert result1 == result2


class TestDictScrubbing:
    """Test dictionary scrubbing."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_scrub_dict(self, scrubber):
        """Test scrubbing dictionary values."""
        data = {
            "patient_name": "John Doe",
            "ssn": "123-45-6789",
            "phone": "555-123-4567",
            "notes": "Patient called from 192.168.1.100"
        }

        result, stats = scrubber.scrub_dict(data)

        assert "[SSN-REDACTED]" in result["ssn"]
        assert "[PHONE-REDACTED]" in result["phone"]
        assert "[IP-REDACTED]" in result["notes"]
        assert stats.phi_scrubbed is True

    def test_scrub_nested_dict(self, scrubber):
        """Test scrubbing nested dictionary."""
        data = {
            "patient": {
                "ssn": "123-45-6789",
                "contact": {
                    "phone": "555-123-4567"
                }
            }
        }

        result, stats = scrubber.scrub_dict(data)

        assert "[SSN-REDACTED]" in result["patient"]["ssn"]
        assert "[PHONE-REDACTED]" in result["patient"]["contact"]["phone"]

    def test_scrub_specific_keys(self, scrubber):
        """Test scrubbing only specific keys."""
        data = {
            "ssn": "123-45-6789",
            "phone": "555-123-4567"
        }

        # Only scrub 'ssn' key
        result, stats = scrubber.scrub_dict(data, keys_to_scrub=["ssn"])

        assert "[SSN-REDACTED]" in result["ssn"]
        # Phone should NOT be scrubbed (key not in list)
        assert result["phone"] == "555-123-4567"


class TestFileScrubbing:
    """Test file scrubbing."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_scrub_file(self, scrubber):
        """Test scrubbing a file."""
        input_content = """
2025-12-04 10:00:00 Patient MRN: 12345678 checked in
2025-12-04 10:05:00 SSN verified: 123-45-6789
2025-12-04 10:10:00 Contact: 555-123-4567
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as infile:
            infile.write(input_content)
            input_path = infile.name

        output_path = input_path + '.scrubbed'

        try:
            stats = scrubber.scrub_file(input_path, output_path)

            # Check stats
            assert stats.phi_scrubbed is True
            assert stats.patterns_matched >= 3

            # Check output file
            with open(output_path) as f:
                output_content = f.read()

            assert "[MRN-REDACTED]" in output_content
            assert "[SSN-REDACTED]" in output_content
            assert "[PHONE-REDACTED]" in output_content
            assert "12345678" not in output_content
            assert "123-45-6789" not in output_content

        finally:
            Path(input_path).unlink(missing_ok=True)
            Path(output_path).unlink(missing_ok=True)


class TestLogLineScrubbing:
    """Test log line scrubbing (optimized path)."""

    @pytest.fixture
    def scrubber(self):
        return PHIScrubber()

    def test_scrub_log_line_with_phi(self, scrubber):
        """Test scrubbing log line with PHI."""
        line = "2025-12-04 10:00:00 ERROR Patient SSN 123-45-6789 not found"
        result, was_scrubbed = scrubber.scrub_log_line(line)

        assert was_scrubbed is True
        assert "[SSN-REDACTED]" in result
        assert "123-45-6789" not in result

    def test_scrub_log_line_without_phi(self, scrubber):
        """Test scrubbing log line without PHI."""
        line = "2025-12-04 10:00:00 INFO Application started successfully"
        result, was_scrubbed = scrubber.scrub_log_line(line)

        assert was_scrubbed is False
        assert result == line


class TestConvenienceFunction:
    """Test convenience function."""

    def test_scrub_phi_function(self):
        """Test quick scrub_phi function."""
        text = "Patient SSN: 123-45-6789, Phone: 555-123-4567"
        result = scrub_phi(text)

        assert "[SSN-REDACTED]" in result
        assert "[PHONE-REDACTED]" in result
        assert "123-45-6789" not in result
        assert "555-123-4567" not in result


class TestSelectivePatterns:
    """Test selective pattern enabling."""

    def test_only_ssn(self):
        """Test only enabling SSN pattern."""
        scrubber = PHIScrubber(patterns=['ssn'])
        text = "SSN: 123-45-6789, Phone: 555-123-4567"
        result, stats = scrubber.scrub(text)

        assert "[SSN-REDACTED]" in result
        # Phone should NOT be redacted
        assert "555-123-4567" in result

    def test_multiple_selected(self):
        """Test multiple selected patterns."""
        scrubber = PHIScrubber(patterns=['ssn', 'email'])
        text = "SSN: 123-45-6789, Email: test@example.com, Phone: 555-123-4567"
        result, stats = scrubber.scrub(text)

        assert "[SSN-REDACTED]" in result
        assert "[EMAIL-REDACTED]" in result
        # Phone should NOT be redacted
        assert "555-123-4567" in result


class TestCustomPatterns:
    """Test custom pattern support."""

    def test_custom_pattern(self):
        """Test adding custom pattern."""
        import re
        custom_patterns = {
            'employee_id': (
                re.compile(r'\bEMP-\d{6}\b'),
                '[EMPLOYEE-ID-REDACTED]'
            )
        }
        scrubber = PHIScrubber(custom_patterns=custom_patterns)

        text = "Employee EMP-123456 accessed the system"
        result, stats = scrubber.scrub(text)

        assert "[EMPLOYEE-ID-REDACTED]" in result
        assert "EMP-123456" not in result

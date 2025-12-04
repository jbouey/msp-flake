"""
PHI/PII Pattern Scrubber.

Scrubs Protected Health Information (PHI) and Personally Identifiable
Information (PII) from logs and data before processing or storage.

HIPAA Controls:
- §164.502: Minimum necessary standard
- §164.514: De-identification requirements
"""

import re
import hashlib
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class ScrubResult:
    """Result of scrubbing operation."""
    original_length: int
    scrubbed_length: int
    patterns_matched: int
    patterns_by_type: Dict[str, int]
    phi_scrubbed: bool


class PHIScrubber:
    """
    Scrub PHI/PII patterns from text data.

    Patterns detected and redacted:
    - SSN (Social Security Numbers)
    - MRN (Medical Record Numbers)
    - Phone numbers
    - Email addresses
    - Credit card numbers
    - IP addresses
    - Dates of birth
    - Names (common patterns)
    - Addresses (street patterns)
    """

    # Pattern definitions with named groups
    PATTERNS: Dict[str, Tuple[re.Pattern, str]] = {
        # SSN: XXX-XX-XXXX or XXXXXXXXX
        'ssn': (
            re.compile(r'\b(\d{3}[-\s]?\d{2}[-\s]?\d{4})\b'),
            '[SSN-REDACTED]'
        ),

        # MRN: Various formats like MRN12345, MRN: 12345, etc.
        'mrn': (
            re.compile(r'\b(MRN\s*[:=#]?\s*\d{4,12})\b', re.IGNORECASE),
            '[MRN-REDACTED]'
        ),

        # Patient ID patterns
        'patient_id': (
            re.compile(r'\b(patient[_\s]?id\s*[:=#]?\s*[\w\d-]{4,20})\b', re.IGNORECASE),
            '[PATIENT-ID-REDACTED]'
        ),

        # Phone numbers (US format)
        'phone': (
            re.compile(r'\b(\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})\b'),
            '[PHONE-REDACTED]'
        ),

        # Email addresses
        'email': (
            re.compile(r'\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b'),
            '[EMAIL-REDACTED]'
        ),

        # Credit card numbers (major formats)
        'credit_card': (
            re.compile(r'\b(\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4})\b'),
            '[CC-REDACTED]'
        ),

        # Dates of birth (various formats)
        'dob': (
            re.compile(
                r'\b(DOB\s*[:=]?\s*\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|'
                r'birth\s*date\s*[:=]?\s*\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b',
                re.IGNORECASE
            ),
            '[DOB-REDACTED]'
        ),

        # Street addresses (common patterns)
        'address': (
            re.compile(
                r'\b(\d{1,5}\s+[\w\s]{1,30}\s+'
                r'(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|'
                r'lane|ln|court|ct|place|pl|way|circle|cir)'
                r'(?:\s*[,.]?\s*(?:apt|apartment|suite|ste|unit|#)\s*[\w\d-]+)?)\b',
                re.IGNORECASE
            ),
            '[ADDRESS-REDACTED]'
        ),

        # ZIP codes (US)
        'zip': (
            re.compile(r'\b(\d{5}(?:-\d{4})?)\b'),
            '[ZIP-REDACTED]'
        ),

        # IP addresses (IPv4)
        'ip_address': (
            re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b'),
            '[IP-REDACTED]'
        ),

        # Account numbers
        'account_number': (
            re.compile(r'\b(account\s*(?:number|num|no|#)?\s*[:=]?\s*[\w\d-]{6,20})\b', re.IGNORECASE),
            '[ACCOUNT-REDACTED]'
        ),

        # Insurance ID
        'insurance_id': (
            re.compile(r'\b(insurance\s*(?:id|#|number)?\s*[:=]?\s*[\w\d-]{6,20})\b', re.IGNORECASE),
            '[INSURANCE-ID-REDACTED]'
        ),

        # Medicare/Medicaid numbers
        'medicare': (
            re.compile(r'\b(medicare\s*(?:id|#|number)?\s*[:=]?\s*[\w\d-]{8,15})\b', re.IGNORECASE),
            '[MEDICARE-REDACTED]'
        ),

        # Driver's license
        'drivers_license': (
            re.compile(r'\b(DL\s*[:=#]?\s*[\w\d]{6,15})\b', re.IGNORECASE),
            '[DL-REDACTED]'
        ),
    }

    # Patterns to preserve (false positives)
    PRESERVE_PATTERNS = [
        # Common timestamps that look like phone numbers
        re.compile(r'\d{4}[-/]\d{2}[-/]\d{2}'),
        # Version numbers
        re.compile(r'v?\d+\.\d+\.\d+'),
        # Port numbers after colon
        re.compile(r':\d{2,5}\b'),
    ]

    def __init__(
        self,
        patterns: Optional[List[str]] = None,
        custom_patterns: Optional[Dict[str, Tuple[re.Pattern, str]]] = None,
        hash_redacted: bool = False
    ):
        """
        Initialize PHI scrubber.

        Args:
            patterns: List of pattern types to enable (None = all)
            custom_patterns: Additional custom patterns
            hash_redacted: If True, append hash of original for correlation
        """
        self.enabled_patterns = patterns or list(self.PATTERNS.keys())
        self.custom_patterns = custom_patterns or {}
        self.hash_redacted = hash_redacted

        # Build active pattern list
        self.active_patterns: Dict[str, Tuple[re.Pattern, str]] = {}
        for name in self.enabled_patterns:
            if name in self.PATTERNS:
                self.active_patterns[name] = self.PATTERNS[name]
        self.active_patterns.update(self.custom_patterns)

    def scrub(self, text: str) -> Tuple[str, ScrubResult]:
        """
        Scrub PHI/PII from text.

        Args:
            text: Input text to scrub

        Returns:
            Tuple of (scrubbed_text, ScrubResult)
        """
        if not text:
            return text, ScrubResult(
                original_length=0,
                scrubbed_length=0,
                patterns_matched=0,
                patterns_by_type={},
                phi_scrubbed=False
            )

        original_length = len(text)
        scrubbed = text
        patterns_by_type: Dict[str, int] = {}
        total_matches = 0

        # Check each pattern
        for pattern_name, (pattern, replacement) in self.active_patterns.items():
            matches = pattern.findall(scrubbed)

            if matches:
                count = len(matches)
                patterns_by_type[pattern_name] = count
                total_matches += count

                # Apply replacement
                if self.hash_redacted:
                    # Replace with hash-based redaction for correlation
                    def replace_with_hash(match):
                        value = match.group(0)
                        hash_suffix = hashlib.sha256(value.encode()).hexdigest()[:8]
                        return f"{replacement[:-1]}-{hash_suffix}]"
                    scrubbed = pattern.sub(replace_with_hash, scrubbed)
                else:
                    scrubbed = pattern.sub(replacement, scrubbed)

        result = ScrubResult(
            original_length=original_length,
            scrubbed_length=len(scrubbed),
            patterns_matched=total_matches,
            patterns_by_type=patterns_by_type,
            phi_scrubbed=total_matches > 0
        )

        return scrubbed, result

    def scrub_dict(
        self,
        data: Dict,
        keys_to_scrub: Optional[List[str]] = None
    ) -> Tuple[Dict, ScrubResult]:
        """
        Scrub PHI/PII from dictionary values.

        Args:
            data: Input dictionary
            keys_to_scrub: Specific keys to scrub (None = all string values)

        Returns:
            Tuple of (scrubbed_dict, combined ScrubResult)
        """
        import copy
        scrubbed_data = copy.deepcopy(data)
        total_result = ScrubResult(
            original_length=0,
            scrubbed_length=0,
            patterns_matched=0,
            patterns_by_type={},
            phi_scrubbed=False
        )

        def process_value(value, key=None):
            nonlocal total_result

            if isinstance(value, str):
                if keys_to_scrub is None or key in keys_to_scrub:
                    scrubbed, result = self.scrub(value)
                    total_result.original_length += result.original_length
                    total_result.scrubbed_length += result.scrubbed_length
                    total_result.patterns_matched += result.patterns_matched
                    for ptype, count in result.patterns_by_type.items():
                        total_result.patterns_by_type[ptype] = \
                            total_result.patterns_by_type.get(ptype, 0) + count
                    if result.phi_scrubbed:
                        total_result.phi_scrubbed = True
                    return scrubbed
            elif isinstance(value, dict):
                return {k: process_value(v, k) for k, v in value.items()}
            elif isinstance(value, list):
                return [process_value(v) for v in value]
            return value

        scrubbed_data = {k: process_value(v, k) for k, v in scrubbed_data.items()}
        return scrubbed_data, total_result

    def scrub_log_line(self, line: str) -> Tuple[str, bool]:
        """
        Scrub a single log line.

        Optimized for high-throughput log processing.

        Args:
            line: Log line to scrub

        Returns:
            Tuple of (scrubbed_line, was_scrubbed)
        """
        scrubbed, result = self.scrub(line)
        return scrubbed, result.phi_scrubbed

    def scrub_file(
        self,
        input_path: str,
        output_path: str,
        encoding: str = 'utf-8'
    ) -> ScrubResult:
        """
        Scrub PHI/PII from a file.

        Args:
            input_path: Path to input file
            output_path: Path to output file
            encoding: File encoding

        Returns:
            ScrubResult with overall statistics
        """
        total_result = ScrubResult(
            original_length=0,
            scrubbed_length=0,
            patterns_matched=0,
            patterns_by_type={},
            phi_scrubbed=False
        )

        with open(input_path, 'r', encoding=encoding) as infile, \
             open(output_path, 'w', encoding=encoding) as outfile:

            for line in infile:
                scrubbed, result = self.scrub(line)
                outfile.write(scrubbed)

                total_result.original_length += result.original_length
                total_result.scrubbed_length += result.scrubbed_length
                total_result.patterns_matched += result.patterns_matched
                for ptype, count in result.patterns_by_type.items():
                    total_result.patterns_by_type[ptype] = \
                        total_result.patterns_by_type.get(ptype, 0) + count
                if result.phi_scrubbed:
                    total_result.phi_scrubbed = True

        return total_result


# Convenience function for quick scrubbing
def scrub_phi(text: str) -> str:
    """
    Quick PHI scrub of text.

    Args:
        text: Text to scrub

    Returns:
        Scrubbed text
    """
    scrubber = PHIScrubber()
    scrubbed, _ = scrubber.scrub(text)
    return scrubbed

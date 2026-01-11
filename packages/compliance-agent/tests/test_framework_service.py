"""
Tests for Multi-Framework Compliance Service

Tests cover:
- Control mapping functionality
- Multi-framework evidence generation
- Compliance score calculation
- Industry recommendations
- Backward compatibility with HIPAA-only systems
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path

from compliance_agent.frameworks.schema import (
    ComplianceFramework,
    FrameworkControl,
    InfrastructureCheck,
    ApplianceFrameworkConfig,
    MultiFrameworkEvidence,
    ComplianceScore,
    ControlStatus,
    get_recommended_frameworks,
)
from compliance_agent.frameworks.framework_service import (
    FrameworkService,
    get_framework_service,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def framework_service():
    """Create a framework service instance for testing"""
    return FrameworkService()


@pytest.fixture
def sample_evidence_pass():
    """Create sample passing evidence bundle"""
    return MultiFrameworkEvidence(
        bundle_id="EB-TEST-PASS-001",
        appliance_id="test-appliance-001",
        site_id="test-site-001",
        check_id="backup_status",
        check_type="windows",
        timestamp=datetime.utcnow(),
        outcome="pass",
        framework_mappings={
            ComplianceFramework.HIPAA: ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],
            ComplianceFramework.SOC2: ["A1.2", "A1.3"],
            ComplianceFramework.PCI_DSS: ["12.10.1"],
            ComplianceFramework.NIST_CSF: ["PR.IP-4", "RC.RP-1"],
        },
        hipaa_controls=["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],
        raw_data={"backup_job": "completed", "timestamp": "2026-01-11T00:00:00Z"},
        signature="test-signature",
        storage_locations=["s3://evidence/EB-TEST-PASS-001"],
    )


@pytest.fixture
def sample_evidence_fail():
    """Create sample failing evidence bundle"""
    return MultiFrameworkEvidence(
        bundle_id="EB-TEST-FAIL-001",
        appliance_id="test-appliance-001",
        site_id="test-site-001",
        check_id="encryption_at_rest",
        check_type="windows",
        timestamp=datetime.utcnow(),
        outcome="fail",
        framework_mappings={
            ComplianceFramework.HIPAA: ["164.312(a)(2)(iv)"],
            ComplianceFramework.SOC2: ["CC6.1", "CC6.7"],
            ComplianceFramework.PCI_DSS: ["3.4", "3.5"],
            ComplianceFramework.NIST_CSF: ["PR.DS-1"],
        },
        hipaa_controls=["164.312(a)(2)(iv)"],
        raw_data={"bitlocker_enabled": False},
        signature="test-signature",
        storage_locations=[],
    )


# =============================================================================
# Test Control Mapping
# =============================================================================

class TestControlMappings:
    """Test control mapping functionality"""

    def test_service_loads_mappings(self, framework_service):
        """Service should load mappings from YAML"""
        assert framework_service.is_loaded
        assert framework_service.check_count > 0
        assert framework_service.framework_count > 0

    def test_backup_maps_to_all_frameworks(self, framework_service):
        """Backup check should map to HIPAA, SOC2, PCI DSS, NIST CSF"""
        controls = framework_service.get_controls_for_check("backup_status")

        assert ComplianceFramework.HIPAA in controls
        assert ComplianceFramework.SOC2 in controls
        assert ComplianceFramework.PCI_DSS in controls
        assert ComplianceFramework.NIST_CSF in controls

        # HIPAA should have specific control
        assert "164.308(a)(7)(ii)(A)" in controls[ComplianceFramework.HIPAA]

    def test_encryption_maps_to_hipaa(self, framework_service):
        """Encryption check should map to HIPAA technical safeguards"""
        controls = framework_service.get_controls_for_check("encryption_at_rest")

        assert ComplianceFramework.HIPAA in controls
        assert "164.312(a)(2)(iv)" in controls[ComplianceFramework.HIPAA]

    def test_filter_by_framework(self, framework_service):
        """Should filter controls to specific frameworks"""
        controls = framework_service.get_controls_for_check(
            "encryption_at_rest",
            frameworks=[ComplianceFramework.PCI_DSS]
        )

        assert ComplianceFramework.PCI_DSS in controls
        assert ComplianceFramework.HIPAA not in controls
        assert "3.4" in controls[ComplianceFramework.PCI_DSS]

    def test_get_checks_for_framework(self, framework_service):
        """Should return all checks that satisfy a framework"""
        checks = framework_service.get_checks_for_framework(ComplianceFramework.HIPAA)

        assert len(checks) >= 5  # At minimum: backup, encryption, firewall, av, patch, logging
        check_ids = [c.check_id for c in checks]
        assert "backup_status" in check_ids
        assert "encryption_at_rest" in check_ids
        assert "firewall_enabled" in check_ids

    def test_get_all_controls_for_framework(self, framework_service):
        """Should return all control IDs for a framework"""
        controls = framework_service.get_all_controls_for_framework(ComplianceFramework.HIPAA)

        assert len(controls) > 0
        # Should include key HIPAA controls
        assert "164.308(a)(7)(ii)(A)" in controls  # Backup
        assert "164.312(a)(2)(iv)" in controls  # Encryption
        assert "164.312(e)(1)" in controls  # Transmission security

    def test_unknown_check_returns_empty(self, framework_service):
        """Unknown check should return empty dict"""
        controls = framework_service.get_controls_for_check("nonexistent_check")
        assert controls == {}

    def test_get_hipaa_controls_backward_compat(self, framework_service):
        """Should provide HIPAA-only controls for backward compatibility"""
        hipaa_controls = framework_service.get_hipaa_controls_for_check("backup_status")

        assert len(hipaa_controls) > 0
        assert "164.308(a)(7)(ii)(A)" in hipaa_controls

    def test_get_control_details(self, framework_service):
        """Should return detailed control information"""
        details = framework_service.get_control_details(
            ComplianceFramework.HIPAA,
            "164.308(a)(7)(ii)(A)"
        )

        assert details is not None
        assert details.control_id == "164.308(a)(7)(ii)(A)"
        assert "Data Backup" in details.control_name
        assert details.category == "Administrative Safeguards"


# =============================================================================
# Test Multi-Framework Evidence Generation
# =============================================================================

class TestMultiFrameworkEvidence:
    """Test multi-framework evidence generation"""

    def test_create_evidence_all_frameworks(self, framework_service):
        """Should create evidence tagged for all frameworks"""
        evidence = framework_service.create_multi_framework_evidence(
            bundle_id="EB-TEST-001",
            appliance_id="test-appliance-001",
            site_id="test-site-001",
            check_id="backup_status",
            check_type="windows",
            outcome="pass",
            raw_data={"backup_complete": True},
            signature="test-sig",
        )

        assert evidence.bundle_id == "EB-TEST-001"
        assert ComplianceFramework.HIPAA in evidence.framework_mappings
        assert ComplianceFramework.SOC2 in evidence.framework_mappings
        assert ComplianceFramework.PCI_DSS in evidence.framework_mappings
        assert ComplianceFramework.NIST_CSF in evidence.framework_mappings

        # HIPAA backward compatibility
        assert len(evidence.hipaa_controls) > 0

    def test_create_evidence_filtered_frameworks(self, framework_service):
        """Should create evidence filtered to specific frameworks"""
        evidence = framework_service.create_multi_framework_evidence(
            bundle_id="EB-TEST-002",
            appliance_id="test-appliance-001",
            site_id="test-site-001",
            check_id="backup_status",
            check_type="windows",
            outcome="pass",
            raw_data={},
            enabled_frameworks=[ComplianceFramework.HIPAA, ComplianceFramework.SOC2],
        )

        assert ComplianceFramework.HIPAA in evidence.framework_mappings
        assert ComplianceFramework.SOC2 in evidence.framework_mappings
        assert ComplianceFramework.PCI_DSS not in evidence.framework_mappings

    def test_evidence_satisfies_control(self, sample_evidence_pass):
        """Evidence should report satisfying specific controls"""
        assert sample_evidence_pass.satisfies_control(
            ComplianceFramework.HIPAA,
            "164.308(a)(7)(ii)(A)"
        )
        assert not sample_evidence_pass.satisfies_control(
            ComplianceFramework.HIPAA,
            "164.312(a)(2)(iv)"  # This is encryption, not backup
        )

    def test_evidence_get_controls_for_framework(self, sample_evidence_pass):
        """Should return controls for specific framework"""
        hipaa_controls = sample_evidence_pass.get_controls_for_framework(
            ComplianceFramework.HIPAA
        )
        assert "164.308(a)(7)(ii)(A)" in hipaa_controls
        assert "164.310(d)(2)(iv)" in hipaa_controls


# =============================================================================
# Test Compliance Score Calculation
# =============================================================================

class TestComplianceScoring:
    """Test compliance score calculation"""

    def test_calculate_score_all_passing(self, framework_service, sample_evidence_pass):
        """Score calculation with passing evidence"""
        evidence = [sample_evidence_pass]

        score = framework_service.calculate_compliance_score(
            ComplianceFramework.HIPAA,
            evidence
        )

        assert score.passing_controls > 0
        assert score.failing_controls == 0
        # All controls with evidence should pass
        assert score.score_percentage > 0

    def test_calculate_score_mixed_outcomes(
        self, framework_service, sample_evidence_pass, sample_evidence_fail
    ):
        """Score reflects mixed pass/fail outcomes"""
        evidence = [sample_evidence_pass, sample_evidence_fail]

        score = framework_service.calculate_compliance_score(
            ComplianceFramework.HIPAA,
            evidence
        )

        assert score.passing_controls >= 1
        assert score.failing_controls >= 1
        # Score should be between 0 and 100
        assert 0 < score.score_percentage < 100

    def test_calculate_score_latest_wins(self, framework_service):
        """Latest evidence should determine control status"""
        # Old failing evidence
        old_fail = MultiFrameworkEvidence(
            bundle_id="EB-OLD-FAIL",
            appliance_id="test-001",
            site_id="site-001",
            check_id="backup_status",
            check_type="windows",
            timestamp=datetime.utcnow() - timedelta(hours=2),
            outcome="fail",
            framework_mappings={
                ComplianceFramework.HIPAA: ["164.308(a)(7)(ii)(A)"]
            },
            raw_data={},
        )

        # New passing evidence
        new_pass = MultiFrameworkEvidence(
            bundle_id="EB-NEW-PASS",
            appliance_id="test-001",
            site_id="site-001",
            check_id="backup_status",
            check_type="windows",
            timestamp=datetime.utcnow(),
            outcome="pass",
            framework_mappings={
                ComplianceFramework.HIPAA: ["164.308(a)(7)(ii)(A)"]
            },
            raw_data={},
        )

        score = framework_service.calculate_compliance_score(
            ComplianceFramework.HIPAA,
            [old_fail, new_pass]
        )

        # Latest (passing) should win
        assert score.control_status["164.308(a)(7)(ii)(A)"] == ControlStatus.PASS

    def test_calculate_score_respects_window(self, framework_service):
        """Old evidence outside window should be ignored"""
        # Evidence from 60 days ago (outside 30-day window)
        old_evidence = MultiFrameworkEvidence(
            bundle_id="EB-OLD",
            appliance_id="test-001",
            site_id="site-001",
            check_id="backup_status",
            check_type="windows",
            timestamp=datetime.utcnow() - timedelta(days=60),
            outcome="pass",
            framework_mappings={
                ComplianceFramework.HIPAA: ["164.308(a)(7)(ii)(A)"]
            },
            raw_data={},
        )

        score = framework_service.calculate_compliance_score(
            ComplianceFramework.HIPAA,
            [old_evidence],
            evidence_window_days=30
        )

        # Control should be unknown (old evidence ignored)
        assert score.control_status["164.308(a)(7)(ii)(A)"] == ControlStatus.UNKNOWN

    def test_score_compliance_thresholds(self):
        """Score should report compliance/risk status correctly"""
        compliant_score = ComplianceScore(
            framework=ComplianceFramework.HIPAA,
            framework_name="HIPAA",
            score_percentage=85.0,
            total_controls=10,
            passing_controls=8,
            failing_controls=1,
            unknown_controls=1,
        )
        assert compliant_score.is_compliant
        assert not compliant_score.at_risk

        at_risk_score = ComplianceScore(
            framework=ComplianceFramework.HIPAA,
            framework_name="HIPAA",
            score_percentage=50.0,
            total_controls=10,
            passing_controls=5,
            failing_controls=5,
            unknown_controls=0,
        )
        assert not at_risk_score.is_compliant
        assert at_risk_score.at_risk

    def test_calculate_all_scores(
        self, framework_service, sample_evidence_pass, sample_evidence_fail
    ):
        """Should calculate scores for multiple frameworks at once"""
        evidence = [sample_evidence_pass, sample_evidence_fail]
        frameworks = [
            ComplianceFramework.HIPAA,
            ComplianceFramework.SOC2,
            ComplianceFramework.NIST_CSF,
        ]

        scores = framework_service.calculate_all_scores(frameworks, evidence)

        assert ComplianceFramework.HIPAA in scores
        assert ComplianceFramework.SOC2 in scores
        assert ComplianceFramework.NIST_CSF in scores

        for fw, score in scores.items():
            assert score.total_controls > 0


# =============================================================================
# Test Industry Recommendations
# =============================================================================

class TestIndustryRecommendations:
    """Test industry-based framework recommendations"""

    def test_healthcare_recommends_hipaa(self, framework_service):
        """Healthcare should recommend HIPAA"""
        frameworks = framework_service.get_industry_frameworks("healthcare")
        assert ComplianceFramework.HIPAA in frameworks

    def test_retail_recommends_pci(self, framework_service):
        """Retail should recommend PCI DSS"""
        frameworks = framework_service.get_industry_frameworks("retail")
        assert ComplianceFramework.PCI_DSS in frameworks

    def test_technology_recommends_soc2(self, framework_service):
        """Technology should recommend SOC 2"""
        frameworks = framework_service.get_industry_frameworks("technology")
        assert ComplianceFramework.SOC2 in frameworks

    def test_finance_recommends_multiple(self, framework_service):
        """Finance should recommend multiple frameworks"""
        frameworks = framework_service.get_industry_frameworks("finance")
        assert ComplianceFramework.SOC2 in frameworks
        assert ComplianceFramework.PCI_DSS in frameworks

    def test_unknown_industry_defaults_to_nist(self, framework_service):
        """Unknown industry should default to NIST CSF"""
        frameworks = framework_service.get_industry_frameworks("unknown_industry")
        assert ComplianceFramework.NIST_CSF in frameworks

    def test_get_recommended_frameworks_function(self):
        """Test standalone recommendation function"""
        healthcare = get_recommended_frameworks("healthcare")
        assert ComplianceFramework.HIPAA in healthcare

        general = get_recommended_frameworks("general")
        assert ComplianceFramework.NIST_CSF in general


# =============================================================================
# Test Framework Metadata
# =============================================================================

class TestFrameworkMetadata:
    """Test framework metadata functionality"""

    def test_get_hipaa_metadata(self, framework_service):
        """Should return HIPAA metadata"""
        metadata = framework_service.get_framework_metadata(ComplianceFramework.HIPAA)

        assert metadata is not None
        assert metadata.name == "HIPAA Security Rule"
        assert "2025 NPRM" in metadata.version or "2013" in metadata.version
        assert metadata.regulatory_body == "HHS/OCR"
        assert "Administrative Safeguards" in metadata.categories

    def test_get_soc2_metadata(self, framework_service):
        """Should return SOC 2 metadata"""
        metadata = framework_service.get_framework_metadata(ComplianceFramework.SOC2)

        assert metadata is not None
        assert "SOC 2" in metadata.name
        assert metadata.regulatory_body == "AICPA"
        assert "Security (CC)" in metadata.categories

    def test_get_all_metadata(self, framework_service):
        """Should return metadata for all frameworks"""
        all_metadata = framework_service.get_all_framework_metadata()

        assert len(all_metadata) >= 4  # HIPAA, SOC2, PCI, NIST at minimum
        assert ComplianceFramework.HIPAA in all_metadata
        assert ComplianceFramework.SOC2 in all_metadata


# =============================================================================
# Test Appliance Configuration
# =============================================================================

class TestApplianceConfiguration:
    """Test appliance framework configuration"""

    def test_default_config_is_hipaa(self):
        """Default config should enable HIPAA"""
        config = ApplianceFrameworkConfig(
            appliance_id="test-001",
            site_id="site-001",
        )

        assert ComplianceFramework.HIPAA in config.enabled_frameworks
        assert config.primary_framework == ComplianceFramework.HIPAA
        assert config.industry == "healthcare"

    def test_multiple_frameworks_enabled(self):
        """Should support multiple frameworks"""
        config = ApplianceFrameworkConfig(
            appliance_id="test-001",
            site_id="site-001",
            enabled_frameworks=[
                ComplianceFramework.HIPAA,
                ComplianceFramework.SOC2,
                ComplianceFramework.NIST_CSF,
            ],
            primary_framework=ComplianceFramework.HIPAA,
        )

        assert config.is_framework_enabled(ComplianceFramework.HIPAA)
        assert config.is_framework_enabled(ComplianceFramework.SOC2)
        assert config.is_framework_enabled(ComplianceFramework.NIST_CSF)
        assert not config.is_framework_enabled(ComplianceFramework.PCI_DSS)

    def test_framework_metadata_storage(self):
        """Should store framework-specific metadata"""
        config = ApplianceFrameworkConfig(
            appliance_id="test-001",
            site_id="site-001",
            enabled_frameworks=[ComplianceFramework.PCI_DSS],
            primary_framework=ComplianceFramework.PCI_DSS,
            industry="retail",
            framework_metadata={
                "pci_dss": {
                    "merchant_level": 4,
                    "saq_type": "A",
                }
            },
        )

        assert config.framework_metadata["pci_dss"]["merchant_level"] == 4


# =============================================================================
# Test Legacy Migration
# =============================================================================

class TestLegacyMigration:
    """Test backward compatibility and migration helpers"""

    def test_map_legacy_hipaa_controls(self, framework_service):
        """Should map legacy HIPAA-only controls to all frameworks"""
        # Legacy HIPAA controls from backup check
        legacy_hipaa = ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]

        mapped = framework_service.map_legacy_hipaa_controls(legacy_hipaa)

        # Should include original HIPAA
        assert ComplianceFramework.HIPAA in mapped
        assert "164.308(a)(7)(ii)(A)" in mapped[ComplianceFramework.HIPAA]

        # Should also include other frameworks
        assert ComplianceFramework.SOC2 in mapped
        assert ComplianceFramework.NIST_CSF in mapped


# =============================================================================
# Test Singleton Pattern
# =============================================================================

class TestSingleton:
    """Test global service instance"""

    def test_get_framework_service_returns_same_instance(self):
        """Should return same instance each time"""
        service1 = get_framework_service()
        service2 = get_framework_service()
        assert service1 is service2

    def test_global_service_is_loaded(self):
        """Global service should load mappings"""
        service = get_framework_service()
        assert service.is_loaded


# =============================================================================
# Test Edge Cases
# =============================================================================

class TestEdgeCases:
    """Test edge cases and error handling"""

    def test_empty_evidence_list(self, framework_service):
        """Should handle empty evidence list"""
        score = framework_service.calculate_compliance_score(
            ComplianceFramework.HIPAA,
            []
        )

        assert score.total_controls > 0
        assert score.passing_controls == 0
        assert score.score_percentage == 0

    def test_check_with_no_runbook(self, framework_service):
        """Check without runbook should still work"""
        check = framework_service.get_check_by_id("backup_status")
        # Even checks without runbook should be valid
        assert check is not None

    def test_framework_control_with_optional_subcategory(self, framework_service):
        """Control with optional subcategory should work"""
        details = framework_service.get_control_details(
            ComplianceFramework.HIPAA,
            "164.308(a)(7)(ii)(A)"
        )
        # Subcategory may or may not be set
        assert details is not None

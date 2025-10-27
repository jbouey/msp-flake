"""
Evidence Collection & Management
Hash-chained audit trail for HIPAA compliance
"""

from .evidence_writer import EvidenceWriter, EvidenceChain, write_evidence, verify_evidence_chain

__all__ = [
    "EvidenceWriter",
    "EvidenceChain",
    "write_evidence",
    "verify_evidence_chain"
]

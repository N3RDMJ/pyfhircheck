from __future__ import annotations


class PyFhircheckError(Exception):
    """Base exception for pyfhircheck operational failures."""


class ConfigError(PyFhircheckError, ValueError):
    """Invalid or unreadable validator configuration."""


class PackageError(PyFhircheckError, OSError):
    """FHIR package resolution, download, or extraction failure."""


class EvidenceError(PyFhircheckError):
    """Evidence store read or write failure."""


class ConformanceError(PyFhircheckError, ValueError):
    """Conformance test case could not be loaded or executed."""

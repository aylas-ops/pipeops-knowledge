"""Tooling for OKF v0.2 knowledge bundles.

Modules:
  model            Parser and object model for bundles and concepts.
  profile          Loader for the house profile (okf-profile.yaml).
  validate         Spec conformance + house-rule validation.
  index            Build a SQLite/FTS5 retrieval index from a bundle tree.
  query            Retrieval with metadata filters, emitting citations.
  serve            Minimal localhost retrieval endpoint for agent tools.
  extract_github   Generate draft concepts from GitHub repository metadata.
"""

OKF_VERSION = "0.2"
__version__ = "0.1.0"

"""Standalone log-message signature extraction primitive."""

from __future__ import annotations

import hashlib
import re

from src.tools.elasticsearch.shared.profiles import GenericProfile, LogInvestigationProfile


class SignatureExtractor:
    """Extracts a short deterministic signature from a log message.

    Builds a compiled variable-substitution regex from the active profile and any caller-supplied
    extra patterns. Signature is a 16-char MD5 hex digest of the normalised (lower-cased,
    variable-replaced) message.
    """

    def __init__(
        self,
        profile: LogInvestigationProfile,
        extra_variable_patterns: list[str] | None = None,
    ) -> None:
        self._extra_variable_patterns: list[str] = list(extra_variable_patterns or [])
        self.set_profile(profile)

    def set_profile(self, profile: LogInvestigationProfile) -> None:
        """Recompile all variable-substitution patterns for *profile*."""
        generic_vars = GenericProfile().variable_signature_patterns()
        profile_vars = (
            profile.variable_signature_patterns() if not isinstance(profile, GenericProfile) else []
        )
        self.variable_patterns: list[tuple[re.Pattern[str], str]] = list(generic_vars) + list(
            profile_vars
        )
        for pattern in self._extra_variable_patterns:
            self.variable_patterns.append((re.compile(pattern, re.IGNORECASE), "<VAR>"))

        if self.variable_patterns:
            combined_parts: list[str] = []
            replacement_map: dict[str, str] = {}
            for idx, (pat, replacement) in enumerate(self.variable_patterns):
                group_name = f"g{idx}"
                combined_parts.append(f"(?P<{group_name}>{pat.pattern})")
                replacement_map[group_name] = replacement
            self._combined_re: re.Pattern[str] | None = re.compile(
                "|".join(combined_parts), re.IGNORECASE
            )
            self._replacement_map: dict[str, str] = replacement_map
        else:
            self._combined_re = None
            self._replacement_map = {}

    def generate_signature(self, message: str) -> str:
        """Return a 16-char MD5 hex digest of the variable-normalised message."""
        normalized = " ".join(message.split())
        if self._combined_re is not None:
            rmap = self._replacement_map

            def _replace(m: re.Match[str]) -> str:
                # lastgroup is the name of the matched alternative (never None here).
                return rmap[m.lastgroup] if m.lastgroup else m.group(0)

            normalized = self._combined_re.sub(_replace, normalized)
        normalized = normalized.lower()
        # md5 is used only as a fast non-cryptographic content fingerprint for clustering.
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:16]

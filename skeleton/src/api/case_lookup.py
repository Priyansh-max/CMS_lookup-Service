"""Case lookup service and ranking logic."""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from src.models.canonical import CaseRecord, CaseSearchQuery, normalize_name
from src.storage.base import CaseRepository


@dataclass(slots=True)
class CaseLookupMatch:
    """Ranked lookup result returned to the API layer."""

    case: CaseRecord
    score: float
    match_type: str


class CaseLookupService:
    """Find and rank likely case matches for noisy voice-agent input."""

    def __init__(self, repository: CaseRepository, *, min_score: float = 30.0):
        self.repository = repository
        self.min_score = min_score

    async def lookup_by_name(self, name: str, firm_id: str) -> list[CaseLookupMatch]:
        normalized_query = normalize_name(name)
        if not normalized_query:
            return []

        candidates = await self.repository.find_candidates_by_name(
            CaseSearchQuery(firm_id=firm_id, name=normalized_query, limit=10)
        )

        ranked_results: list[CaseLookupMatch] = []
        for candidate in candidates:
            normalized_candidate = candidate.normalized_client_name

            # Exact normalized matches should always beat fuzzy matches because
            # they are the safest answer for a live voice-agent lookup.
            if normalized_candidate == normalized_query:
                ranked_results.append(
                    CaseLookupMatch(case=candidate, score=100.0, match_type="exact")
                )
                continue

            score = float(fuzz.token_sort_ratio(normalized_query, normalized_candidate))
            if score >= self.min_score:
                ranked_results.append(
                    CaseLookupMatch(case=candidate, score=score, match_type="fuzzy")
                )

        ranked_results.sort(
            key=lambda item: (
                item.score,
                item.case.updated_at.isoformat() if item.case.updated_at else "",
            ),
            reverse=True,
        )
        return ranked_results[:5]

"""LLM katmanı için istisnalar."""


class QuotaExceededError(Exception):
    """Gemini/OpenAI dahil API kota veya rate limit (429) durumunda."""

from pydantic import BaseModel


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ServiceUsage(BaseModel):
    """Aggregated consumption + derived cost for one metered service over the filtered window."""

    service: str  # "llm" | "stt" | "tts" | "ocr"
    # Raw quantities (only the ones meaningful for the service are non-zero):
    input_tokens: int = 0
    output_tokens: int = 0
    minutes: float = 0.0
    characters: int = 0
    pages: int = 0
    events: int = 0  # number of usage rows aggregated
    cost_inr: float = 0.0


class PricingOut(BaseModel):
    """The env-configured rates (₹) the costs were derived from — shown for transparency."""

    llm_per_mtok_input: float
    llm_per_mtok_output: float
    stt_per_minute: float
    tts_per_1k_chars: float
    ocr_per_1k_pages: float


class UsageReport(BaseModel):
    start: str | None  # echo of the applied date filter (ISO), None = unbounded
    end: str | None
    services: list[ServiceUsage]  # always all four, in a stable order
    total_cost_inr: float
    pricing: PricingOut

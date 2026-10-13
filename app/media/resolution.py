@dataclass(frozen=True)
class ResolvedImageRequest:
    description: str
    interface_level: str

    quality: str
    aspect_ratio: str

    layout_type: str
    panel_count: int | None
    arrangement: str | None

    references: list[dict]

    provider: str
    model: str
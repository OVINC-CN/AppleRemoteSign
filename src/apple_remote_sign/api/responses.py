from fastapi.responses import RedirectResponse


class ArtifactResponse(RedirectResponse):
    """Issue a fresh, uncacheable CDN redirect for every authorized download."""

    def __init__(self, url: str) -> None:
        super().__init__(url, status_code=302, headers={"Cache-Control": "no-store"})

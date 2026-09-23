from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .models import CatalogueOptions, RecommendationRequest, RecommendationResponse
from .service import catalogue_options, recommend


app = FastAPI(
    title="Умный подбор event-подрядчиков",
    description="До трёх проверяемых рекомендаций из каталога HackAlem AI.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/catalogue/options", response_model=CatalogueOptions)
def options() -> CatalogueOptions:
    return catalogue_options()


@app.post("/recommendations", response_model=RecommendationResponse)
def recommendations(request: RecommendationRequest) -> RecommendationResponse:
    try:
        return recommend(request)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

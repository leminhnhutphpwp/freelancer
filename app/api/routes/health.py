from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/live")
async def live() -> dict:
    return {"status": "ok"}

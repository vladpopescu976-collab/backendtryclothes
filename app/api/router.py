from fastapi import APIRouter

from app.api.routes import auth, brands, fit, health, stylist, tryon, users

api_router = APIRouter()
api_router.include_router(health.router, tags=["Health"])
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(users.router, tags=["Users"])
api_router.include_router(brands.router, prefix="/brands", tags=["Brands"])
api_router.include_router(fit.router, prefix="/fit", tags=["Fit"])
api_router.include_router(stylist.router, prefix="/stylist", tags=["Stylist"])
api_router.include_router(tryon.router, prefix="/tryon", tags=["TryOn"])

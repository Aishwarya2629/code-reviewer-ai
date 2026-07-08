from fastapi import APIRouter
from app.api.v1.routes import review, problem, image, health, jobs, webhooks, analytics

router = APIRouter(prefix="/api/v1")
router.include_router(review.router,    tags=["Review"])
router.include_router(problem.router,   tags=["Problem Solver"])
router.include_router(image.router,     tags=["Image"])
router.include_router(health.router,    tags=["Health"])
router.include_router(jobs.router,      tags=["Async Jobs"])
router.include_router(webhooks.router,  tags=["Webhooks"])
router.include_router(analytics.router, tags=["Analytics"])

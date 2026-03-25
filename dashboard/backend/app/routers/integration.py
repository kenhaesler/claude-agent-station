"""Integration branch feature tracking and promotion endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import IntegrationFeature, QueueItem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integration", tags=["integration"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _feature_to_dict(f: IntegrationFeature) -> dict:
    """Convert an IntegrationFeature row to a JSON-serializable dict."""
    return {
        "id": f.id,
        "project_repo": f.project_repo,
        "issue_number": f.issue_number,
        "issue_title": f.issue_title,
        "branch": f.branch,
        "state": f.state,
        "merge_commit": f.merge_commit,
        "validation_status": f.validation_status,
        "validation_output": f.validation_output,
        "pr_number": f.pr_number,
        "run_id": f.run_id,
        "promotion_run_id": f.promotion_run_id,
        "excluded_reason": f.excluded_reason,
        "created_at": f.created_at.isoformat() if f.created_at else None,
        "updated_at": f.updated_at.isoformat() if f.updated_at else None,
    }


# ---------------------------------------------------------------------------
# GET endpoints
# ---------------------------------------------------------------------------

@router.get("/status/{repo:path}")
async def get_integration_status(
    repo: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return dev branch status summary for a project repo."""
    base = select(IntegrationFeature).where(IntegrationFeature.project_repo == repo)

    total_result = await db.execute(
        select(func.count(IntegrationFeature.id)).where(
            IntegrationFeature.project_repo == repo,
        )
    )
    feature_count = total_result.scalar() or 0

    validated_result = await db.execute(
        select(func.count(IntegrationFeature.id)).where(
            IntegrationFeature.project_repo == repo,
            IntegrationFeature.state == "validated",
        )
    )
    validated_count = validated_result.scalar() or 0

    conflict_result = await db.execute(
        select(func.count(IntegrationFeature.id)).where(
            IntegrationFeature.project_repo == repo,
            IntegrationFeature.validation_status == "conflict",
        )
    )
    conflict_count = conflict_result.scalar() or 0

    # Most recent validation timestamp
    last_val_result = await db.execute(
        base.where(
            IntegrationFeature.validation_status.isnot(None),
        ).order_by(IntegrationFeature.updated_at.desc()).limit(1)
    )
    last_validated = last_val_result.scalar_one_or_none()
    last_validation = (
        last_validated.updated_at.isoformat() if last_validated and last_validated.updated_at else None
    )
    validation_status = last_validated.validation_status if last_validated else None

    return {
        "project_repo": repo,
        "dev_branch": "dev",
        "feature_count": feature_count,
        "validated_count": validated_count,
        "conflict_count": conflict_count,
        "last_validation": last_validation,
        "validation_status": validation_status,
    }


@router.get("/features")
async def list_features(
    project_repo: str | None = Query(None),
    state: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List integration features with optional filters."""
    q = select(IntegrationFeature)
    count_q = select(func.count(IntegrationFeature.id))

    if project_repo:
        q = q.where(IntegrationFeature.project_repo == project_repo)
        count_q = count_q.where(IntegrationFeature.project_repo == project_repo)
    if state:
        q = q.where(IntegrationFeature.state == state)
        count_q = count_q.where(IntegrationFeature.state == state)

    q = q.order_by(IntegrationFeature.created_at.desc())
    q = q.offset(offset).limit(limit)

    result = await db.execute(q)
    items = result.scalars().all()
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    return {"items": [_feature_to_dict(f) for f in items], "total": total}


@router.get("/features/{feature_id}")
async def get_feature(
    feature_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get a single integration feature by ID."""
    result = await db.execute(
        select(IntegrationFeature).where(IntegrationFeature.id == feature_id)
    )
    feature = result.scalar_one_or_none()
    if not feature:
        raise HTTPException(404, "Integration feature not found")
    return _feature_to_dict(feature)


# ---------------------------------------------------------------------------
# POST / PUT endpoints
# ---------------------------------------------------------------------------

class FeatureCreate(BaseModel):
    project_repo: str
    branch: str
    issue_number: int | None = None
    issue_title: str | None = None
    state: str = "merged_to_dev"
    merge_commit: str | None = None
    run_id: str | None = None


@router.post("/features", status_code=201)
async def create_feature(
    data: FeatureCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Record a new feature merged into the integration branch."""
    feature = IntegrationFeature(
        project_repo=data.project_repo,
        branch=data.branch,
        issue_number=data.issue_number,
        issue_title=data.issue_title or None,
        state=data.state,
        merge_commit=data.merge_commit,
        run_id=data.run_id,
    )
    db.add(feature)
    await db.commit()
    await db.refresh(feature)

    logger.info(
        "Integration feature %d created: %s branch=%s state=%s",
        feature.id, feature.project_repo, feature.branch, feature.state,
    )

    # Cross-table coordination: auto-complete matching queue item
    if data.issue_number is not None:
        from app.routers.queue import ACTIVE_STATES

        qi_result = await db.execute(
            select(QueueItem).where(
                QueueItem.project_repo == data.project_repo,
                QueueItem.issue_number == data.issue_number,
                QueueItem.state.in_(ACTIVE_STATES),
            )
        )
        qi = qi_result.scalar_one_or_none()
        if qi:
            now = datetime.now(timezone.utc)
            # Walk through valid transitions to completed
            transition_path = {
                "pending": ["assigned", "in_progress", "review", "approved", "completed"],
                "claimed": ["in_progress", "review", "approved", "completed"],
                "assigned": ["in_progress", "review", "approved", "completed"],
                "planning": ["in_progress", "review", "approved", "completed"],
                "in_progress": ["review", "approved", "completed"],
                "review": ["approved", "completed"],
                "verifying": ["approved", "completed"],
                "approved": ["completed"],
            }
            for next_state in transition_path.get(qi.state, []):
                qi.state = next_state
                qi.updated_at = now
                if next_state in ("assigned", "claimed"):
                    qi.assigned_at = qi.assigned_at or now
                elif next_state == "in_progress":
                    qi.started_at = qi.started_at or now
                elif next_state == "completed":
                    qi.completed_at = now
            await db.commit()
            logger.info(
                "Auto-completed queue item %d (issue #%d) via integration feature creation",
                qi.id, data.issue_number,
            )

    return _feature_to_dict(feature)


class FeatureUpdate(BaseModel):
    state: str | None = None
    validation_status: str | None = None
    validation_output: str | None = None
    pr_number: int | None = None
    promotion_run_id: str | None = None
    excluded_reason: str | None = None


@router.put("/features/{feature_id}")
async def update_feature(
    feature_id: int,
    data: FeatureUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update an integration feature's state or metadata."""
    result = await db.execute(
        select(IntegrationFeature).where(IntegrationFeature.id == feature_id)
    )
    feature = result.scalar_one_or_none()
    if not feature:
        raise HTTPException(404, "Integration feature not found")

    for field in data.model_fields_set:
        setattr(feature, field, getattr(data, field))

    feature.updated_at = _utcnow()
    await db.commit()
    await db.refresh(feature)

    logger.info("Integration feature %d updated: state=%s", feature.id, feature.state)
    return _feature_to_dict(feature)


# ---------------------------------------------------------------------------
# Action endpoints (placeholders for shell-script triggers)
# ---------------------------------------------------------------------------

class PromoteRequest(BaseModel):
    project_repo: str
    feature_ids: list[int] | None = None
    strategy: str | None = None


@router.post("/promote")
async def trigger_promote(data: PromoteRequest) -> dict:
    """Trigger promotion of validated features to main.

    This is a lightweight trigger -- actual git operations happen in promote.sh.
    """
    logger.info(
        "Promotion triggered for %s (features=%s, strategy=%s)",
        data.project_repo, data.feature_ids, data.strategy,
    )
    return {
        "status": "triggered",
        "message": f"Promotion triggered for {data.project_repo}. "
                   "The promote.sh script will handle the actual git operations.",
    }


@router.post("/sync/{repo:path}")
async def trigger_sync(repo: str) -> dict:
    """Trigger dev branch sync for a project repo (placeholder)."""
    logger.info("Sync triggered for %s", repo)
    return {
        "status": "triggered",
        "message": f"Sync triggered for {repo}.",
    }


@router.post("/validate/{repo:path}")
async def trigger_validate(repo: str) -> dict:
    """Trigger validation of the dev branch for a project repo (placeholder)."""
    logger.info("Validation triggered for %s", repo)
    return {
        "status": "triggered",
        "message": f"Validation triggered for {repo}.",
    }


# ---------------------------------------------------------------------------
# Exclude / re-include
# ---------------------------------------------------------------------------

class ExcludeRequest(BaseModel):
    reason: str


@router.post("/exclude/{feature_id}")
async def exclude_feature(
    feature_id: int,
    data: ExcludeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Exclude a feature from the next promotion."""
    result = await db.execute(
        select(IntegrationFeature).where(IntegrationFeature.id == feature_id)
    )
    feature = result.scalar_one_or_none()
    if not feature:
        raise HTTPException(404, "Integration feature not found")

    if feature.state == "excluded":
        raise HTTPException(400, "Feature is already excluded")

    feature.state = "excluded"
    feature.excluded_reason = data.reason
    feature.updated_at = _utcnow()
    await db.commit()
    await db.refresh(feature)

    logger.info("Integration feature %d excluded: %s", feature.id, data.reason)
    return _feature_to_dict(feature)


@router.delete("/exclude/{feature_id}")
async def reinclude_feature(
    feature_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Re-include a previously excluded feature."""
    result = await db.execute(
        select(IntegrationFeature).where(IntegrationFeature.id == feature_id)
    )
    feature = result.scalar_one_or_none()
    if not feature:
        raise HTTPException(404, "Integration feature not found")

    if feature.state != "excluded":
        raise HTTPException(400, f"Feature is not excluded (current state: {feature.state})")

    # Restore to a sensible default state
    feature.state = "merged_to_dev" if not feature.validation_status else "validated"
    feature.excluded_reason = None
    feature.updated_at = _utcnow()
    await db.commit()
    await db.refresh(feature)

    logger.info("Integration feature %d re-included, state=%s", feature.id, feature.state)
    return _feature_to_dict(feature)

"""CRUD endpoints for implementation plans."""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Plan, Project
from app.schemas import PlanCreate, PlanUpdate, PlanOut, PlanList

router = APIRouter(prefix="/api/plans", tags=["plans"])


@router.get("", response_model=PlanList)
async def list_plans(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    project_id: Optional[int] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> PlanList:
    query = select(Plan)
    count_query = select(func.count(Plan.id))

    if project_id is not None:
        query = query.where(Plan.project_id == project_id)
        count_query = count_query.where(Plan.project_id == project_id)
    if status:
        query = query.where(Plan.status == status)
        count_query = count_query.where(Plan.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(desc(Plan.created_at)).offset(offset).limit(limit)
    result = await db.execute(query)
    plans = result.scalars().all()

    return PlanList(plans=plans, total=total or 0)


@router.get("/{plan_id}", response_model=PlanOut)
async def get_plan(plan_id: int, db: AsyncSession = Depends(get_db)) -> Plan:
    result = await db.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


@router.post("", response_model=PlanOut, status_code=201)
async def create_plan(data: PlanCreate, db: AsyncSession = Depends(get_db)) -> Plan:
    # Verify project exists
    proj = await db.execute(select(Project).where(Project.id == data.project_id))
    if not proj.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    plan = Plan(**data.model_dump())
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


@router.put("/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: int,
    data: PlanUpdate,
    db: AsyncSession = Depends(get_db),
) -> Plan:
    result = await db.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(plan, key, value)

    await db.commit()
    await db.refresh(plan)
    return plan


@router.delete("/{plan_id}", status_code=204)
async def delete_plan(plan_id: int, db: AsyncSession = Depends(get_db)) -> None:
    result = await db.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    await db.delete(plan)
    await db.commit()


@router.post("/{plan_id}/approve", response_model=PlanOut)
async def approve_plan(plan_id: int, db: AsyncSession = Depends(get_db)) -> Plan:
    """Mark a plan as approved and ready for implementation."""
    result = await db.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.status not in ("draft", "rejected"):
        raise HTTPException(status_code=400, detail=f"Cannot approve plan with status '{plan.status}'")

    plan.status = "approved"
    await db.commit()
    await db.refresh(plan)
    return plan


@router.post("/{plan_id}/reject", response_model=PlanOut)
async def reject_plan(plan_id: int, db: AsyncSession = Depends(get_db)) -> Plan:
    """Reject a plan."""
    result = await db.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    plan.status = "rejected"
    await db.commit()
    await db.refresh(plan)
    return plan

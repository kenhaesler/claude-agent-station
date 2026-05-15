from __future__ import annotations

"""CRUD endpoints for projects. Mutations sync back to config JSON."""


from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Plan, Project, Run
from app.schemas import ProjectCreate, ProjectOut, ProjectUpdate
from app.services.config_sync import sync_db_to_config

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).order_by(Project.id))
    return result.scalars().all()


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(data: ProjectCreate, db: AsyncSession = Depends(get_db)):
    # Check for duplicate repo
    result = await db.execute(select(Project).where(Project.repo == data.repo))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Project with this repo already exists")

    # Pattern: flush → sync → commit. The sync writes the JSON file and
    # raises if the path isn't writable; we want that failure to roll back
    # the row insertion so DB and JSON stay consistent. flush() makes the
    # pending INSERT visible to the SELECT inside sync_db_to_config without
    # finalizing the transaction.
    project = Project(**data.model_dump())
    db.add(project)
    try:
        await db.flush()
        await sync_db_to_config(db)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    await db.refresh(project)
    return project


async def _apply_project_update(
    project_id: int, data: ProjectUpdate, db: AsyncSession,
) -> Project:
    """Shared body for PUT and PATCH.

    Both verbs accept partial updates via ``exclude_unset=True`` —
    PUT-as-replace is not a meaningful contract here because the
    ``ProjectUpdate`` schema's fields are all ``Optional``. The two
    routes share this body verbatim; keeping them as separate
    decorators preserves the externally-advertised PATCH verb without
    duplicating the implementation.
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(project, key, value)

    try:
        await db.flush()
        await sync_db_to_config(db)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    await db.refresh(project)
    return project


@router.put("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    data: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
):
    return await _apply_project_update(project_id, data, db)


@router.patch("/{project_id}", response_model=ProjectOut)
async def patch_project(
    project_id: int,
    data: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Partial update — identical semantics to PUT but explicit PATCH verb."""
    return await _apply_project_update(project_id, data, db)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Nullify project_id on related runs (preserve history)
    await db.execute(
        sa_update(Run).where(Run.project_id == project_id).values(project_id=None)
    )
    # Delete related plans
    await db.execute(
        sa_delete(Plan).where(Plan.project_id == project_id)
    )

    await db.delete(project)
    try:
        await db.flush()
        await sync_db_to_config(db)
        await db.commit()
    except Exception:
        await db.rollback()
        raise

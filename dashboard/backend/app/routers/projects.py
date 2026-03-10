"""CRUD endpoints for projects. Mutations sync back to config JSON."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Project
from app.schemas import ProjectCreate, ProjectUpdate, ProjectOut
from app.services.config_sync import sync_db_to_config

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=List[ProjectOut])
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

    project = Project(**data.model_dump())
    db.add(project)
    await db.commit()
    await db.refresh(project)

    # Sync to config JSON
    await sync_db_to_config(db)
    return project


@router.put("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    data: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(project, key, value)

    await db.commit()
    await db.refresh(project)

    # Sync to config JSON
    await sync_db_to_config(db)
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await db.delete(project)
    await db.commit()

    # Sync to config JSON
    await sync_db_to_config(db)

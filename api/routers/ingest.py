from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import json

from api.core.database import get_db
from api.models.task import IngestTask

router = APIRouter(prefix="/ingest", tags=["ingest"])

class IngestRequest(BaseModel):
    limit: int = 0
    force: bool = False
    skip_ddb: bool = False

@router.post("/start")
def start_ingest(request: IngestRequest, db: Session = Depends(get_db)):
    existing_task = db.query(IngestTask).filter(
        IngestTask.status.in_(["pending", "running"])
    ).first()
    
    if existing_task:
        raise HTTPException(status_code=400, detail="A task is already running or pending.")
    
    new_task = IngestTask(
        task_type="ingest",
        status="pending",
        params=json.dumps(request.model_dump())
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    
    return {"message": "Ingest task queued successfully", "task_id": new_task.id}

@router.post("/start_vectorize")
def start_vectorize(db: Session = Depends(get_db)):
    existing_task = db.query(IngestTask).filter(
        IngestTask.status.in_(["pending", "running"])
    ).first()
    
    if existing_task:
        raise HTTPException(status_code=400, detail="A task is already running or pending.")
    
    new_task = IngestTask(
        task_type="vectorize",
        status="pending",
        params="{}"
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    
    return {"message": "Vectorize task queued successfully", "task_id": new_task.id}

@router.get("/status")
def get_ingest_status(db: Session = Depends(get_db)):
    # Return the latest task
    task = db.query(IngestTask).order_by(IngestTask.created_at.desc()).first()
    if not task:
        return {"status": "idle"}
    
    return {
        "task_id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "created_at": task.created_at,
        "completed_at": task.completed_at
    }
    
    return {
        "task_id": task.id,
        "status": task.status,
        "created_at": task.created_at,
        "completed_at": task.completed_at
    }

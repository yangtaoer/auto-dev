"""HTTP adapters for business logic diagram generation and notifications."""
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .main import RunnerIdentity, can_access_request, current_user, learning_call, runner_auth
from .services import business_logic_diagrams as diagrams


router = APIRouter()


class DiagramRequest(BaseModel):
    retry_mail: bool = False


class DiagramClaim(RunnerIdentity):
    claim_token: str = Field(min_length=1, max_length=80)


class DiagramProgress(DiagramClaim):
    progress: str = Field(default="", max_length=500)
    error_message: str = Field(default="", max_length=2000)


@router.post("/api/requests/{request_id}/business-logic-diagram")
def request_diagram(request_id: str, payload: DiagramRequest, user: Annotated[dict, Depends(current_user)]) -> dict:
    can_access_request(user, request_id, allow_acceptance_owner=True)
    return {"job": learning_call(diagrams.queue, request_id, user, retry_mail=payload.retry_mail)}


@router.post("/api/runner/business-logic-diagrams/claim", dependencies=[Depends(runner_auth)])
def claim_diagram(payload: RunnerIdentity) -> dict:
    return {"job": diagrams.claim(payload.runner_id)}


@router.patch("/api/runner/business-logic-diagrams/{request_id}", dependencies=[Depends(runner_auth)])
def diagram_progress(request_id: str, payload: DiagramProgress) -> dict:
    return {"job": learning_call(diagrams.generation_update, request_id, payload.runner_id, payload.claim_token,
                                 progress=payload.progress, error_message=payload.error_message)}


@router.post("/api/runner/business-logic-diagrams/{request_id}/complete", dependencies=[Depends(runner_auth)])
async def complete_diagram(
    request_id: str,
    runner_id: Annotated[str, Form(min_length=1, max_length=80)],
    claim_token: Annotated[str, Form(min_length=1, max_length=80)],
    explanation: Annotated[str, Form(min_length=1, max_length=1800)],
    file: Annotated[UploadFile, File()],
) -> dict:
    try:
        raw = await file.read(diagrams.MAX_IMAGE_BYTES + 1)
    finally:
        await file.close()
    if len(raw) > diagrams.MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="图片超过交付容量限制")
    job = await run_in_threadpool(learning_call, diagrams.complete, request_id, runner_id, claim_token, explanation, raw)
    return {"job": job}


@router.post("/api/runner/business-logic-diagrams/{request_id}/notify", dependencies=[Depends(runner_auth)])
def notify_diagram(request_id: str, payload: DiagramClaim) -> dict:
    return {"job": learning_call(diagrams.send_claimed, request_id, payload.runner_id, payload.claim_token)}

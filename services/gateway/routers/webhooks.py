# Inbound webhook endpoints: delivery receipts from WhatsApp/Twilio/SendGrid,
# and transaction event pushes from the Core Banking System (CBS).

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.session import get_db
from services.notifications.tracker import process_delivery_receipt

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/delivery", status_code=status.HTTP_200_OK)
async def delivery_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Receives delivery receipts from WhatsApp, Twilio, and SendGrid,
    and updates outreach campaign delivery timestamps.
    """
    try:
        # Check if request has JSON payload
        payload = await request.json()
    except Exception:
        # Fallback to form URL-encoded data
        try:
            form_data = await request.form()
            payload = dict(form_data)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid request body")

    success = await process_delivery_receipt(payload, db)
    if not success:
        return {
            "status": "ignored",
            "message": "Payload parsed but not matched to any campaign or event ignored",
        }

    return {"status": "success", "message": "Delivery status updated"}

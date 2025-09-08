from pydantic import BaseModel, EmailStr, Field
from typing import List, Literal, Optional, Any, Dict

class ChatMessage(BaseModel):
    role: Literal["system","user","assistant","tool"]
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    lead_opt_in: bool = False
    email: Optional[EmailStr] = None

class ChatResponse(BaseModel):
    message: ChatMessage

class LeadRequest(BaseModel):
    email: EmailStr
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    phone: Optional[str] = None
    context: Optional[str] = None

class LeadResponse(BaseModel):
    status: str
    hubspot_contact_id: Optional[str] = None
    detail: Optional[str] = None

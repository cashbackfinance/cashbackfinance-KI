from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI
from settings import Settings, get_allowed_origins_list

def attach_cors(app: FastAPI, settings: Settings):
    if settings.ALLOWED_ORIGINS == "*":
        allow_origins = ["*"]
    else:
        allow_origins = get_allowed_origins_list(settings.ALLOWED_ORIGINS)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

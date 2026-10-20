from pydantic import BaseModel, Field


class InfoRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class DownloadRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    quality: str = Field(pattern="^(best|2160|1440|1080|720|480|360)$")

from pydantic import BaseModel, Field

class ProviderKeyIn(BaseModel):
    api_key: str = Field(min_length=3)
    base_url: str | None = None

class ChatCreate(BaseModel):
    provider: str
    model: str

class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=100000)

class ChatUpdate(BaseModel):
    provider: str | None = None
    model: str | None = None
    title: str | None = None

class VideoAnalyzeIn(BaseModel):
    url: str = Field(min_length=10, max_length=4000)
    provider: str
    model: str

class VideoProxyIn(BaseModel):
    proxy_url: str = Field(min_length=3, max_length=1000)


class FilmProjectCreate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    original_text: str = Field(min_length=20, max_length=500000)
    provider: str
    model: str
    settings: dict = Field(default_factory=dict)

class FilmProjectUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    settings: dict | None = None

class FilmSceneUpdate(BaseModel):
    title: str | None = None
    source_text: str | None = None
    summary: str | None = None
    duration: float | None = Field(default=None, ge=1, le=60)
    characters: list[str] | None = None
    location_id: str | None = None
    action: str | None = None
    camera: str | None = None
    lighting: str | None = None
    atmosphere: str | None = None
    voiceover: str | None = None
    dialogue: list[dict] | None = None
    start_state: str | None = None
    end_state: str | None = None
    visual_prompt: str | None = None
    flow_prompt: str | None = None


class FilmRenderQueueIn(BaseModel):
    scene_ids: list[str] | None = None

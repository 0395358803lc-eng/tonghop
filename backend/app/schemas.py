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


class FilmPipelineStartIn(BaseModel):
    from_scene_id: str | None = None
    scene_limit: int | None = Field(default=None, ge=1, le=200)


class FilmResourceBindingIn(BaseModel):
    provider_ref: str | None = Field(default=None, max_length=1000)
    status: str | None = None
    error: str | None = Field(default=None, max_length=2000)
    metadata: dict | None = None


class FilmResourceAssetIn(BaseModel):
    data_url: str = Field(min_length=32, max_length=25_000_000)
    filename: str | None = Field(default=None, max_length=255)


class FilmCanonicalGenerateIn(BaseModel):
    resource_type: str | None = None
    entity_ids: list[str] | None = None
    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=120)


class FilmCanonicalQcIn(BaseModel):
    resource_type: str | None = None
    entity_ids: list[str] | None = None
    auto_repair: bool = False
    repair_provider: str | None = Field(default="flow", max_length=32)
    repair_model: str | None = Field(default=None, max_length=120)


class FilmNarratorUpgradeIn(BaseModel):
    voice_id: str = Field(default="confident-male-vietnamese", min_length=2, max_length=120)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class FlowBridgeSettingsIn(BaseModel):
    bridge_url: str = Field(min_length=12, max_length=500)
    api_key: str = Field(min_length=16, max_length=500)
    enabled: bool = True

class FlowSessionSaveIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)

class FlowSessionNewIn(BaseModel):
    save_current: bool = True
    name: str | None = Field(default=None, max_length=120)


class FilmMediaSelectIn(BaseModel):
    confirm: bool = True


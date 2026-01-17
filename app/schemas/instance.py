from pydantic import BaseModel


class InstanceConfigResponse(BaseModel):
    """Public instance configuration safe for frontend consumption."""

    import_export_max_file_size_mb: int
    disable_signup: bool

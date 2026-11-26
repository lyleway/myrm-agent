"""[INPUT]
- app.channels.voice.stt.core::transcribe / download_audio (POS: voice stt 子包的一部分；由 __init__.py 聚合导出。)
- app.channels.voice.stt.local::get_local_status / is_local_available / local_stt_unavailable_detail (POS: voice stt 子包的一部分；由 __init__.py 聚合导出。)
- app.channels.voice.stt.models::default_stt_model (POS: voice stt 子包的一部分；由 __init__.py 聚合导出。)

[OUTPUT]
- transcribe
- download_audio
- get_local_status
- is_local_available
- local_stt_unavailable_detail
- default_stt_model

[POS]
`app.channels.voice.stt` 的唯一入口；实现按 common/models/local/providers/core 分层。
"""

from app.channels.voice.stt.core import download_audio, transcribe
from app.channels.voice.stt.local import get_local_status, is_local_available, local_stt_unavailable_detail
from app.channels.voice.stt.models import default_stt_model

__all__ = [
    "default_stt_model",
    "download_audio",
    "get_local_status",
    "is_local_available",
    "local_stt_unavailable_detail",
    "transcribe",
]

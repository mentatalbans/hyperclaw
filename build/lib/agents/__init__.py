# Assistant Agent System
"""
HyperClaw Agents - Background monitoring, planning, transcription, and learning.
"""

try:
    from .background_monitor import agent_start, agent_stop, agent_status, agent_add_watch
except ImportError:
    agent_start = agent_stop = agent_status = agent_add_watch = None

try:
    from .planner import plan_create, plan_add_task, plan_status, plan_next, plan_update, plan_list, plan_delete
except ImportError:
    plan_create = plan_add_task = plan_status = plan_next = plan_update = plan_list = plan_delete = None

try:
    from .transcriber import audio_transcribe, video_transcribe, audio_record, audio_record_transcribe, transcripts_list, transcript_get
except ImportError:
    audio_transcribe = video_transcribe = audio_record = audio_record_transcribe = transcripts_list = transcript_get = None

try:
    from .learning import learning_log, learning_stats, learning_patterns, learning_reflect, learning_advice, learning_feedback
except ImportError:
    learning_log = learning_stats = learning_patterns = learning_reflect = learning_advice = learning_feedback = None

__all__ = [
    "agent_start", "agent_stop", "agent_status", "agent_add_watch",
    "plan_create", "plan_add_task", "plan_status", "plan_next", "plan_update", "plan_list", "plan_delete",
    "audio_transcribe", "video_transcribe", "audio_record", "audio_record_transcribe", "transcripts_list", "transcript_get",
    "learning_log", "learning_stats", "learning_patterns", "learning_reflect", "learning_advice", "learning_feedback",
]

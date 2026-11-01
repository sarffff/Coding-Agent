import pytest
from pydantic import ValidationError

from app.models import (
    AgentAdvanceRequest, AgentLoopRequest, ChatMessageRequest, CommitCreateRequest,
    CommitPreviewRequest, DraftPatchRequest, IterationCreateRequest, RepairCreateRequest,
    TaskCreateRequest,
)


@pytest.mark.parametrize("request_type,field,limit,extra", [
    (TaskCreateRequest, "goal", 32_000, {}),
    (ChatMessageRequest, "message", 32_000, {}),
    (AgentLoopRequest, "feedback", 32_000, {}),
    (AgentAdvanceRequest, "feedback", 32_000, {}),
    (AgentAdvanceRequest, "goal", 32_000, {}),
    (IterationCreateRequest, "goal", 32_000, {}),
    (RepairCreateRequest, "feedback", 32_000, {}),
    (DraftPatchRequest, "feedback", 32_000, {}),
    (CommitPreviewRequest, "message", 2_000, {}),
    (CommitCreateRequest, "message", 2_000, {"scope_hash": "a" * 64}),
])
def test_input_limits_match_across_request_stages(request_type, field, limit, extra):
    # The same accepted text must survive creation, loop advancement and drafting.
    text = "末" * limit
    assert getattr(request_type(**extra, **{field: text}), field) == text
    with pytest.raises(ValidationError) as error:
        request_type(**extra, **{field: text + "界"})
    assert error.value.errors()[0]["type"] == "string_too_long"

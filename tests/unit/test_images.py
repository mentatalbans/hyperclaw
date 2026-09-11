import base64

import pytest
from pydantic import ValidationError
from hyperclaw.contracts import ImageAttachment, RunRequest


from tests.support.images import png


def test_image_current_request_preserves_bytes_and_rejects_wrong_type_or_budget():
    data = base64.b64encode(png()).decode()
    image = ImageAttachment(media_type='image/png',data=data)
    request = RunRequest(session_id='s',generation=0,request_id='r',text='color?',images=(image,))
    assert request.current_message().content[1]['source'] == {'type':'base64','media_type':'image/png','data':data}
    for media, bad in [('image/png','not base64'), ('image/jpeg',data)]:
        with pytest.raises(ValidationError):
            ImageAttachment(media_type=media,data=bad)
    oversized = ImageAttachment(media_type='image/png',data=base64.b64encode(png()+b'\0'*65536).decode())
    with pytest.raises(ValidationError):
        request.model_validate(dict(request.model_dump(),images=[oversized.model_dump()]))
    larger = request.model_validate(dict(request.model_dump(),images=[oversized.model_dump()],context_bytes=131072))
    assert larger.images[0].data == oversized.data

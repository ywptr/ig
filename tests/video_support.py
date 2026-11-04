MP4 = b"\x00\x00\x00\x18ftypisom" + b"test-video" * 8
TASK_ID = "task-123"
REQUEST_ID = "12345678-1234-1234-1234-123456789abc"
TEMP_VIDEO_URL = "https://video.oss-ap-southeast-1.aliyuncs.com/result.mp4?signature=temporary-secret"
TEST_KEY = "fake-alibaba-key-for-tests"
BASE_URL = "https://test-workspace.ap-southeast-1.maas.aliyuncs.com"


class HTTPResponse:
    def __init__(self, body=None, *, status=200, chunks=None, headers=None):
        self.body = body
        self.status_code = status
        self.headers = headers or {"Content-Type": "video/mp4"}
        self.chunks = [MP4] if chunks is None else chunks
        self.iterated = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def json(self):
        return self.body

    @property
    def content(self):
        raise AssertionError("Video downloads must be incremental")

    def iter_content(self, chunk_size):
        self.iterated = True
        assert chunk_size == 64 * 1024
        yield from self.chunks


def operation_response(status="PENDING", **output):
    return HTTPResponse({
        "request_id": REQUEST_ID,
        "output": {"task_id": TASK_ID, "task_status": status, **output},
        "usage": {"video_count": 1, "output_video_duration": 5, "fps": 30,
                  "private_url": TEMP_VIDEO_URL},
        "unfiltered_secret": TEST_KEY,
    })

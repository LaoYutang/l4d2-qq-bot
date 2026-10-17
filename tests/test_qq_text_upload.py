import base64
from unittest.mock import AsyncMock

import pytest

from l4d2_qq_bot.qq.client import QQAPIError, QQClient


def api():
    return QQClient(AsyncMock(), app_id="fixture-app", client_secret="fixture-secret")


async def test_txt_upload_is_inline_utf8_with_a_safe_fixed_filename():
    client = api()
    client.request = AsyncMock(return_value={"file_info": "opaque"})
    text = "服务器响应: 中文档案\n下一行😀\n"
    media = await client.upload_txt("group/fixture", text)
    assert media.file_info == "opaque"
    client.request.assert_awaited_once_with(
        "POST",
        "/v2/groups/group%2Ffixture/files",
        {
            "file_type": 4,
            "file_name": "rcon-result.txt",
            "file_data": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "srv_send_msg": False,
        },
    )
    payload = client.request.await_args.args[2]
    assert base64.b64decode(payload["file_data"], validate=True).decode("utf-8") == text
    assert "url" not in payload and "upload_id" not in payload


@pytest.mark.parametrize(
    "response", [{}, {"file_info": ""}, {"file_info": None}, {"file_info": 42}]
)
async def test_txt_upload_rejects_missing_or_invalid_file_info(response):
    client = api()
    client.request = AsyncMock(return_value=response)
    with pytest.raises(QQAPIError) as error:
        await client.upload_txt("group", "fixture-result")
    assert error.value.code == "missing_file_info"
    assert "fixture-result" not in str(error.value)
    client.request.assert_awaited_once()


@pytest.mark.parametrize("text", ["", "中" * (1024 * 1024 // 3 + 1)], ids=["empty", "over-limit"])
async def test_txt_upload_bounds_raw_utf8_before_requesting(text):
    client = api()
    client.request = AsyncMock()
    with pytest.raises(ValueError):
        await client.upload_txt("group", text)
    client.request.assert_not_awaited()

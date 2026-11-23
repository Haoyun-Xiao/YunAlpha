import httpx2
from deepagents.middleware.summarization import compute_summarization_defaults
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI


def streaming_response(text: str) -> httpx2.Response:
    body = (
        'data: {"id":"chatcmpl-test","object":"chat.completion.chunk",'
        '"created":1,"model":"deepseek-v4-flash","choices":[{"index":0,'
        f'"delta":{{"role":"assistant","content":"{text}"}},"finish_reason":null}}]}}\n\n'
        'data: {"id":"chatcmpl-test","object":"chat.completion.chunk",'
        '"created":1,"model":"deepseek-v4-flash","choices":[{"index":0,'
        '"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    return httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"})


async def test_chat_openai_compatible_layer_retries_429_and_streams():
    attempts = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx2.Response(
                429,
                json={"error": {"message": "rate limited", "type": "rate_limit"}},
                headers={"retry-after": "0"},
            )
        return streaming_response("兼容成功")

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    model = ChatOpenAI(
        model="deepseek-v4-flash",
        base_url="https://compat.test/v1",
        api_key="test-key",
        max_retries=2,
        streaming=True,
        profile={"max_input_tokens": 131072},
        http_async_client=client,
    )

    result = await model.ainvoke([HumanMessage(content="测试")])
    await client.aclose()

    assert result.content == "兼容成功"
    assert attempts == 2


def test_model_profile_drives_compaction_before_context_limit():
    model = ChatOpenAI(
        model="deepseek-v4-flash",
        base_url="https://compat.test/v1",
        api_key="test-key",
        profile={"max_input_tokens": 131072},
    )

    defaults = compute_summarization_defaults(model)

    assert defaults["trigger"] == ("fraction", 0.85)
    assert defaults["keep"] == ("fraction", 0.10)

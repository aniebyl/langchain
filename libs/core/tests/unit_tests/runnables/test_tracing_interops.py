import json
import sys
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from langsmith import Client, traceable
from langsmith.run_helpers import tracing_context

from langchain_core.runnables.base import RunnableLambda
from langchain_core.runnables.config import RunnableConfig
from langchain_core.tracers.langchain import LangChainTracer


def _get_posts(client: Client) -> list:
    mock_calls = client.session.request.mock_calls  # type: ignore
    posts = []
    for call in mock_calls:
        if call.args:
            if call.args[0] != "POST":
                continue
            assert call.args[0] == "POST"
            assert call.args[1].startswith("https://api.smith.langchain.com")
            body = json.loads(call.kwargs["data"])
            if "post" in body:
                # Batch request
                assert body["post"]
                posts.extend(body["post"])
            else:
                posts.append(body)
    return posts


def test_config_traceable_handoff() -> None:
    mock_session = MagicMock()
    mock_client_ = Client(
        session=mock_session, api_key="test", auto_batch_tracing=False
    )
    tracer = LangChainTracer(client=mock_client_)

    @traceable
    def my_great_great_grandchild_function(a: int) -> int:
        return a + 1

    @RunnableLambda
    def my_great_grandchild_function(a: int) -> int:
        return my_great_great_grandchild_function(a)

    @RunnableLambda
    def my_grandchild_function(a: int) -> int:
        return my_great_grandchild_function.invoke(a)

    @traceable
    def my_child_function(a: int) -> int:
        return my_grandchild_function.invoke(a) * 3

    @traceable()
    def my_function(a: int) -> int:
        return my_child_function(a)

    def my_parent_function(a: int) -> int:
        return my_function(a)

    my_parent_runnable = RunnableLambda(my_parent_function)

    assert my_parent_runnable.invoke(1, {"callbacks": [tracer]}) == 6
    posts = _get_posts(mock_client_)
    # There should have been 6 runs created,
    # one for each function invocation
    assert len(posts) == 6
    name_to_body = {post["name"]: post for post in posts}
    ordered_names = [
        "my_parent_function",
        "my_function",
        "my_child_function",
        "my_grandchild_function",
        "my_great_grandchild_function",
        "my_great_great_grandchild_function",
    ]
    trace_id = posts[0]["trace_id"]
    last_dotted_order = None
    parent_run_id = None
    for name in ordered_names:
        id_ = name_to_body[name]["id"]
        parent_run_id_ = name_to_body[name]["parent_run_id"]
        if parent_run_id_ is not None:
            assert parent_run_id == parent_run_id_
        assert name in name_to_body
        # All within the same trace
        assert name_to_body[name]["trace_id"] == trace_id
        dotted_order: str = name_to_body[name]["dotted_order"]
        assert dotted_order is not None
        if last_dotted_order is not None:
            assert dotted_order > last_dotted_order
            assert dotted_order.startswith(last_dotted_order), (
                "Unexpected dotted order for run"
                f" {name}\n{dotted_order}\n{last_dotted_order}"
            )
        last_dotted_order = dotted_order
        parent_run_id = id_


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Asyncio context vars require Python 3.11+"
)
async def test_config_traceable_async_handoff() -> None:
    mock_session = MagicMock()
    mock_client_ = Client(
        session=mock_session, api_key="test", auto_batch_tracing=False
    )
    tracer = LangChainTracer(client=mock_client_)

    @traceable
    def my_great_great_grandchild_function(a: int) -> int:
        return a + 1

    @RunnableLambda
    def my_great_grandchild_function(a: int) -> int:
        return my_great_great_grandchild_function(a)

    @RunnableLambda  # type: ignore
    async def my_grandchild_function(a: int) -> int:
        return my_great_grandchild_function.invoke(a)

    @traceable
    async def my_child_function(a: int) -> int:
        return await my_grandchild_function.ainvoke(a) * 3  # type: ignore

    @traceable()
    async def my_function(a: int) -> int:
        return await my_child_function(a)

    async def my_parent_function(a: int) -> int:
        return await my_function(a)

    my_parent_runnable = RunnableLambda(my_parent_function)  # type: ignore
    result = await my_parent_runnable.ainvoke(1, {"callbacks": [tracer]})
    assert result == 6
    posts = _get_posts(mock_client_)
    # There should have been 6 runs created,
    # one for each function invocation
    assert len(posts) == 6
    name_to_body = {post["name"]: post for post in posts}
    ordered_names = [
        "my_parent_function",
        "my_function",
        "my_child_function",
        "my_grandchild_function",
        "my_great_grandchild_function",
        "my_great_great_grandchild_function",
    ]
    trace_id = posts[0]["trace_id"]
    last_dotted_order = None
    parent_run_id = None
    for name in ordered_names:
        id_ = name_to_body[name]["id"]
        parent_run_id_ = name_to_body[name]["parent_run_id"]
        if parent_run_id_ is not None:
            assert parent_run_id == parent_run_id_
        assert name in name_to_body
        # All within the same trace
        assert name_to_body[name]["trace_id"] == trace_id
        dotted_order: str = name_to_body[name]["dotted_order"]
        assert dotted_order is not None
        if last_dotted_order is not None:
            assert dotted_order > last_dotted_order
            assert dotted_order.startswith(last_dotted_order), (
                "Unexpected dotted order for run"
                f" {name}\n{dotted_order}\n{last_dotted_order}"
            )
        last_dotted_order = dotted_order
        parent_run_id = id_


@patch("langchain_core.tracers.langchain.get_client")
@pytest.mark.parametrize("enabled", [None, True, False])
@pytest.mark.parametrize("env", ["", "true"])
def test_tracing_enable_disable(
    mock_get_client: MagicMock, enabled: bool, env: str
) -> None:
    mock_session = MagicMock()
    mock_client_ = Client(
        session=mock_session, api_key="test", auto_batch_tracing=False
    )
    mock_get_client.return_value = mock_client_

    def my_func(a: int) -> int:
        return a + 1

    env_on = env == "true"
    with patch.dict("os.environ", {"LANGSMITH_TRACING": env}):
        with tracing_context(enabled=enabled):
            RunnableLambda(my_func).invoke(1)

    mock_posts = _get_posts(mock_client_)
    if enabled is True:
        assert len(mock_posts) == 1
    elif enabled is False:
        assert not mock_posts
    elif env_on:
        assert len(mock_posts) == 1
    else:
        assert not mock_posts


@pytest.mark.parametrize(
    "method", ["invoke", "ainvoke", "stream", "astream", "batch", "abatch"]
)
async def test_runnable_with_fallbacks_trace_nesting(method: str) -> None:
    if method.startswith("a") and sys.version_info < (3, 11):
        pytest.skip("Asyncio context vars require Python 3.11+")
    mock_session = MagicMock()
    mock_client_ = Client(
        session=mock_session, api_key="test", auto_batch_tracing=False
    )
    tracer = LangChainTracer(client=mock_client_)

    @RunnableLambda
    def my_child_function(a: int) -> int:
        return a + 2

    chain = my_child_function.with_config(tags=["atag"])

    def before(x: int) -> int:
        return x

    def after(x: int) -> int:
        return x

    sequence = before | chain | after
    if method.startswith("a"):

        @RunnableLambda  # type: ignore
        async def parent(a: int, *, config: Optional[RunnableConfig] = None) -> int:
            return await sequence.ainvoke(a, config)

    else:

        @RunnableLambda
        def parent(a: int, *, config: Optional[RunnableConfig] = None) -> int:
            return sequence.invoke(a, config)

    # Now run the chain and check the resulting posts
    cb = [tracer, LangChainTracer()]
    match method:
        case "invoke":
            res: Any = parent.invoke(1, {"callbacks": cb})  # type: ignore
        case "ainvoke":
            res = await parent.ainvoke(1, {"callbacks": cb})  # type: ignore
        case "stream":
            results = list(parent.stream(1, {"callbacks": cb}))  # type: ignore
            res = results[-1]
        case "astream":
            results = [res async for res in parent.astream(1, {"callbacks": cb})]  # type: ignore
            res = results[-1]
        case "batch":
            res = parent.batch([1], {"callbacks": cb})[0]  # type: ignore
        case "abatch":
            res = (await parent.abatch([1], {"callbacks": cb}))[0]  # type: ignore
    assert res == 3
    posts = _get_posts(mock_client_)
    name_order = [
        "parent",
        "RunnableSequence",
        "before",
        "my_child_function",
        "after",
    ]
    assert len(posts) == len(name_order)
    prev_dotted_order = None
    dotted_order_map = {}
    id_map = {}
    parent_id_map = {}
    for i, name in enumerate(name_order):
        assert posts[i]["name"] == name
        dotted_order = posts[i]["dotted_order"]
        if prev_dotted_order is not None:
            assert (
                dotted_order > prev_dotted_order
            ), f"{name} not after {name_order[i-1]}"
        prev_dotted_order = dotted_order
        if name in dotted_order_map:
            raise ValueError(f"Duplicate name {name}")
        dotted_order_map[name] = dotted_order
        id_map[name] = posts[i]["id"]
        parent_id_map[name] = posts[i].get("parent_run_id")
    expected_parents = {
        "parent": None,
        "RunnableSequence": "parent",
        "before": "RunnableSequence",
        "my_child_function": "RunnableSequence",
        "after": "RunnableSequence",
    }

    # Now check the dotted orders
    for name, parent_ in expected_parents.items():
        dotted_order = dotted_order_map[name]
        if parent_ is not None:
            parent_dotted_order = dotted_order_map[parent_]
            assert dotted_order.startswith(
                parent_dotted_order
            ), f"{name}, {parent_dotted_order} not in {dotted_order}"
            assert str(parent_id_map[name]) == str(id_map[parent_])
        else:
            assert dotted_order.split(".")[0] == dotted_order

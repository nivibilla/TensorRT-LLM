import argparse
import asyncio
import json
from typing import AsyncGenerator
import time

from tensorrt_llm.hlapi.tokenizer import TransformersTokenizer

import uvicorn
from tensorrt_llm.executor import GenerationExecutor
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

TIMEOUT_KEEP_ALIVE = 5  # seconds.
TIMEOUT_TO_PREVENT_DEADLOCK = 1  # seconds.
app = FastAPI()
executor: GenerationExecutor | None = None


@app.get("/stats")
async def stats() -> Response:
    assert executor is not None
    return JSONResponse(json.loads(await executor.aget_stats()))


@app.get("/health")
async def health() -> Response:
    """Health check."""
    return Response(status_code=200)


@app.post("/generate")
async def generate(request: Request) -> Response:
    assert executor is not None
    """Generate completion for the request.

    The request should be a JSON object with the following fields:
    - prompt: the prompt to use for the generation.
    - stream: whether to stream the results or not.
    - other fields: the sampling parameters (See `SamplingParams` for details).
    """
    request_dict = await request.json()

    streaming = request_dict.pop("streaming", False)
    promise = executor.generate_async(prompt=request_dict['prompt'],
                                      max_new_tokens=request_dict['max_new_tokens'],
                                      streaming=streaming)

    async def stream_results() -> AsyncGenerator[bytes, None]:
        async for output in promise:
            yield (json.dumps(output.text) + "\0").encode("utf-8")

    if streaming:
        return StreamingResponse(stream_results())

    # Non-streaming case
    await promise.await_completion()
    return JSONResponse({"text": promise.text})


@app.get("/v1/models")
async def show_available_models():
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": "default",
                    "object": "model",
                    "created": 1686935002,
                    "owned_by": "TensorRT-LLM",
                }
            ],
            "object": "list",
        }
    )

@app.post("/v1/chat/completions")
async def chat_completion(request: Request) -> Response:
    assert executor is not None
    request_dict = await request.json()

    streaming = request_dict.pop("streaming", False)
    if streaming:
        return JSONResponse({'error' : "streaming is not yet supported!"})
    
    chat_prompt = "<s> "

    for message in request_dict['messages']:
        if message['role'] == 'user':
            chat_prompt += f"[INST] {message['content']} [/INST] "
        if message['role'] == 'assistant':
            chat_prompt += f"{message['content']} </s> "

    # llama/mistral prompt format
    # <s> [INST] Instruction [/INST] Model answer</s> [INST] Follow-up instruction [/INST]

    promise = executor.generate_async(prompt=chat_prompt,
                                      max_new_tokens=request_dict['max_tokens'],
                                      streaming=streaming)
    
    # TODO: Streaming Case

    # Non-Streaming Case
    await promise.await_completion()
    return JSONResponse({
        "object": "chat.completion",
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {
                    "content": promise.text,
                    "role": "assistant",
                },
            }
        ],
        "id": "xd",
        "created": time.time(),
        "model": "default",
        "usage": {"completion_tokens": 18, "prompt_tokens": 14, "total_tokens": 32},
    })


async def main(args):
    global executor
    

    executor = GenerationExecutor(args.engine_path, TransformersTokenizer.from_pretrained(args.tokenizer_path),
                                  args.max_beam_width)
    config = uvicorn.Config(app,
                            host=args.host,
                            port=args.port,
                            log_level="info",
                            timeout_keep_alive=TIMEOUT_KEEP_ALIVE)
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine_path", type=str)
    parser.add_argument("--tokenizer_path", type=str)
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--max_beam_width", type=int, default=1)
    args = parser.parse_args()

    asyncio.run(main(args))

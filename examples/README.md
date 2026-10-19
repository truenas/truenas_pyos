# Examples

Runnable demonstrations of the `truenas_pyos` APIs. Each example is
self-contained (works in a temporary directory it creates) and needs the
`python3-truenas-pyos` package installed plus a kernel with io_uring
available.

| Example | Demonstrates |
|---|---|
| [`uring_callback_chain.py`](uring_callback_chain.py) | `truenas_os.Uring` completion callbacks driving an op chain (create/open → write → read → print + close) off an asyncio event loop: `loop.add_reader(ring.ringfd(), ring.reap)` is the entire integration. |
| [`uring_asyncio_futures.py`](uring_asyncio_futures.py) | An awaitable futures facade over the same surface: each op resolves an asyncio Future, failures surface as OSError, and ops staged in one event-loop tick submit as a single batch (an `asyncio.gather` of concurrent ops costs one `io_uring_enter`). |

Run one directly:

```bash
python3 examples/uring_callback_chain.py
```

"""
RQ Worker Entrypoint

This module provides the entrypoint for running RQ (Redis Queue) workers.
Run with: python -m stack.queue.rq_worker
"""

import os
import sys

from redis import Redis
from rq import Worker, Queue

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import Config


def get_redis_connection() -> Redis:
    """
    Create a Redis connection from configuration.

    Returns:
        Redis connection instance
    """
    return Redis.from_url(Config.REDIS_URL)


def run_worker(queues: list[str] | None = None) -> None:
    """
    Run the RQ worker.

    Args:
        queues: List of queue names to listen on (default: ["default"])
    """
    if queues is None:
        queues = ["default", "emails", "outbox"]

    redis_conn = get_redis_connection()

    # RQ 2.x: Pass connection to Queue and Worker directly (Connection removed)
    queue_instances = [Queue(name, connection=redis_conn) for name in queues]
    worker = Worker(queue_instances, connection=redis_conn)
    print(f"Starting worker listening on queues: {', '.join(queues)}")
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    # Allow specifying queues via command line
    queues = sys.argv[1:] if len(sys.argv) > 1 else None
    run_worker(queues)

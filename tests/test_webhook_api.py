"""Check API endpoints."""
import datetime
from queue import Queue

import pytest
import requests

from tradeexecutor.cli.log import setup_in_memory_logging, get_ring_buffer_handler
from tradeexecutor.state.metadata import Metadata
from tradeexecutor.state.state import State
from tradeexecutor.state.portfolio import Portfolio
from tradeexecutor.state.store import JSONFileStore
from tradeexecutor.strategy.execution_state import ExecutionState
from tradeexecutor.webhook.server import create_webhook_server


@pytest.fixture()
def store() -> JSONFileStore:
    """Dummy state and store for the tests."""
    portfolio = Portfolio()
    state = State(portfolio=portfolio)
    store = JSONFileStore("/tmp/webhook-test.json")
    store.sync(state)
    return store


@pytest.fixture()
def server_url(store):
    execution_state = ExecutionState()
    execution_state.source_code = "Foobar"

    queue = Queue()
    metadata = Metadata("Foobar", "Short desc", "Long desc", None, datetime.datetime.utcnow(), True)
    server = create_webhook_server("127.0.0.1", 5000, "test", "test", queue, store, metadata, execution_state)
    server_url = "http://test:test@127.0.0.1:5000"
    yield server_url
    server.shutdown()


def test_home(logger, server_url):
    """Homepage renders plain text"""
    resp = requests.get(server_url)
    assert resp.status_code == 200
    # Chuck the Trade Executor server, version 0.1.0, our URL is http://127.0.0.1:5000
    assert resp.headers["content-type"] == "text/plain; charset=UTF-8"
    assert "Chuck the Trade Executor server" in resp.text


def test_ping(logger,server_url):
    """Get pong for ping"""
    resp = requests.get(f"{server_url}/ping")
    assert resp.status_code == 200
    assert resp.json() == {"ping": "pong"}


def test_metadata(logger, server_url):
    """Get executor metadata"""
    resp = requests.get(f"{server_url}/metadata")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Foobar"
    assert data["short_description"] == "Short desc"
    assert data["icon_url"] == None
    assert data["executor_running"] == True


def test_cors(logger, server_url):
    """Cors headers are in place."""
    resp = requests.get(f"{server_url}/ping")
    assert "Access-Control-Allow-Origin" in resp.headers


def test_state(logger, server_url):
    """Download an empty state."""

    # Create a state.
    resp = requests.get(f"{server_url}/state")
    assert resp.status_code == 200

    # Test deserialisation
    state_dict = resp.json()
    state = State.from_dict(state_dict)

    assert state.portfolio.next_trade_id == 1
    assert state.portfolio.next_position_id == 1


def test_logs(logger, server_url):
    """Download logs."""

    setup_in_memory_logging(logger)
    ring_buffer_handler = get_ring_buffer_handler()
    assert ring_buffer_handler is not None

    logger.error("Test message")

    # Create a state.
    resp = requests.get(f"{server_url}/logs")
    assert resp.status_code == 200

    log_message_list = resp.json()
    assert len(log_message_list) > 0


def test_source(logger, server_url):
    """Download sourec code."""
    resp = requests.get(f"{server_url}/source")
    assert resp.status_code == 200
    assert resp.text == "Foobar"
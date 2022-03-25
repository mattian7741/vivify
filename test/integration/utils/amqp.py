from __future__ import annotations

import contextlib
import inspect
import json
import pathlib
import socket
from functools import wraps
from test.integration.utils import FunctionComponent, retries
from typing import Callable, Dict, List, Optional, Iterable, cast
import kombu
import kombu.simple
import kombu.pools
from amqp import Channel

import pika
import pika.exceptions
import pytest
from pika.adapters.blocking_connection import BlockingChannel

from ergo.topic import SubTopic

try:
    from collections.abc import Generator
except ImportError:
    from typing import Generator

from collections import defaultdict

from ergo.topic import PubTopic
from ergo.message import Message, decodes

AMQP_HOST = "amqp://guest:guest@localhost:5672/%2F"
CONNECTION = kombu.Connection(AMQP_HOST)
EXCHANGE = "amq.topic"  # use a pre-declared exchange that we kind bind to while the ergo runtime is booting
SHORT_TIMEOUT = 0.01
LONG_TIMEOUT = 5
_LIVE_INSTANCES: Dict = defaultdict(int)


class AMQPComponent(FunctionComponent):
    protocol = "amqp"
    instances: List[AMQPComponent] = []

    def __init__(
        self,
        func: Callable,
        subtopic: Optional[str] = None,
        pubtopic: Optional[str] = None,
        **manifest
    ):
        super().__init__(func, **manifest)
        self.queue_name = f"{self.handler_path.replace('/', ':')[1:]}:{self.handler_name}"
        self.error_queue_name = f"{self.queue_name}:error"
        handler_module = pathlib.Path(self.handler_path).with_suffix("").name
        self.subtopic = subtopic or f"{handler_module}_{self.handler_name}_sub"
        self.pubtopic = pubtopic or f"{handler_module}_{self.handler_name}_pub"

    @property
    def namespace(self):
        ns = {
            "protocol": "amqp",
            "host": AMQP_HOST,
            "exchange": EXCHANGE,
            "subtopic": self.subtopic,
        }
        if self.pubtopic:
            ns["pubtopic"] = self.pubtopic
        return ns

    def rpc(self, payload: dict, inactivity_timeout=LONG_TIMEOUT):
        self.send(payload)
        return self.consume(inactivity_timeout=inactivity_timeout)

    def send(self, payload: dict):
        # publish_pika(self.subtopic, **message, channel=self.channel)
        publish(payload, self.subtopic)

    def consume(self, inactivity_timeout=LONG_TIMEOUT):
        return self._subscription.get(timeout=inactivity_timeout)

        attempt = 0
        while True:
            value = consume(
                self._subscription_queue,
                channel=self.channel,
                inactivity_timeout=SHORT_TIMEOUT,
            )
            if value:
                return value
            self.propagate_error(inactivity_timeout=SHORT_TIMEOUT)
            attempt += 1
            if inactivity_timeout and attempt >= inactivity_timeout * 20:
                return None

    def propagate_error(self, inactivity_timeout=None):
        pass
        # body = consume(self.error_queue_name, channel=self.error_consumer_channel, inactivity_timeout=inactivity_timeout)
        # if body:
        #     raise ComponentFailure(body["traceback"])

    def setup_component(self):
        self.channel.queue_declare(self.queue_name)
        self.channel.queue_bind(self.queue_name, EXCHANGE, str(SubTopic(self.subtopic)))
        purge_queue(self.queue_name)
        self.channel.queue_declare(self.error_queue_name)
        purge_queue(self.error_queue_name)


    def setup_instance(self):
        self._subscription_queue = new_queue(self.pubtopic, channel=self.channel)
        return self

    def teardown_component(self):
        try:
            channel = new_channel()
            channel.queue_delete(self.queue_name)
            channel.queue_delete(self.error_queue_name)
        except pika.exceptions.ChannelClosedByBroker:
            pass

    def __call__(self, test):
        params = inspect.signature(test).parameters

        if "component" in params:

            @wraps(test)
            @pytest.mark.parametrize("component", [self])
            def test_with_component(*args, component=None, **kwargs):
                with self:
                    return test(*args, component=component, **kwargs)

            return test_with_component
        if "components" in params:

            @wraps(test)
            @pytest.mark.parametrize("components", [AMQPComponent.instances])
            def test_with_component(*args, components=None, **kwargs):
                with self:
                    return test(*args, components=components, **kwargs)

            return test_with_component
        return test

    def __enter__(self):
        self.channel = new_channel()
        self.error_consumer_channel = new_channel()
        self.instances.append(self)
        super().__enter__()
        if not _LIVE_INSTANCES[self.func]:
            self.teardown_component()
            self.setup_component()
            self._subscription = KombuQueue(self.pubtopic)
            self._subscription.__enter__()
        _LIVE_INSTANCES[self.func] += 1
        self.setup_instance()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.instances.pop()
        _LIVE_INSTANCES[self.func] -= 1
        if not _LIVE_INSTANCES[self.func]:
            self.teardown_component()
            self._subscription.__exit__()
        super().__exit__(exc_type, exc_val, exc_tb)


amqp_component = AMQPComponent


class Queue:
    def __init__(self, routing_key: str):
        self.channel = new_channel()
        self.queue = self.channel.queue_declare(
            queue="",
            exclusive=True,
        ).method.queue
        self.bind(routing_key)

    def bind(self, routing_key: str, exchange=None):
        self.channel.queue_bind(
            self.queue, exchange or EXCHANGE, routing_key=str(SubTopic(routing_key))
        )

    def consume(self, inactivity_timeout=LONG_TIMEOUT):
        method, _, body = next(
            self.channel.consume(self.queue, inactivity_timeout=inactivity_timeout)
        )
        if body:
            self.channel.basic_ack(method.delivery_tag)
            return json.loads(body)


def publish_pika(routing_key, channel=None, **message):
    channel = channel or new_channel()
    channel.confirm_delivery()
    for retry in retries(200, SHORT_TIMEOUT, pika.exceptions.UnroutableError):
        with retry():
            body = json.dumps(message).encode()
            channel.basic_publish(
                exchange=EXCHANGE,
                routing_key=str(PubTopic(routing_key)),
                body=body,
                mandatory=True,
            )


def consume(queue_name, inactivity_timeout=None, channel=None):
    channel = channel or new_channel()
    method, _, body = next(
        channel.consume(queue_name, inactivity_timeout=inactivity_timeout)
    )
    if body:
        channel.basic_ack(method.delivery_tag)
        return json.loads(body)
    return None


def new_queue(routing_key, channel=None):
    channel = channel or new_channel()
    queue = channel.queue_declare(
        queue="",
        exclusive=True,
    ).method.queue
    channel.queue_bind(queue, EXCHANGE, routing_key=str(SubTopic(routing_key)))
    return queue


def purge_queue(queue_name: str, channel=None):
    channel = channel or new_channel()
    try:
        channel.queue_purge(queue_name)
    except pika.exceptions.ChannelClosedByBroker as exc:
        if "no queue" not in str(exc):
            raise


def new_channel() -> BlockingChannel:
    connection = get_connection()
    return connection.channel()


def get_connection() -> pika.BlockingConnection:
    return pika.BlockingConnection(pika.URLParameters(AMQP_HOST))


class ComponentFailure(Exception):
    pass


def publish(payload: dict, routing_key: str):
    with kombu.pools.producers[CONNECTION].acquire() as producer:
        producer = cast(kombu.Producer, producer)
        producer.publish(json.dumps(payload), routing_key=str(PubTopic(routing_key)), exchange=EXCHANGE, serializer="raw")


class PubSub:
    def __init__(self, name: str, pubtopic: str, *subtopics: str):
        self.name = name
        self.pubtopic = pubtopic
        self.subtopics = subtopics

    def put(self, data: dict):
        self._pub_queue.put(data)

    def get(self, block=True, timeout=None):
        self._sub_queue.get(block=block, timeout=timeout)

    def __enter__(self):
        self._pub_queue = KombuQueue(self.pubtopic)
        self._pub_queue.__enter__()
        self._sub_queue = KombuQueue(*self.subtopics)
        self._sub_queue.__enter__()

    def __exit__(self, *exc_info):
        self._pub_queue.__exit__()
        self._sub_queue.__exit__()


class KombuQueue:
    def __init__(self, routing_key):
        self.name = f"test_queue:{routing_key}"
        self.routing_key = routing_key

    def put(self, data: dict):
        self._queue.put(json.dumps(data))

    def get(self, block=True, timeout=None) -> Message:
        amqp_message = self._queue.get(block=block, timeout=timeout)
        return decodes(amqp_message.body)

    def __enter__(self):
        self._channel: Channel = CONNECTION.channel()
        print(self.routing_key)
        print(str(PubTopic(self.routing_key)))
        queue = kombu.Queue(self.name, exchange=EXCHANGE, routing_key=str(PubTopic(self.routing_key)), auto_delete=True, no_ack=True)
        self._queue = kombu.simple.SimpleQueue(self._channel, queue, serializer="raw")
        return self

    def __exit__(self, *exc_info):
        self._channel.__exit__()


class propagate_errors:
    def __init__(self):
        self._queue = kombu.Queue("test_error_queue", exchange=EXCHANGE, routing_key="#", auto_delete=True, no_ack=True)

    def __enter__(self):
        self._channel: Channel = CONNECTION.channel()
        self._consumer = kombu.Consumer(self._channel, queues=[self._queue], callbacks=[self._handle_message])
        self._consumer.consume()
        return self

    def __exit__(self, *exc_info):
        self._consumer.close()
        self._channel.close()

    @staticmethod
    def _handle_message(body: str, _):
        ergo_msg = decodes(body)
        if ergo_msg.error:
            raise ComponentFailure(ergo_msg.traceback)



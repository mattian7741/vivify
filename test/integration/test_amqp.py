import timeout_decorator
import pika
import json
import docker
import time
from pika import URLParameters
from src.topic import PubTopic
from src.amqp_invoker import declare_topic_exchange
from test.integration.scaffold import ErgoStartTest


AMQP_HOST = "amqp://guest:guest@localhost:5672/%2F"


class ErgoStartAMQPTest(ErgoStartTest):
    @classmethod
    def setUpClass(cls) -> None:
        start_rabbitmq()
        super().setUpClass()


def start_rabbitmq():
    docker_client = docker.from_env()
    try:
        container, = docker_client.containers.list(filters={"name": "rabbitmq"})
    except ValueError:
        container = docker_client.containers.run(
            name="rabbitmq",
            image="rabbitmq:3.8.16-management-alpine",
            ports={5672: 5672},
            detach=True,
        )

    print("awaiting broker")
    output = ""
    for retry in range(200):
        if container.status in ["created", "running"]:
            exit_code, output = container.exec_run(["rabbitmqctl", "await_online_nodes", "1"])
            if exit_code == 0:
                break
        time.sleep(.5)
    else:
        raise RuntimeError(output)
    print("broker started")


class TestStartProductAMQP(ErgoStartTest):
    manifest = "configs/product.yml"
    namespace = "configs/amqp.yml"

    @timeout_decorator.timeout(seconds=2)
    def runTest(self):
        connection = pika.BlockingConnection(URLParameters(AMQP_HOST))
        channel = connection.channel()
        declare_topic_exchange(channel, "primary")

        def on_pubtopic_message(body):
            result = body["data"]
            self.assertEqual(20.0, result)

        def on_error_mesage(body):
            error = body["error"]
            raise Exception(error)

        def add_consumer(queue_name, consumer):
            channel.queue_declare(queue=queue_name)
            channel.queue_bind(exchange="primary", queue=queue_name)
            channel.queue_purge(queue_name)

            def on_message_callback(chan, method, properties, body):
                channel.stop_consuming()
                body = json.loads(body)
                return consumer(body)

            channel.basic_consume(queue=queue_name, on_message_callback=on_message_callback)

        add_consumer("product_out", on_pubtopic_message)
        add_consumer("entrypoints.py:product_error", on_error_mesage)

        channel.confirm_delivery()
        err = None
        for retry in range(5):
            try:
                data = {"data": '{"x": 4, "y": 5}'}
                routing_key = str(PubTopic("product_in"))
                channel.basic_publish(exchange="primary", routing_key=routing_key,
                                      body=json.dumps(data), mandatory=True)  # noqa
                break
            except pika.exceptions.UnroutableError as err:
                import time
                time.sleep(.5)
        else:
            raise err

        channel.start_consuming()
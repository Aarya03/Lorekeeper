"""Small Kafka-compatible durable event log using aiokafka."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
from prometheus_client import Counter

dead_letters=Counter("lorekeeper_dead_letter_events_total","Events moved to the projection dead-letter topic")


class EventBus:
    def __init__(self, bootstrap_servers: str, handler, topic: str = "events.raw"):
        self.bootstrap_servers=bootstrap_servers
        self.handler=handler
        self.topic=topic
        self.loop=None
        self.thread=None
        self.producer=None
        self.consumer=None
        self.ready=threading.Event()
        self.failure=None
        self._closing=False

    @property
    def healthy(self):
        return bool(self.loop and self.loop.is_running() and self.producer and self.consumer and hasattr(self,"consume_task") and not self.consume_task.done())

    def start(self, timeout=30):
        self.thread=threading.Thread(target=self._run,daemon=True,name="lorekeeper-kafka")
        self.thread.start()
        if not self.ready.wait(timeout): raise TimeoutError("Kafka event log did not start before timeout")
        if self.failure: raise RuntimeError("Kafka event log failed to start") from self.failure

    def _run(self):
        self.loop=asyncio.new_event_loop(); asyncio.set_event_loop(self.loop)
        try:
            from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
            self.loop.run_until_complete(self._initialize(AIOKafkaProducer,AIOKafkaConsumer))
            self.ready.set()
            self.loop.run_forever()
        except Exception as exc:
            self.failure=exc; self.ready.set()
        finally:
            if self.loop and not self.loop.is_closed(): self.loop.close()

    async def _initialize(self, producer_cls, consumer_cls):
        from aiokafka.admin import AIOKafkaAdminClient, NewTopic
        from aiokafka.errors import TopicAlreadyExistsError
        admin=AIOKafkaAdminClient(bootstrap_servers=self.bootstrap_servers)
        await admin.start()
        try:
            await admin.create_topics([NewTopic(self.topic,num_partitions=3,replication_factor=1,topic_configs={"retention.ms":"604800000"}),NewTopic("dlq.events",num_partitions=1,replication_factor=1,topic_configs={"retention.ms":"2592000000"})])
        except TopicAlreadyExistsError:
            pass
        finally:
            await admin.close()
        self.producer=producer_cls(bootstrap_servers=self.bootstrap_servers,acks="all",enable_idempotence=True,compression_type="gzip",linger_ms=10)
        self.consumer=consumer_cls(self.topic,bootstrap_servers=self.bootstrap_servers,group_id="lorekeeper-projections-v1",enable_auto_commit=False,auto_offset_reset="earliest",isolation_level="read_committed")
        await self.producer.start()
        await self.consumer.start()
        self.consume_task=asyncio.create_task(self._consume())

    async def _consume(self):
        async for record in self.consumer:
            event=json.loads(record.value)
            for attempt in range(3):
                try:
                    # Projection transaction completes before its Kafka offset is committed.
                    await asyncio.to_thread(self.handler,event)
                    await self.consumer.commit()
                    break
                except Exception as exc:
                    if attempt<2:
                        await asyncio.sleep(0.25*(attempt+1))
                        continue
                    print(f"Event projection moved to DLQ at {record.topic}:{record.partition}:{record.offset}: {exc}",flush=True)
                    dlq={"failed_record":{"topic":record.topic,"partition":record.partition,"offset":record.offset},"event":event,"error":str(exc)[:1000],"failed_at":time.time()}
                    await self.producer.send_and_wait("dlq.events",json.dumps(dlq,ensure_ascii=False).encode(),key=str(event.get("channel_id","default")).encode())
                    dead_letters.inc()
                    await self.consumer.commit()

    async def _publish(self,event):
        await self.producer.send_and_wait(self.topic,json.dumps(event,ensure_ascii=False).encode(),key=str(event.get("channel_id","default")).encode())

    def publish(self,event,timeout=10):
        if not self.loop or not self.producer: raise RuntimeError("Kafka event log is unavailable")
        future=asyncio.run_coroutine_threadsafe(self._publish(event),self.loop)
        try: future.result(timeout=timeout)
        except FutureTimeout as exc: future.cancel(); raise TimeoutError("Kafka event append timed out") from exc

    async def _shutdown(self):
        self._closing=True
        if hasattr(self,"consume_task"):
            self.consume_task.cancel()
            try: await self.consume_task
            except asyncio.CancelledError: pass
        if self.consumer: await self.consumer.stop()
        if self.producer: await self.producer.stop()

    def stop(self):
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(),self.loop).result(timeout=10)
            self.loop.call_soon_threadsafe(self.loop.stop)
            if self.thread: self.thread.join(timeout=10)

"""EventBus tests — Phase 2's WebSocket broadcaster is built directly on this."""

from __future__ import annotations

import asyncio

import pytest

from app.events import EventBus, EventType


@pytest.fixture
def bus() -> EventBus:
    return EventBus("game-1")


class TestPublishing:
    def test_seq_starts_at_one_and_is_gap_free(self, bus):
        for _ in range(5):
            bus.publish(EventType.COMMENTARY, {"text": "hi"})
        assert [e.seq for e in bus.history] == [1, 2, 3, 4, 5]

    def test_event_carries_game_id_type_and_payload(self, bus):
        event = bus.publish(EventType.MOVE_MADE, {"san": "e4"})

        assert event.game_id == "game-1"
        assert event.type == EventType.MOVE_MADE
        assert event.data == {"san": "e4"}
        assert event.ts > 0

    def test_publish_without_data_yields_empty_payload(self, bus):
        assert bus.publish(EventType.GAME_STARTED).data == {}

    def test_history_is_a_copy(self, bus):
        bus.publish(EventType.COMMENTARY)
        bus.history.clear()
        assert len(bus.history) == 1


class TestSubscribers:
    async def test_subscriber_receives_published_events(self, bus):
        queue = bus.subscribe()
        bus.publish(EventType.GAME_STARTED, {"a": 1})

        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event.type == EventType.GAME_STARTED
        assert event.data == {"a": 1}

    async def test_every_subscriber_gets_its_own_copy(self, bus):
        first, second = bus.subscribe(), bus.subscribe()
        bus.publish(EventType.MOVE_MADE, {"san": "e4"})

        assert (await first.get()).seq == 1
        assert (await second.get()).seq == 1
        assert bus.subscriber_count == 2

    def test_subscribers_only_get_events_published_after_subscribing(self, bus):
        bus.publish(EventType.GAME_STARTED)
        queue = bus.subscribe()
        bus.publish(EventType.MOVE_MADE)

        # Late joiners replay from history; the queue holds only what came after.
        assert queue.qsize() == 1
        assert len(bus.history) == 2

    def test_unsubscribe_stops_delivery(self, bus):
        queue = bus.subscribe()
        bus.unsubscribe(queue)
        bus.publish(EventType.MOVE_MADE)

        assert queue.empty()
        assert bus.subscriber_count == 0

    def test_unsubscribing_twice_is_harmless(self, bus):
        queue = bus.subscribe()
        bus.unsubscribe(queue)
        bus.unsubscribe(queue)
        assert bus.subscriber_count == 0

    def test_publishing_with_no_subscribers_still_records_history(self, bus):
        bus.publish(EventType.MOVE_MADE)
        assert len(bus.history) == 1


class TestSlowConsumers:
    def test_a_full_queue_is_dropped_rather_than_stalling_the_game(self):
        """A browser tab that stops reading must never block the game loop."""
        bus = EventBus("game-1", queue_maxsize=2)
        queue = bus.subscribe()

        for _ in range(5):
            bus.publish(EventType.MOVE_MADE)

        # The slow subscriber is evicted; it rehydrates over REST on reconnect.
        assert bus.subscriber_count == 0
        assert len(bus.history) == 5, "history must stay complete for rehydration"

    def test_one_slow_consumer_does_not_starve_a_healthy_one(self):
        bus = EventBus("game-1", queue_maxsize=2)
        slow = bus.subscribe()
        healthy = bus.subscribe()

        for _ in range(3):
            bus.publish(EventType.MOVE_MADE)
            while not healthy.empty():
                healthy.get_nowait()

        assert slow not in bus._subscribers
        assert bus.subscriber_count == 1

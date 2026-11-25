"""OpenTelemetry setup with a processor that stores finished spans in the
gateway database, so traces are inspectable from the dashboard and MCP tools
without running a collector. Set OTEL_EXPORTER_OTLP_ENDPOINT and install
opentelemetry-exporter-otlp to also ship them elsewhere."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import sys
from datetime import datetime, timezone

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from companion.models import TraceSpan

_pending: "queue.SimpleQueue[TraceSpan]" = queue.SimpleQueue()


class DatabaseSpanProcessor(SpanProcessor):
    def on_end(self, span: ReadableSpan) -> None:
        # Libraries (FastAPI included) emit their own spans on every request; the
        # trace store keeps only the gateway's spans. OTLP export still gets all.
        if span.instrumentation_scope is None or span.instrumentation_scope.name != "companion":
            return
        ctx = span.get_span_context()
        if ctx is None or span.start_time is None or span.end_time is None:
            return
        attrs = {k: v if isinstance(v, (str, int, float, bool)) else list(v) for k, v in (span.attributes or {}).items()}
        _pending.put(
            TraceSpan(
                trace_id=format(ctx.trace_id, "032x"),
                span_id=format(ctx.span_id, "016x"),
                parent_span_id=format(span.parent.span_id, "016x") if span.parent else None,
                execution_id=attrs.get("companion.execution_id"),
                name=span.name,
                started_at=datetime.fromtimestamp(span.start_time / 1e9, tz=timezone.utc),
                duration_ms=(span.end_time - span.start_time) / 1e6,
                status=span.status.status_code.name.lower(),
                attributes=attrs,
            )
        )


_provider: TracerProvider | None = None


def setup_tracing() -> None:
    global _provider
    if _provider is not None:
        return
    _provider = TracerProvider()
    _provider.add_span_processor(DatabaseSpanProcessor())
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        except ImportError:
            logging.getLogger(__name__).warning("OTLP endpoint set but opentelemetry-exporter-otlp is not installed")
    trace.set_tracer_provider(_provider)


def tracer():
    return trace.get_tracer("companion")


async def flush_spans(sessions: async_sessionmaker[AsyncSession]) -> int:
    batch: list[TraceSpan] = []
    while True:
        try:
            batch.append(_pending.get_nowait())
        except queue.Empty:
            break
    if batch:
        async with sessions() as s:
            s.add_all(batch)
            await s.commit()
    return len(batch)


async def span_flush_loop(sessions: async_sessionmaker[AsyncSession]) -> None:
    while True:
        await asyncio.sleep(0.5)
        try:
            await flush_spans(sessions)
        except Exception:
            logging.getLogger(__name__).exception("failed to store spans")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        span = trace.get_current_span().get_span_context()
        if span and span.is_valid:
            entry["trace_id"] = format(span.trace_id, "032x")
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def setup_logging(level: str = "INFO") -> None:
    # stderr only: the MCP server speaks JSON-RPC on stdout.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

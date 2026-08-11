import { BookOpen, Cpu, Database, Server } from 'lucide-react';

export const CATEGORY_META = {
  'LLM Providers': { Icon: Cpu, tone: 'rust' },
  'Literature Sources': { Icon: BookOpen, tone: 'ink' },
  'Document Processing': { Icon: Server, tone: 'forest' },
  Infrastructure: { Icon: Database, tone: 'forest' },
};

export function formatAgo(iso) {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const sec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (sec < 5) return 'just now';
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  return `${Math.floor(sec / 3600)}h ago`;
}

export function sourceStatusMeta(source) {
  const live = source.live || {};
  const inFlight = Number(live.in_flight || 0);
  const inUse = inFlight > 0 || Boolean(source.in_use);
  const skipped = source.enabled === false;

  let badge = 'operational';
  let label = String(source.status || '').replace(/_/g, ' ');

  if (source.status === 'rate_limited' || source.status === 'degraded') {
    badge = 'warning';
  } else if (source.status === 'offline' || source.status === 'no_key') {
    badge = 'offline';
  }

  if (skipped) {
    badge = 'skipped';
    label = 'skipped';
  } else if (inUse) {
    badge = badge === 'offline' ? 'warning' : badge;
    label = `in use ×${inFlight || 1}`;
  }

  return { live, inFlight, inUse, skipped, badge, label };
}

export function afterText(source) {
  const live = source.live || {};
  const lastAgo = formatAgo(live.last_finished_at);
  if (!live.last_finished_at) return 'No live calls yet this process';
  const outcome = live.last_ok ? 'ok' : 'fail';
  const bits = [outcome];
  if (live.last_latency_ms != null) bits.push(`${live.last_latency_ms} ms`);
  if (live.last_items != null) bits.push(`${live.last_items} items`);
  if (live.last_operation) bits.push(live.last_operation);
  if (lastAgo) bits.push(lastAgo);
  if (!live.last_ok && live.last_error) bits.push(live.last_error);
  return bits.join(' · ');
}

export function isUnhealthy(source) {
  return ['offline', 'no_key', 'degraded', 'rate_limited'].includes(source.status);
}
